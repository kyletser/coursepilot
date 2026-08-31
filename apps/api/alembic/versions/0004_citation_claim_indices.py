"""Persist every claim binding for a citation.

Revision ID: 0004_citation_claim_indices
Revises: 0003_course_index_coverage
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_citation_claim_indices"
down_revision: str | None = "0003_course_index_coverage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("citations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("claim_indices", sa.JSON(), nullable=True))
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("UPDATE citations SET claim_indices = json_build_array(claim_index)")
    else:
        op.execute("UPDATE citations SET claim_indices = json_array(claim_index)")
    with op.batch_alter_table("citations", schema=None) as batch_op:
        batch_op.alter_column("claim_indices", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("citations", schema=None) as batch_op:
        batch_op.drop_column("claim_indices")
