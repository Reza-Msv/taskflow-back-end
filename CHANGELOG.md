# Changelog

All notable changes to the TaskFlow Backend project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Step 1] - MVP Authentication System Implementation (2026-10-17)

### Added
- **Identity Domain Layer (`src/taskflow/domains/identity/`)**:
  - **Models (`models.py`)**:
    - `User`: Primary user entity with UUID primary key, indexed unique `email`, `full_name`, `is_active`, `is_superuser`, and UTC timestamp mixins.
    - `UserCredentials`: 1:1 credential entity linked to `users.id` with `ondelete="CASCADE"`, storing Argon2id hashed passwords.
    - `Session`: Database-backed session entity storing SHA-256 token digest, CSRF token, user agent, IP address, and UTC expiration time.
  - **DTO Schemas (`schemas.py`)**:
    - `RegisterRequest`: Registration payload with email validation and password length checks (min 8, max 128 chars).
    - `LoginRequest`: Email and password authentication payload.
    - `UserResponse`: Public user profile DTO with `ConfigDict(from_attributes=True)`.
    - `AuthResponse`: Authentication response payload returning user profile, CSRF token, and human-readable message.
    - `MessageResponse`: Generic operational message response.
  - **Repository (`repository.py`)**:
    - `UserRepository`: Case-insensitive email query, primary key lookup, and entity creation.
    - `SessionRepository`: Creation of active sessions, lookup by SHA-256 token hash with eager user loading, and deletion/revocation.
  - **Service (`service.py`)**:
    - `AuthService`: Transaction boundary controller with explicit `session.commit()`.
    - Integrated Argon2id password hashing (`argon2-cffi`) with production parameters (64MB memory, 3 iterations, 4 parallelism).
    - Session and CSRF token generation via `secrets.token_urlsafe` and `secrets.token_hex`.
    - Session validation and lazy cleanup of expired sessions.
- **FastAPI Dependencies (`src/taskflow/api/deps.py`)**:
  - `get_db`: Asynchronous generator yielding SQLAlchemy `AsyncSession`.
  - `get_auth_service`: Dependency injecting `AuthService`.
  - `get_current_session_credentials`: Dual-transport session validator supporting both HttpOnly cookies (`taskflow_session`) and `Authorization: Bearer <token>` headers. Enforces `X-CSRF-Token` header verification on mutating HTTP methods (`POST`, `PUT`, `PATCH`, `DELETE`) for cookie-based clients.
  - `get_current_user` and `get_current_active_user`: Dependency route guards.
- **API Endpoints (`src/taskflow/api/v1/endpoints/auth.py`)**:
  - `POST /api/v1/auth/register`: Creates new user, sets `taskflow_session` (HttpOnly, SameSite=Lax) and `taskflow_csrf` cookies, returns `201 Created` with `AuthResponse`.
  - `POST /api/v1/auth/login`: Authenticates credentials, creates active session, sets cookies, returns `200 OK` with `AuthResponse`.
  - `POST /api/v1/auth/logout`: Revokes database session, clears cookies, returns `200 OK` with `MessageResponse`. Requires `X-CSRF-Token` for cookie-authenticated clients.
  - `GET /api/v1/auth/me`: Returns profile of currently authenticated user (`200 OK` with `UserResponse`).
- **Alembic Database Migrations**:
  - `alembic/env.py`: Asyncpg-compatible migration runner using SQLAlchemy 2.x async engine.
  - `alembic/script.py.mako`: Standard migration template.
  - `alembic/versions/20261017_0001_create_identity_tables.py`: Initial DDL migration creating `users`, `user_credentials`, and `sessions` tables with indexes and foreign key constraints.
- **Automated Test Suite (`tests/api/test_auth.py`)**:
  - Added 13 comprehensive tests covering:
    - Successful registration and cookie issuance.
    - Duplicate email rejection (409 Conflict).
    - Payload validation errors for email and password (422 Unprocessable Content).
    - Public registration toggle enforcement (403 Forbidden).
    - Successful login and invalid credentials handling (401 Unauthorized).
    - Deactivated user login rejection (403 Forbidden).
    - Unauthenticated `/me` rejection (401 Unauthorized).
    - Current user retrieval via HttpOnly cookie and Bearer token.
    - Expired session rejection (401 Unauthorized).
    - Logout with CSRF token verification and subsequent session invalidation.
    - Logout via Bearer token.
  - Configured safe test database fixture with SQLite async in `tests/conftest.py`.
- **Documentation & Contracts (`docs/api-contracts.md`)**:
  - Complete OpenAPI contract specifications for authentication endpoints.
  - Security model documentation (HttpOnly cookie, CSRF header, Bearer fallback, CORS configuration).
  - Next.js frontend integration example demonstrating credentialed requests and CSRF header transmission.
- **Environment Configuration (`.env`)**:
  - Created `.env` file from `.env.example` with local development settings.

### Changed
- `src/taskflow/main.py`: Mounted `auth_router` in API v1 router and registered `RequestValidationError` handler to return unified `VALIDATION_ERROR` responses.
- `src/taskflow/core/config.py`: Added `CSRF_COOKIE_NAME` and `CSRF_HEADER_NAME`, typed `SESSION_COOKIE_SAMESITE`, and set `ENABLE_PUBLIC_REGISTRATION=True` default.
- `src/taskflow/core/exceptions.py`: Added `validation_exception_handler` to standardize 422 errors.
- `pyproject.toml` and `requirements-dev.txt`: Added `aiosqlite` for test database isolation.
