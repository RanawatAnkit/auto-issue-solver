"""Sync agentstateenum with the model and add baseline_test_result

Earlier migrations only created 10 of the AgentStateEnum values; the rest were
added to existing databases by hand, so a fresh `alembic upgrade head` produced a
database that rejected states such as QUEUED or PATCH_APPLIED.

Revision ID: 9c1d2e3f4a5b
Revises: 74baf9cff657
Create Date: 2026-10-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c1d2e3f4a5b'
down_revision: Union[str, None] = '74baf9cff657'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kept as a literal list (not imported from app.models) so this migration stays
# reproducible even if the model enum changes later.
AGENT_STATES = [
    "QUEUED", "RECEIVED", "PENDING", "ANALYZING", "ANALYZING_ISSUE", "ANALYZING_REPOSITORY",
    "RETRIEVING_CONTEXT", "PLANNING", "PLAN_READY", "CODING", "PATCH_APPLIED", "CODE_CHANGED",
    "TESTING", "TEST_FAILED", "ANALYZING_FAILURE", "REPAIR_PLANNING", "REPAIRING", "TESTING_REPAIR",
    "REPAIR_SUCCESS", "REPAIR_EXHAUSTED", "VALIDATING", "BRANCH_CREATED", "COMMITTING", "COMMITTED",
    "PUSHING", "PUSHED", "CREATING_PR", "PR_CREATED", "COMPLETED", "FIXING", "FAILED",
    "VALIDATION_FAILED", "BRANCH_CREATION_FAILED", "COMMIT_FAILED", "PUSH_FAILED",
    "PR_CREATION_FAILED", "CANCELLED",
]


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot be used in the same transaction that adds it.
    with op.get_context().autocommit_block():
        for state in AGENT_STATES:
            op.execute(f"ALTER TYPE agentstateenum ADD VALUE IF NOT EXISTS '{state}'")

    op.add_column('agent_runs', sa.Column('baseline_test_result', sa.JSON(), nullable=True))


def downgrade() -> None:
    # PostgreSQL cannot drop enum values; only the column is reverted.
    op.drop_column('agent_runs', 'baseline_test_result')
