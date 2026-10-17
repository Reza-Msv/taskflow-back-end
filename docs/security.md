# Security & OWASP Checklist

1. **Authentication & Session Security**:
   - **Password Hashing**: Argon2id via `argon2-cffi` (`time_cost=3`, `memory_cost=64MB`, `parallelism=4`, `salt_len=16`, `hash_len=32`).
   - **Session Token Protection**: Session tokens are cryptographically generated via `secrets.token_urlsafe(32)`. The database persists only the SHA-256 digest (`token_hash`) so that a compromised database dump cannot yield active sessions.
   - **Cookie Hardening**: `taskflow_session` cookie is marked `HttpOnly` to mitigate XSS exfiltration, with configurable `Secure` flag and `SameSite=Lax`.
   - **CSRF Defense**: All mutating requests (`POST`, `PUT`, `PATCH`, `DELETE`) authenticated via cookies require an `X-CSRF-Token` header matching the active session's `csrf_token`.
   - **Alternative Bearer Header**: API/script clients can pass `Authorization: Bearer <session_token>`. Bearer requests are inherently immune to CSRF in browsers and omit the CSRF header requirement.
   - **Brute Force Protection**: Centralized domain error codes and parameterized queries prevent timing attacks and credential leakage.
2. **Access Control (Broken Object Level Authorization / IDOR)**:
   - Mandatory membership check for every organization request.
   - Project-level authorization for restricted projects.
3. **Data Protection**:
   - Parameterized SQL queries exclusively through SQLAlchemy 2.x async ORM.
   - Strict input validation through Pydantic v2 schemas (`EmailStr`, string length bounds).
   - Sanitized HTML rendering in Markdown representations.
4. **Auditability**:
   - Request ID tracking (`X-Request-ID`) generated per request and injected into all response headers and standard error payloads.
   - Activity feed records all mutations with actor ID and timestamp.
