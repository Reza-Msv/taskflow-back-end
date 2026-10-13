# Database Model Design

## Key Entities
- **Identity**: `users`, `user_credentials`, `sessions`
- **Tenant**: `organizations`, `organization_memberships`, `invitations`, `teams`, `team_memberships`
- **Projects**: `projects`, `project_memberships`, `project_sequences`
- **Work Management**: `work_items`, `workflow_statuses`, `workflow_transitions`, `labels`, `work_item_labels`, `work_item_comments`, `work_item_activities`
- **Scrum**: `sprints`, `sprint_items`, `sprint_scope_events`, `sprint_retrospectives`

## Constraints & Invariants
1. `organization_id` foreign key on all tenant data.
2. Composite uniqueness:
   - `(organization_id, slug)` on organizations.
   - `(organization_id, key)` on projects (e.g. `WEB`, `PAY`).
   - `(project_id, sequence_number)` on work items.
3. Transactional atomic increments for `project_sequences` to guarantee continuous, gap-free issue numbering without collisions under load.
