"""create deterministic recovery assessments

Revision ID: 0006_recovery_assessments
Revises: 0005_recovery_outcomes
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0006_recovery_assessments"
down_revision = "0005_recovery_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "recovery_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("recovery_case_id", sa.Uuid(), sa.ForeignKey("recovery_cases.id"), nullable=False),
        sa.Column("diagnosis", sa.String(64), nullable=False),
        sa.Column("diagnosis_confidence", sa.String(16), nullable=False),
        sa.Column("recovery_probability", sa.Numeric(5, 4), nullable=False),
        sa.Column("payment_amount", sa.Integer(), nullable=False),
        sa.Column("expected_recovery_value", sa.Integer(), nullable=False),
        sa.Column("candidate_action", sa.String(64), nullable=False),
        sa.Column("reason_codes", json_type, nullable=False),
        sa.Column("feature_snapshot", json_type, nullable=False),
        sa.Column("score_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("recovery_case_id", "score_version", name="uq_recovery_assessments_case_version"),
    )
    op.create_index("ix_recovery_assessments_case_created", "recovery_assessments", ["recovery_case_id", "created_at"])


def downgrade() -> None:
    op.drop_table("recovery_assessments")
