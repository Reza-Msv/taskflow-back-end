# API Contracts & Conventions

## Source of Truth
FastAPI schemas are exported as OpenAPI 3.1 specification at:
- JSON: `/api/v1/openapi.json`
- Interactive Swagger: `/docs`
- ReDoc: `/redoc`

The frontend consumes this schema directly using `openapi-typescript` to ensure zero drift.

## Standard Error Format
All errors (4xx, 5xx) return:
```json
{
  "error": {
    "code": "ERROR_MACHINE_NAME",
    "message": "Human readable, safe error message",
    "details": {},
    "request_id": "req_01j9a..."
  }
}
```

Standard Error Codes:
- `VALIDATION_ERROR` (422): Schema/payload validation failure.
- `INVALID_CREDENTIALS` (401): Incorrect email or password.
- `UNAUTHORIZED` (401): Missing, malformed, or expired session.
- `CSRF_TOKEN_INVALID` (403): Missing or invalid `X-CSRF-Token` header for cookie-authenticated mutating requests.
- `USER_INACTIVE` (403): User account has been deactivated.
- `REGISTRATION_DISABLED` (403): Public registration is disabled by policy.
- `EMAIL_ALREADY_EXISTS` (409): Account with provided email already registered.
- `INTERNAL_ERROR` (500): Unexpected server failure.

---

## Authentication & Session API Contracts

### Overview & Security Model
- **Primary Auth Mode**: Secure, `HttpOnly`, `SameSite=Lax` cookie (`taskflow_session`).
- **CSRF Protection**: For browser clients using cookies, mutating endpoints (`POST`, `PUT`, `PATCH`, `DELETE`) require the `X-CSRF-Token` header matching the session CSRF token. The CSRF token is provided in the JSON login/register response body and as a client-readable cookie (`taskflow_csrf`).
- **Alternative Auth Mode**: Bearer token header (`Authorization: Bearer <token>`). When Bearer tokens are used, CSRF header checks are omitted (safe per OWASP guidance).
- **CORS Requirements**: Frontend requests from `http://localhost:3000` must specify `credentials: "include"` (Fetch API) or `withCredentials: true` (Axios) so session cookies are included in requests.

---

### 1. User Registration
`POST /api/v1/auth/register`

Creates a new user and hashes password with Argon2id. Registration does not create an active session, does not issue cookies, and does not automatically log the user in. The client redirects the user to the login endpoint to authenticate.

**Request Headers**:
- `Content-Type: application/json`

**Request Body**:
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!",
  "full_name": "Alice Smith"
}
```
*Validation Rules*:
- `email`: Valid email format.
- `password`: String, min 8 characters, max 128 characters.
- `full_name`: String, min 1 character, max 255 characters.

**Success Response**: `201 Created`
- **Cookies Set**: None
- **Response Body**:
```json
{
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "full_name": "Alice Smith",
    "is_active": true,
    "is_superuser": false,
    "created_at": "2026-10-17T07:30:00Z",
    "updated_at": "2026-10-17T07:30:00Z"
  },
  "message": "User registered successfully"
}
```

**Errors**:
- `409 Conflict`: `EMAIL_ALREADY_EXISTS`
- `403 Forbidden`: `REGISTRATION_DISABLED` (if `ENABLE_PUBLIC_REGISTRATION=false`)
- `422 Unprocessable Content`: `VALIDATION_ERROR`

---

### 2. User Login
`POST /api/v1/auth/login`

Authenticates credentials, generates a new session, and sets session cookies.

**Request Body**:
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```

**Success Response**: `200 OK`
- **Cookies Set**:
  - `taskflow_session`: `<session_token>` (`HttpOnly; Path=/; SameSite=Lax; Max-Age=604800`)
  - `taskflow_csrf`: `<csrf_token>` (`Path=/; SameSite=Lax; Max-Age=604800`)
- **Response Body**:
```json
{
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "full_name": "Alice Smith",
    "is_active": true,
    "is_superuser": false,
    "created_at": "2026-10-17T07:30:00Z",
    "updated_at": "2026-10-17T07:30:00Z"
  },
  "csrf_token": "a1b2c3d4e5f60718...",
  "message": "Authenticated successfully"
}
```

**Errors**:
- `401 Unauthorized`: `INVALID_CREDENTIALS` (email not found or incorrect password)
- `403 Forbidden`: `USER_INACTIVE`
- `422 Unprocessable Content`: `VALIDATION_ERROR`

---

### 3. User Logout
`POST /api/v1/auth/logout`

Revokes the active session in PostgreSQL and clears cookies on the client.

**Authentication & CSRF Requirements**:
- Cookie Auth:
  - Must include `taskflow_session` cookie.
  - Must include `X-CSRF-Token: <csrf_token>` header.
- Bearer Auth:
  - Must include `Authorization: Bearer <session_token>` header (CSRF header optional).

**Success Response**: `200 OK`
- **Cookies Deleted**:
  - `taskflow_session` (expired/deleted)
  - `taskflow_csrf` (expired/deleted)
- **Response Body**:
```json
{
  "message": "Successfully logged out"
}
```

**Errors**:
- `401 Unauthorized`: `UNAUTHORIZED`
- `403 Forbidden`: `CSRF_TOKEN_INVALID`

---

### 4. Current User Profile
`GET /api/v1/auth/me`

Retrieves the authenticated user profile.

**Authentication Requirements**:
- `taskflow_session` cookie OR `Authorization: Bearer <session_token>` header.

**Success Response**: `200 OK`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "full_name": "Alice Smith",
  "is_active": true,
  "is_superuser": false,
  "created_at": "2026-10-17T07:30:00Z",
  "updated_at": "2026-10-17T07:30:00Z"
}
```

**Errors**:
- `401 Unauthorized`: `UNAUTHORIZED` (missing, invalid, or expired session)
- `403 Forbidden`: `USER_INACTIVE`

---

## Next.js Frontend Integration Example

```typescript
// Example fetch utility with CSRF header injection and credential inclusion
async function apiRequest<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  // Extract CSRF token from document.cookie
  const csrfToken = document.cookie
    .split("; ")
    .find((row) => row.startsWith("taskflow_csrf="))
    ?.split("=")[1];

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (csrfToken && options.method && ["POST", "PUT", "PATCH", "DELETE"].includes(options.method.toUpperCase())) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  const response = await fetch(`http://localhost:8000${endpoint}`, {
    ...options,
    headers,
    credentials: "include", // Essential for HttpOnly session cookie transmission
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error?.message || "API request failed");
  }
  return data;
}
```

---

## Pagination Schema
Offset-based pagination:
```json
{
  "items": [],
  "page": 1,
  "page_size": 25,
  "total": 100
}
```
Cursor-based pagination (for high-volume feeds):
```json
{
  "items": [],
  "next_cursor": "eyJpZCI6...",
  "has_more": true
}
```
