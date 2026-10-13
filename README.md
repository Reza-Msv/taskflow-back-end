# TaskFlow Backend

> Open-source, enterprise-ready project and work management platform backend API built with FastAPI, SQLAlchemy 2.x, Pydantic v2, and PostgreSQL.

## Features
- **Contract-First API**: Automated OpenAPI 3.1 generation for frontend TypeScript client synchronization.
- **Strict Multi-Tenancy**: Tenant-isolated data model and authorization policies.
- **Robust Work Management**: Deterministic workflow state machine, concurrent-safe issue keys, and optimistic concurrency.
- **Scrum & Kanban**: Sprint lifecycle, committed scope tracking, burndown reporting, and column-based boards.
- **No AI Lock-in**: 100% deterministic, reliable open-source software.
- **Professional Tooling**: Configured with Ruff (linting & formatting), pre-commit, pytest, mypy, and pip-audit.

---

## Tech Stack
- **Framework**: FastAPI
- **Language**: Python 3.12+
- **ORM**: SQLAlchemy 2.0 (AsyncIO)
- **Database Driver**: `asyncpg` (PostgreSQL)
- **Migrations**: Alembic
- **Validation & Settings**: Pydantic v2 & Pydantic-Settings
- **Security**: Argon2id password hashing (`argon2-cffi`), JWT / Session auth
- **Testing & Quality**: Pytest, Pytest-AsyncIO, HTTPX, Ruff, Mypy, Pre-commit, Pip-audit

---

## Directory Overview
```text
taskflow-back-end/
├── .agents/             # Agent rules and architecture guidelines
├── alembic/             # Async database schema migrations
├── docs/                # Architecture, API contracts, database, security docs
├── src/taskflow/
│   ├── core/            # Config, security, exceptions, logging
│   ├── db/              # Base ORM models, async session factory
│   ├── api/             # API routes, v1 router, dependencies
│   └── domains/         # Domain business logic (identity, orgs, projects, work items, scrum)
├── tests/               # Unit, integration, and API test suites
├── .pre-commit-config.yaml # Pre-commit hooks configuration
└── pyproject.toml       # Python package, ruff, mypy, and pytest configuration
```

---

## Getting Started

### 1. Prerequisites
- Python 3.11+ (Python 3.12+ recommended)
- PostgreSQL 15+ (or Docker Compose)

### 2. Virtual Environment Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### 3. Environment Configuration
Copy the template and adjust connection credentials:
```bash
cp .env.example .env
```

### 4. Database Migrations
```bash
alembic upgrade head
```

### 5. Running the Dev Server
```bash
uvicorn taskflow.main:app --reload --port 8000
```
- API Docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- OpenAPI JSON: [http://localhost:8000/api/v1/openapi.json](http://localhost:8000/api/v1/openapi.json)
- Health Check: [http://localhost:8000/health](http://localhost:8000/health)

---

## Quality & Development Commands

```bash
# Lint with Ruff
ruff check src/ tests/
ruff check --fix src/ tests/

# Check formatting with Ruff
ruff format --check src/ tests/
ruff format src/ tests/

# Type check with Mypy
mypy src/ tests/

# Run tests with Pytest
pytest

# Dependency vulnerability scanning
pip-audit

# Run all pre-commit hooks manually (inside git repo)
pre-commit run --all-files
```
