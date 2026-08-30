"""Record which document versions each course index corpus covers.

Revision ID: 0003_course_index_coverage
Revises: 0002_mvp_core
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_course_index_coverage"
down_revision: str | None = "0002_mvp_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("course_indexes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("covered_document_version_ids", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("course_indexes", schema=None) as batch_op:
        batch_op.drop_column("covered_document_version_ids")
