"""
Load testing worker tasks for arq.

Thin wrappers around shared workload functions from load_test_workloads service.
"""

from typing import Any

from app.services.load_test_workloads import (
    run_cpu_intensive,
    run_failure_testing,
    run_io_simulation,
    run_memory_operations,
)


async def cpu_intensive_task(ctx: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Stress-test CPU with synthetic computation.

    Runs a configurable burst of mathematical operations (prime sieve,
    matrix multiply) to measure worker throughput under CPU pressure.
    """
    task_id = ctx.get("job_id", "unknown")
    return await run_cpu_intensive(task_id=task_id)


async def io_simulation_task(ctx: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Simulate I/O-bound workloads with async sleep.

    Mimics network calls, file reads, and database queries using
    randomised async delays to test worker concurrency handling.
    """
    task_id = ctx.get("job_id", "unknown")
    return await run_io_simulation(task_id=task_id)


async def memory_operations_task(ctx: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Exercise memory allocation and garbage collection.

    Allocates and releases large byte buffers and data structures to
    test worker memory behaviour under sustained allocation pressure.
    """
    task_id = ctx.get("job_id", "unknown")
    return await run_memory_operations(task_id=task_id)


async def failure_testing_task(ctx: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Randomly raise exceptions for error-handling validation.

    Fails with a configurable probability to verify retry logic,
    dead-letter routing, and failure metric collection.
    """
    task_id = ctx.get("job_id", "unknown")
    return await run_failure_testing(task_id=task_id)
