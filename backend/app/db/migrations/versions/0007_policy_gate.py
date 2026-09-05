"""create deterministic policy gate

Revision ID: 0007_policy_gate
Revises: 0006_recovery_assessments
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_policy_gate"
down_revision = "0006_recovery_assessments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "merchant_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("merchant_id", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("allowed_actions", json_type, nullable=False),
        sa.Column("max_amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("min_recovery_probability", sa.Numeric(5, 4), nullable=False),
        sa.Column("min_expected_recovery_value_minor_units", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("daily_recovery_budget_minor_units", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("merchant_id", "policy_version", name="uq_merchant_policies_version"),
    )
    op.create_index("ix_merchant_policies_merchant_id", "merchant_policies", ["merchant_id"])
    op.create_table(
        "policy_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("recovery_case_id", sa.Uuid(), sa.ForeignKey("recovery_cases.id"), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), sa.ForeignKey("recovery_assessments.id"), nullable=False),
        sa.Column("merchant_id", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("candidate_action", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_codes", json_type, nullable=False),
        sa.Column("evaluated_facts", json_type, nullable=False),
        sa.Column("authorized_amount", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("recovery_case_id", "assessment_id", "policy_version", name="uq_policy_decisions_assessment_version"),
    )
    op.create_index("ix_policy_decisions_merchant_id", "policy_decisions", ["merchant_id"])
    op.create_index("ix_policy_decisions_case_decision", "policy_decisions", ["recovery_case_id", "decision"])


def downgrade() -> None:
    op.drop_table("policy_decisions")
    op.drop_table("merchant_policies")
