# Security & OWASP Checklist

1. **Authentication**:
   - Argon2id with recommended memory and iteration parameters.
   - Brute force protection on login attempts.
   - HttpOnly, Secure, SameSite cookies for session storage.
2. **Access Control (Broken Object Level Authorization / IDOR)**:
   - Mandatory membership check for every organization request.
   - Project-level authorization for restricted projects.
3. **Data Protection**:
   - Parameterized SQL queries via SQLAlchemy.
   - Safe input validation through Pydantic v2.
   - Sanitized HTML rendering in Markdown representations.
4. **Auditability**:
   - Request ID tracking across logs.
   - Activity feed records all mutations with actor ID and timestamp.
