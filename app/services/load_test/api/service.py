"""HTTP load-test orchestrator.

Drives N concurrent requests through ``httpx.AsyncClient`` against a
target endpoint, collecting per-request latency, status codes, and
sampled errors. Supports both out-of-process (real server) and
in-process (``httpx.ASGITransport``) modes.

The service is instantiable rather than fully static because it carries
optional dependency state (the ``RedisResultStore``). When a store is
provided, ``run`` automatically persists results; without one, results
are returned but not saved.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import time
from typing import Any
import uuid

from fastapi import FastAPI
import httpx

from app.services.load_test.api.discovery import extract_path_params
from app.services.load_test.api.models import (
    APILoadTestConfiguration,
    APILoadTestMetrics,
    APILoadTestResult,
    ErrorSample,
)
from app.services.load_test.common.storage import RedisResultStore

_MAX_ERROR_SAMPLES = 100
_MAX_ERROR_MESSAGE_LEN = 500


class APILoadTestService:
    def __init__(
        self, store: RedisResultStore[APILoadTestResult] | None = None
    ) -> None:
        self._store = store

    async def run(
        self,
        config: APILoadTestConfiguration,
        app: FastAPI | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> APILoadTestResult:
        if config.in_process and app is None:
            raise ValueError("in_process=True requires an app argument")

        resolved_path = _substitute_path_params(config.path, config.path_params)

        test_id = config.test_id or _generate_test_id()
        start_dt = datetime.now(UTC)
        t0 = time.perf_counter()

        latencies_ms: list[float] = []
        status_codes: dict[int, int] = {}
        errors: list[ErrorSample] = []
        completed = 0
        failed = 0

        client_kwargs: dict[str, Any] = {"timeout": config.timeout_s}
        if config.in_process:
            client_kwargs["transport"] = httpx.ASGITransport(app=app)
            client_kwargs["base_url"] = "http://testserver"
        else:
            client_kwargs["base_url"] = config.base_url
            client_kwargs["limits"] = httpx.Limits(max_connections=config.clients)

        semaphore = asyncio.Semaphore(config.clients)
        total_requests = config.requests
        progress_lock = asyncio.Lock()

        async def _tick() -> None:
            if progress_callback is None:
                return
            # Snapshot under the lock so concurrent callers can't both
            # read a stale ``completed + failed`` and emit
            # out-of-order progress.
            async with progress_lock:
                progress_callback(completed + failed, total_requests)

        async with httpx.AsyncClient(**client_kwargs) as client:

            async def _one(idx: int) -> None:
                nonlocal completed, failed
                async with semaphore:
                    if config.delay_ms > 0:
                        await asyncio.sleep(config.delay_ms / 1000)
                    req_start = time.perf_counter()
                    try:
                        request_kwargs: dict[str, Any] = {
                            "headers": dict(config.headers) if config.headers else None,
                        }
                        if config.payload is not None:
                            if isinstance(config.payload, dict):
                                request_kwargs["json"] = config.payload
                            else:
                                request_kwargs["content"] = config.payload
                        response = await client.request(
                            config.method, resolved_path, **request_kwargs
                        )
                        elapsed_ms = (time.perf_counter() - req_start) * 1000
                        latencies_ms.append(elapsed_ms)
                        status_codes[response.status_code] = (
                            status_codes.get(response.status_code, 0) + 1
                        )
                        if 200 <= response.status_code < 400:
                            completed += 1
                        else:
                            failed += 1
                            if len(errors) < _MAX_ERROR_SAMPLES:
                                errors.append(
                                    ErrorSample(
                                        request_index=idx,
                                        error_type=f"HTTP{response.status_code}",
                                        message=f"status {response.status_code}",
                                    )
                                )
                    except Exception as exc:
                        elapsed_ms = (time.perf_counter() - req_start) * 1000
                        latencies_ms.append(elapsed_ms)
                        failed += 1
                        if len(errors) < _MAX_ERROR_SAMPLES:
                            errors.append(
                                ErrorSample(
                                    request_index=idx,
                                    error_type=type(exc).__name__,
                                    message=str(exc)[:_MAX_ERROR_MESSAGE_LEN],
                                )
                            )
                    await _tick()

            await asyncio.gather(*[_one(i) for i in range(config.requests)])

        duration = time.perf_counter() - t0
        end_dt = datetime.now(UTC)

        p50, p95, p99, p_max = _compute_percentiles(latencies_ms)
        sent = config.requests

        metrics = APILoadTestMetrics(
            tasks_sent=sent,
            tasks_completed=completed,
            tasks_failed=failed,
            total_duration_seconds=duration,
            overall_throughput=(sent / duration) if duration > 0 else 0.0,
            failure_rate_percent=(failed / sent * 100) if sent > 0 else 0.0,
            completion_percentage=(completed / sent * 100) if sent > 0 else 0.0,
            latency_ms_p50=p50,
            latency_ms_p95=p95,
            latency_ms_p99=p99,
            latency_ms_max=p_max,
            status_codes=status_codes,
            errors=errors,
        )

        result = APILoadTestResult(
            status="completed",
            test_id=test_id,
            configuration=config,
            metrics=metrics,
            start_time=start_dt.isoformat(),
            end_time=end_dt.isoformat(),
        )

        if self._store is not None:
            await self._store.save(result)

        return result

    async def get_result(self, test_id: str) -> APILoadTestResult | None:
        if self._store is None:
            return None
        return await self._store.get(test_id)

    async def list_recent(self, limit: int) -> list[APILoadTestResult]:
        if self._store is None:
            return []
        return await self._store.list_recent(limit)


def _generate_test_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"htl_{stamp}_{uuid.uuid4().hex[:6]}"


def _substitute_path_params(path: str, values: dict[str, str]) -> str:
    """Substitute ``{name}`` placeholders in ``path`` using ``values``.

    Raises ``ValueError`` listing every still-unsubstituted placeholder. The
    check happens once, before any requests are issued, so a misconfigured
    run fails fast instead of producing a stream of 404s.
    """
    declared = extract_path_params(path)
    if not declared:
        return path
    missing = [name for name in declared if name not in values]
    if missing:
        raise ValueError(
            f"path {path} has unsubstituted params: {', '.join(missing)}. "
            f"Pass --path-param "
            f"{'='.join([missing[0], '<value>'])}"
            + (f" (and {len(missing) - 1} more)" if len(missing) > 1 else "")
        )
    resolved = path
    for name, value in values.items():
        resolved = resolved.replace("{" + name + "}", str(value))
    return resolved


def _compute_percentiles(
    latencies_ms: list[float],
) -> tuple[float, float, float, float]:
    """Return (p50, p95, p99, max) via linear interpolation between ranks.

    On the empty list, all percentiles are 0.0 — callers should never see
    this in practice (you can't run zero requests), but the zero-default
    keeps the metrics model valid.
    """
    if not latencies_ms:
        return 0.0, 0.0, 0.0, 0.0
    sorted_lat = sorted(latencies_ms)
    n = len(sorted_lat)

    def _pct(p: float) -> float:
        if n == 1:
            return sorted_lat[0]
        idx = (p / 100.0) * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return sorted_lat[lo] + (sorted_lat[hi] - sorted_lat[lo]) * frac

    return _pct(50.0), _pct(95.0), _pct(99.0), sorted_lat[-1]
