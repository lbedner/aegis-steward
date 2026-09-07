# aegis-steward

A production-ready async Python application built with Aegis Stack

## Components

This Aegis Stack project includes the following components:

- **Backend**: FastAPI-based async web server
- **Frontend**: Flet-based Python UI framework (Overseer dashboard at `/dashboard`)
- **Web Frontend**: Server-rendered pages at `/` (Jinja2 + htmx + Alpine, Tailwind/DaisyUI)
- **Scheduler**: APScheduler-based async task scheduling
- **Database**: SQLite database with SQLModel ORM


## Services
- **AI**: Multi-provider AI integration with PydanticAI
- **Comms**: Email, SMS, and voice communications


## Getting Started

### Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Installation

1. **Install dependencies:**
   ```bash
   uv sync                    # Core dependencies only
   # Or for development: uv sync --all-extras  (includes testing, linting, docs)
   ```

2. **Activate virtual environment:**
   ```bash
   source .venv/bin/activate
   ```

3. **Set up environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

4. **Set up database:**
   ```bash
   # Create data directory for SQLite database
   mkdir -p data

   # Database will be created automatically on first run
   # Default location: data/app.db
   ```

5. **Run the application:**
   ```bash
   make serve
   ```

The application will be available at `http://127.0.0.1:8000`. If port 8000 is
already taken, `make serve` picks the next free port and prints the URL it
chose, so two stacks can run side by side without editing any config.

No `make` on Windows? After installing the dev dependencies once
(`uv sync --all-extras`), every command in this README also works as
`uv run poe <target>` (for example `uv run poe serve`); run `uv run poe -h`
for the full list.

## Development

### Running Tests
```bash
make test
```

### Code Quality
```bash
make check  # Run linting, type checking, and tests
```

### CLI Commands

Your project gets its own CLI command **automatically installed** when first used - no setup required! 

```bash
# Health monitoring (auto-installs CLI if needed)
make health                     # Basic health check
make health-detailed            # Detailed component status
make health-json               # JSON output for scripts
make health-quick              # Quick healthy/unhealthy check

# Direct CLI usage (after first make command)
aegis-steward health check              # Basic health check
aegis-steward health check --detailed   # Detailed component status
aegis-steward health check --json       # JSON output for scripts
aegis-steward health quick              # Quick healthy/unhealthy check

# Explore all commands
aegis-steward --help                    # See all available commands
aegis-steward health --help             # See health subcommand options
```

**Environment Issues?** If CLI commands don't work:
```bash
# Ensure you're in the project's virtual environment
unset VIRTUAL_ENV               # Clear Docker contamination
source .venv/bin/activate       # Activate local venv
make health                     # This will auto-install CLI
# Or manually: make install-cli
```

### Documentation
```bash
make docs-serve  # Serve docs at http://localhost:8001/aegis-steward/
```

## Docker

### Development
```bash
docker compose --profile dev up
```

### Production
```bash
docker compose --profile prod up
```

## Configuration

### Database Settings

The database component uses SQLite with the following default configuration:

```bash
# Environment variables (add to .env file)
DATABASE_URL=sqlite:///data/app.db          # Database file location
DATABASE_ENGINE_ECHO=false                  # Enable SQL query logging
```

### Database Usage

```python
from app.core.db import db_session
from sqlmodel import SQLModel, Field, select

# Define a model
class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str

# Use the database
with db_session() as session:
    user = User(name="John Doe", email="john@example.com")
    session.add(user)
    # Auto-committed by context manager
```

## Project Structure

```
aegis-steward/
├── app/                    # Application code
│   ├── components/         # Component implementations
│   │   ├── backend/       # FastAPI backend
│   │   └── frontend/      # Flet frontend
│   │   └── web_frontend/  # htmx web frontend (pages at /)
│   │   └── scheduler/     # APScheduler background tasks
│   ├── cli/               # CLI commands and health monitoring
│   ├── core/              # Core utilities
│   │   ├── config.py      # Environment-dependent configuration
│   │   ├── constants.py   # Immutable constants (12-Factor App)
│   │   ├── db.py          # Database connection and session management
│   │   └── log.py         # Structured logging
│   ├── entrypoints/       # Execution entry points
│   ├── integrations/      # App composition layer
├── tests/                 # Test suite
├── docs/                  # Documentation
├── docker-compose.yml     # Docker services
└── pyproject.toml        # Project configuration
```

---

Generated with [Aegis Stack](https://github.com/lbedner/aegis-stack)