# Database & Migrations

1. **SQLAlchemy 2.x Async**:
   - Use `AsyncSession` with explicit transaction control.
   - Commit at the service transaction boundary, never inside individual repository helper queries.
2. **Key Design**:
   - Primary keys: UUIDv7 / UUIDv4.
   - Issue keys: Transactional atomic counter per project sequence (`WEB-14`).
3. **Relationships & Performance**:
   - Prevent N+1 queries using `selectinload` or `joinedload`.
   - Appropriate composite indexes on `(organization_id, created_at)` and status columns.
4. **Migrations (Alembic)**:
   - All schema changes require a tested Alembic migration in `alembic/versions/`.
   - Never use `Base.metadata.create_all()` in production code.
