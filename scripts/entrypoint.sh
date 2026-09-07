#!/usr/bin/env bash

set -e

# Configure UV environment based on execution context
if [ -n "$DOCKER_CONTAINER" ] || [ "$USER" = "root" ]; then
    echo "Running in Docker container..."

    # Docker uses /opt/venv (set in Dockerfile) to avoid volume mount conflicts
    export UV_PROJECT_ENVIRONMENT=/opt/venv
    export UV_LINK_MODE=copy
    export VIRTUAL_ENV=/opt/venv
    export PATH="/opt/venv/bin:$PATH"
else
    echo "Running in local environment, UV will use project defaults"

    # Ensure we don't inherit Docker environment variables
    unset UV_PROJECT_ENVIRONMENT
    unset UV_SYSTEM_PYTHON
fi

# Pop run_command from arguments
run_command="$1"
shift

if [ "$run_command" = "webserver" ]; then
    # Web server (FastAPI + Flet)
    uv run python -m app.entrypoints.webserver
elif [ "$run_command" = "scheduler" ]; then
    # Scheduler component. Dev auto-reload matters MORE here than for the
    # webserver: a scheduler process running stale code has no requests to
    # make the staleness visible - it just executes old job logic against
    # new data, silently, for days (APP_ENV from .env or SCHEDULER_WATCH
    # override, mirroring the worker branch below).
    if { [ "$APP_ENV" = "dev" ] || [ "$SCHEDULER_WATCH" = "true" ]; } \
        && uv run python -c "import watchfiles" 2>/dev/null; then
        echo "Starting scheduler with auto-reload..."
        exec uv run watchfiles --filter python \
            "python -m app.entrypoints.scheduler" /code/app
    else
        # watchfiles ships via uvicorn[standard] (not guaranteed in every
        # stack shape) - a missing watcher must not stop the scheduler.
        exec uv run python -m app.entrypoints.scheduler
    fi
elif [ "$run_command" = "worker" ]; then
    # Worker component using STANDARD arq CLI
    queue_type="${1:-system}"  # Default to system queue if not specified
    shift

    # Build the module path for the queue
    worker_module="app.components.worker.queues.${queue_type}.WorkerSettings"

    # Development mode auto-reload (APP_ENV from .env or WORKER_WATCH override)
    if [ "$APP_ENV" = "dev" ] || [ "$WORKER_WATCH" = "true" ]; then
        echo "Starting ${queue_type} worker with auto-reload..."
        exec uv run python -m arq "${worker_module}" --watch /code/app "$@"
    else
        echo "Starting ${queue_type} worker..."
        exec uv run python -m arq "${worker_module}" "$@"
    fi
elif [ "$run_command" = "build-watch" ]; then
    # Re-fingerprint web frontend assets whenever Tailwind (or a hand-edited
    # source) rewrites them, so the manifest never goes stale in dev.
    exec uv run python -m app.components.web_frontend.build_watch
elif [ "$run_command" = "lint" ]; then
    uv run ruff check .
elif [ "$run_command" = "typecheck" ]; then
    uv run mypy .
elif [ "$run_command" = "test" ]; then
    uv run pytest "$@"
elif [ "$run_command" = "health" ]; then
    uv run python -m app.cli.health check "$@"
elif [ "$run_command" = "help" ]; then
    echo "Available commands:"
    echo "  webserver   - Run FastAPI + Flet web server"
    echo "  scheduler   - Run scheduler component"
    echo "  worker      - Run arq worker (standard arq CLI patterns)"
    echo "  build-watch - Re-fingerprint web frontend assets on change"
    echo "  health      - Check system health status"
    echo "  lint        - Run ruff linting"
    echo "  typecheck   - Run mypy type checking"
    echo "  test        - Run pytest test suite"
    echo "  help        - Show this help message"
else
    echo "Unknown command: $run_command"
    echo "Available commands: webserver, scheduler, worker, build-watch, health, lint, typecheck, test, help"
    exit 1
fi
