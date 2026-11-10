"""evaluation_results: one row per injected fault (two-fault scenarios)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("evaluation_results") as batch:
        batch.drop_constraint("uq_eval_result", type_="unique")
        batch.create_unique_constraint("uq_eval_result", ["evaluation_run_id", "scenario_id", "strategy", "true_cause_service"])


def downgrade() -> None:
    with op.batch_alter_table("evaluation_results") as batch:
        batch.drop_constraint("uq_eval_result", type_="unique")
        batch.create_unique_constraint("uq_eval_result", ["evaluation_run_id", "scenario_id", "strategy"])
