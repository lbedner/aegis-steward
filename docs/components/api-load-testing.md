# API Load Testing

Built-in API load testing against any FastAPI endpoint your project exposes.
No demo endpoints to wire up, no third-party tool to install: route discovery
reads `app.routes` directly, and the orchestrator drives `httpx.AsyncClient`
with configurable concurrency. Results land in Redis and surface in the
Overseer dashboard.

## Quickstart

```bash
# 1. See what's loadable
my-app api-load-test list

# 2. Hit a simple endpoint
my-app api-load-test run /health/ --requests 500 --clients 10 --in-process

# 3. Pull recent runs (out of Redis)
my-app api-load-test recent
```

`--in-process` runs the test through `httpx.ASGITransport` directly against
the FastAPI app (no port, no network). Drop the flag and pass
`--base-url http://localhost:8000` to hit a real running server.

## Endpoint recipes

The same command shape covers every endpoint your project exposes. Examples
below mirror the rows in `api-load-test list`.

### Plain GET, no parameters

```bash
my-app api-load-test run /health/ --requests 500 --clients 10 --in-process
```

### GET with a path parameter

Routes like `/api/v1/tasks/status/{task_id}` are templates: you must supply
the value with `--path-param`. Forgetting it fails fast with a message naming
the missing keys (no silent 404s).

```bash
my-app api-load-test run /api/v1/tasks/status/{task_id} \
    --path-param task_id=abc-123 \
    --requests 100 --clients 5 --in-process
```

Multiple params? Repeat the flag:

```bash
my-app api-load-test run /api/v1/items/{item_id}/owner/{user_id} \
    --path-param item_id=42 --path-param user_id=u-9 \
    --requests 100 --clients 5 --in-process
```

### POST with a static payload

```bash
my-app api-load-test run /api/v1/tasks/enqueue \
    --method POST \
    --payload '{"queue": "load_test", "task": "ping"}' \
    --requests 200 --clients 10 --in-process
```

Or read the payload from a file:

```bash
my-app api-load-test run /api/v1/items \
    --method POST \
    --payload-file ./fixtures/new_item.json \
    --requests 200 --clients 10
```

### Authenticated endpoints

Today: pass the bearer token in a header.

```bash
TOKEN=$(my-app auth login alice@example.com --print-token)
my-app api-load-test run /api/v1/users/me \
    --header "Authorization=Bearer $TOKEN" \
    --requests 200 --clients 5
```

A first-class `--auth-as <username>` flag that handles the login round-trip
is on the roadmap (folded into the multi-step scenarios work; see
[Limitations](#limitations)).

## What `list` actually shows

```
                           Discovered 21 routes
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┓
┃ METHOD ┃ PATH                              ┃ AUTH ┃ PARAMS  ┃ TAGS       ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━┩
│ GET    │ /health/                          │ no   │         │ health     │
│ GET    │ /api/v1/users/me                  │ yes  │         │ users      │
│ GET    │ /api/v1/tasks/status/{task_id}    │ no   │ task_id │ worker     │
│ POST   │ /api/v1/tasks/enqueue             │ no   │         │ worker     │
└────────┴───────────────────────────────────┴──────┴─────────┴────────────┘
```

- `METHOD` is colored per verb (GET / POST / PUT / PATCH / DELETE map to
  blue / green / yellow / magenta / red, mirroring the dashboard's method
  badges).
- `AUTH` is populated by inspecting each route's FastAPI dependency tree for
  the project's auth callable (`get_current_active_user`). Auth-less stacks
  show `no` everywhere.
- `PARAMS` only appears when at least one discovered route has `{...}`
  placeholders.

## Reading results in Overseer

The backend modal (click the **Backend** card on the dashboard) has a
**Load Tests** tab. Each run is a row; expanding a row shows full latency
percentiles, status-code distribution, and a sample of any errors.

Results persist in Redis at `api_load_test:results:<test_id>` with a
24-hour TTL by default; the recency index is a ZSET at `api_load_test:recent`.

## In-process vs out-of-process

| | `--in-process` (default off) | Out-of-process |
|---|---|---|
| Transport | `httpx.ASGITransport` against the app object | Real HTTP over the network |
| Server needed | No | Yes (`my-app run` first) |
| Realism | Skips uvicorn / keep-alive / etc. | Exercises the whole stack |
| Use for | CI, quick checks, throughput regression tests | Validating production-shape numbers |

Throughput numbers in-process are typically 2-5x higher than out-of-process
because there is no socket / no kernel hop. Don't compare across modes.

## Programmatic use

```python
from app.services.load_test.api.service import APILoadTestService
from app.services.load_test.api.models import APILoadTestConfiguration

service = APILoadTestService()
result = await service.run(
    APILoadTestConfiguration(
        method="GET",
        path="/api/v1/users/{user_id}",
        path_params={"user_id": "u-9"},
        requests=200,
        clients=10,
        in_process=True,
    ),
    app=fastapi_app,
)
print(result.metrics.latency_ms_p95)
```

## Limitations

- **No `--auth-as` yet.** The CLI does not perform the auth round-trip for
  you. Either supply `--header "Authorization=Bearer ..."` or use the auth
  service's CLI to print a token first. Folded into the deferred multi-step
  scenarios work.
- **No streaming-aware mode.** SSE / chunked-transfer endpoints will run, but
  every request is awaited to completion, which inflates latency numbers and
  risks timeouts. A streaming-aware mode is its own future design.
- **HTTP only.** No WebSocket or gRPC targets.
- **Results expire.** 24-hour TTL by default. Run-to-run regression
  comparison is a separate deferred ticket.
