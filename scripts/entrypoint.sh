#!/usr/bin/env bash

set -e

# Configure UV environment based on execution context.
#
# A container is known by the file its runtime writes into every one
# (docker: /.dockerenv, podman: /run/.containerenv), not by a variable
# each compose service has to remember. The dev build-static-watcher
# forgot DOCKER_CONTAINER, took the local branch below, and ran uv
# against /code/.venv - the HOST's venv, through the bind mount -
# rewriting it with a Linux interpreter, so the developer's dev tools
# vanished after every ``make serve``. DOCKER_CONTAINER still forces it.
if [ -f /.dockerenv ] || [ -f /run/.containerenv ] \
    || [ -n "$DOCKER_CONTAINER" ] || [ "$USER" = "root" ]; then
    echo "Running in Docker container..."

    # Docker uses /opt/venv (set in Dockerfile) to avoid volume mount conflicts
    export UV_PROJECT_ENVIRONMENT=/opt/venv
    export UV_LINK_MODE=copy
    export VIRTUAL_ENV=/opt/venv
    export PATH="/opt/venv/bin:$PATH"

    # The image was synced when it was built and its venv is on PATH, so
    # each role runs its program directly and ``exec``s into it: no idle
    # ``uv run`` parent holding memory, no lock check on every start, and
    # docker's SIGTERM reaches the program itself. In dev the source is
    # mounted from the host, so one sync first lands a dependency added
    # there (``uv add``) - it runs and exits.
    RUN=()
    if [ "$APP_ENV" = "dev" ]; then
        uv sync --frozen --quiet
    fi
else
    echo "Running in local environment, UV will use project defaults"

    # Ensure we don't inherit Docker environment variables
    unset UV_PROJECT_ENVIRONMENT
    unset UV_SYSTEM_PYTHON

    # A laptop venv may be behind the lock; ``uv run`` brings it in step.
    RUN=(uv run)
fi

# Pop run_command from arguments
run_command="$1"
shift

if [ "$run_command" = "webserver" ]; then
    # Web server (FastAPI + Flet)
    exec "${RUN[@]}" python -m app.entrypoints.webserver
elif [ "$run_command" = "scheduler" ]; then
    # Scheduler component. Dev auto-reload matters MORE here than for the
    # webserver: a scheduler process running stale code has no requests to
    # make the staleness visible - it just executes old job logic against
    # new data, silently, for days (APP_ENV from .env or SCHEDULER_WATCH
    # override, mirroring the worker branch below).
    if { [ "$APP_ENV" = "dev" ] || [ "$SCHEDULER_WATCH" = "true" ]; } \
        && "${RUN[@]}" python -c "import watchfiles" 2>/dev/null; then
        echo "Starting scheduler with auto-reload..."
        exec "${RUN[@]}" watchfiles --filter python \
            "python -m app.entrypoints.scheduler" /code/app
    else
        # watchfiles ships via uvicorn[standard] (not guaranteed in every
        # stack shape) - a missing watcher must not stop the scheduler.
        exec "${RUN[@]}" python -m app.entrypoints.scheduler
    fi
elif [ "$run_command" = "worker" ]; then
    # Jobs one worker process runs at once on a queue, from
    # Settings.WORKER_QUEUES (app/core/queue_workers.py). Stops the
    # container if the settings name a queue that does not exist.
    worker_concurrency() {
        out=$("${RUN[@]}" python -m app.components.worker.runtime concurrency "$1" 2>&1) || {
            printf '%s\n' "$out" >&2
            return 1
        }
        printf '%s\n' "$out" | tail -n 1
    }
    # Worker component using arq
    queue_type="${1:-system}"  # Default to system queue if not specified
    shift


    # Development mode auto-reload (APP_ENV from .env or WORKER_WATCH override).
    #
    # NOT arq's own watch flag: that closes the worker and calls async_run()
    # again in the same process, with the same already-imported modules.
    # It prints "files changed, reloading arq worker..." and runs the old
    # code. watchfiles restarts the process, as the scheduler branch does,
    # so a job runs the code on disk.
    #
    # Both branches run the same entrypoint, which owns its event loop.
    # arq's CLI does not, and on Python 3.14 that is a RuntimeError -
    # dev never saw it because the watch flag ran the worker inside
    # asyncio.run, and nothing else ran the other branch.
    if { [ "$APP_ENV" = "dev" ] || [ "$WORKER_WATCH" = "true" ]; } \
        && "${RUN[@]}" python -c "import watchfiles" 2>/dev/null; then
        echo "Starting ${queue_type} worker with auto-reload..."
        exec "${RUN[@]}" watchfiles --filter python \
            "python -m app.entrypoints.worker ${queue_type}" /code/app
    else
        echo "Starting ${queue_type} worker..."
        exec "${RUN[@]}" python -m app.entrypoints.worker "${queue_type}"
    fi
elif [ "$run_command" = "build-watch" ]; then
    # Re-fingerprint web frontend assets whenever Tailwind (or a hand-edited
    # source) rewrites them, so the manifest never goes stale in dev.
    exec "${RUN[@]}" python -m app.components.web_frontend.build_watch
elif [ "$run_command" = "lint" ]; then
    "${RUN[@]}" ruff check .
elif [ "$run_command" = "typecheck" ]; then
    "${RUN[@]}" mypy .
elif [ "$run_command" = "test" ]; then
    "${RUN[@]}" pytest "$@"
elif [ "$run_command" = "health" ]; then
    "${RUN[@]}" python -m app.cli.health check "$@"
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
