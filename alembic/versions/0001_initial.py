"""initial

Revision ID: 0001
Revises:
Create Date: 2026-09-22

Empty initial revision. Stamps alembic_version so /health/ready can
confirm migrations are at head before any domain table exists.
"""

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
