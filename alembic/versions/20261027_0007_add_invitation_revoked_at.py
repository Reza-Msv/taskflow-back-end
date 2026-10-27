"""add invitation revoked_at column

Revision ID: 20261027_0007
Revises: 20261027_0006
Create Date: 2026-10-27 15:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261027_0007"
down_revision: str | None = "20261027_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invitations",
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_invitations_revoked_at",
        "invitations",
        ["revoked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_invitations_revoked_at", table_name="invitations")
    op.drop_column("invitations", "revoked_at")
