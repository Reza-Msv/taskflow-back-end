# Contributing to TaskFlow Backend

Thank you for contributing to TaskFlow Backend! To ensure software reliability, tenant security, and clean architecture, please follow these guidelines.

## 1. Development Principles
- **Explicit over Implicit**: Keep abstractions close to domain boundaries. No massive catch-all utility modules.
- **Contract Integrity**: Any API schema change must be backward-compatible or versioned. Never break frontend contracts without coordination.
- **Backend Authorization**: Never trust client inputs for access control. Validate organization membership and resource access in FastAPI dependencies and services.
- **Migrations First**: Never alter database models without creating a corresponding Alembic migration.

---

## 2. Professional Tooling & Commands

### Linting & Formatting (Ruff)
Ruff is our unified Python linter and formatter (configured to 88 char line length and standard double quotes):
```bash
# Check for lint violations
ruff check src/ tests/

# Automatically fix safe lint violations
ruff check --fix src/ tests/

# Check formatting without modifying files
ruff format --check src/ tests/

# Format code
ruff format src/ tests/
```

### Static Type Checking (mypy)
```bash
mypy src/ tests/
```

### Automated Testing (pytest)
```bash
# Run all tests
pytest

# Run tests with verbose output
pytest -v
```

### Vulnerability Scanning (pip-audit)
Scan dependencies against known vulnerability databases:
```bash
pip-audit
```

### Pre-commit Hooks
Pre-commit checks run Ruff, trailing whitespace, EOF, YAML/TOML syntax, and merge conflicts:
```bash
# Install git hooks (when inside a git repository)
pre-commit install

# Run pre-commit manually on all files
pre-commit run --all-files
```

---

## 3. Branch & Git Workflow
- Development branch: `develop` or feature branches.
- Branch naming convention:
  - `feat/feature-name` (e.g. `feat/sprint-burndown`)
  - `fix/bug-description`
  - `refactor/scope`
  - `test/test-scope`
- Keep pull requests focused on a single responsibility.

---

## 4. Pull Request Checklist
Before submitting a PR, verify:
- [ ] Code formatted and linted with `ruff check src/ tests/` and `ruff format --check src/ tests/`
- [ ] Type checks pass with `mypy src/ tests/`
- [ ] All unit and integration tests pass with `pytest`
- [ ] Vulnerability scan clean with `pip-audit`
- [ ] Pre-commit hooks pass with `pre-commit run --all-files`
- [ ] New database tables or column modifications have an Alembic migration
- [ ] IDOR checks: tests verify that users cannot access resources from other organizations
- [ ] OpenAPI docs updated and verified at `/api/v1/openapi.json`
