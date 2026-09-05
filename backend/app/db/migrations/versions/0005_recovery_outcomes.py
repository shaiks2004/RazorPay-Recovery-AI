"""add attributed-safe recovery outcomes

Revision ID: 0005_recovery_outcomes
Revises: 0004_recovery_cases
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_recovery_outcomes"
down_revision = "0004_recovery_cases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recovery_outcomes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("recovery_case_id", sa.Uuid(), sa.ForeignKey("recovery_cases.id"), nullable=False),
        sa.Column("observed_payment_id", sa.Uuid(), sa.ForeignKey("payments.id")),
        sa.Column("source_webhook_event_id", sa.Uuid(), sa.ForeignKey("webhook_events.id"), nullable=False),
        sa.Column("outcome_type", sa.String(64), nullable=False),
        sa.Column("attribution_status", sa.String(64), nullable=False),
        sa.Column("attributed_intervention_reference", sa.String(255)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("recovery_case_id", name="uq_recovery_outcomes_case"),
    )
    op.create_index("ix_recovery_outcomes_observed_payment", "recovery_outcomes", ["observed_payment_id"])
    op.create_index("ix_recovery_outcomes_source_event", "recovery_outcomes", ["source_webhook_event_id"])


def downgrade() -> None:
    op.drop_table("recovery_outcomes")
