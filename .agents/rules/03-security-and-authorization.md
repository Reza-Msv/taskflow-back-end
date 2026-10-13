# Security & Authorization

1. **Password Hashing**:
   - Argon2id via `argon2-cffi`. Never store plain text or simple hashes.
2. **Tenant Isolation**:
   - Every multi-tenant model must have `organization_id` foreign key.
   - Every query mutating or fetching tenant data MUST verify active membership of the authenticated user in that organization.
3. **Defense Against IDOR**:
   - Never query solely by `id=item_id`. Always query by `id=item_id, organization_id=current_org_id`.
4. **Secrets & CORS**:
   - Secrets are loaded from `.env` using Pydantic Settings.
   - CORS allowlists only specified trusted frontend origins. Never wildcard `*` with credentials enabled.
