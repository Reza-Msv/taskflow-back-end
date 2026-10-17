# Database Model Design

## Key Entities
- **Identity**: `users`, `user_credentials`, `sessions`
- **Tenant**: `organizations`, `organization_memberships`, `invitations`, `teams`, `team_memberships`
- **Projects**: `projects`, `project_memberships`, `project_sequences`
- **Work Management**: `work_items`, `workflow_statuses`, `workflow_transitions`, `labels`, `work_item_labels`, `work_item_comments`, `work_item_activities`
- **Scrum**: `sprints`, `sprint_items`, `sprint_scope_events`, `sprint_retrospectives`

## Identity Schema Specification (Implemented)

### `users`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Unique identifier (UUIDv4/v7) |
| `email` | `VARCHAR(255)` | Unique, Not Null, Index | User email address (case-insensitive) |
| `full_name` | `VARCHAR(255)` | Not Null | User full display name |
| `is_active` | `BOOLEAN` | Not Null, Default `true` | Account active status flag |
| `is_superuser` | `BOOLEAN` | Not Null, Default `false` | Superuser privileges flag |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Timestamp of creation (UTC) |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Timestamp of last update (UTC) |

### `user_credentials`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Unique identifier |
| `user_id` | `UUID` | FK `users(id)` ON DELETE CASCADE, Unique, Index | 1:1 link to owner user record |
| `password_hash` | `VARCHAR(255)` | Not Null | Argon2id password hash |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Timestamp of creation (UTC) |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Timestamp of last update (UTC) |

### `sessions`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Unique identifier |
| `user_id` | `UUID` | FK `users(id)` ON DELETE CASCADE, Not Null, Index | Owning user ID |
| `token_hash` | `VARCHAR(64)` | Unique, Not Null, Index | SHA-256 digest of session token |
| `csrf_token` | `VARCHAR(64)` | Not Null | Cryptographic CSRF validation token |
| `user_agent` | `VARCHAR(500)` | Nullable | Client User-Agent string |
| `ip_address` | `VARCHAR(45)` | Nullable | Client IP address (IPv4 / IPv6) |
| `expires_at` | `TIMESTAMPTZ` | Not Null, Index | Expiration timestamp (UTC) |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Timestamp of creation (UTC) |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Timestamp of last update (UTC) |

## Constraints & Invariants
1. `organization_id` foreign key on all tenant data.
2. Composite uniqueness:
   - `(organization_id, slug)` on organizations.
   - `(organization_id, key)` on projects (e.g. `WEB`, `PAY`).
   - `(project_id, sequence_number)` on work items.
3. Transactional atomic increments for `project_sequences` to guarantee continuous, gap-free issue numbering without collisions under load.
4. Cascade deletion on `users` cascades to `user_credentials` and all active `sessions`.
