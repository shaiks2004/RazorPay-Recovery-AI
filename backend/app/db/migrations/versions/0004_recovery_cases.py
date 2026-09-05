"""create recovery case foundation

Revision ID: 0004_recovery_cases
Revises: 0003_payment_projections
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0004_recovery_cases"
down_revision = "0003_payment_projections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "recovery_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("merchant_id", sa.String(128), nullable=False),
        sa.Column("customer_reference", sa.String(255)),
        sa.Column("original_payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id")),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("initial_error_code", sa.String(255)),
        sa.Column("initial_error_reason", sa.String(255)),
        sa.Column("eligibility_result", sa.String(64), nullable=False),
        sa.Column("eligibility_reason_codes", json_type, nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recovery_cases_correlation_id", "recovery_cases", ["correlation_id"])
    op.create_index(
        "uq_recovery_cases_active_payment",
        "recovery_cases",
        ["merchant_id", "original_payment_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('NEW', 'ASSESSING')"),
    )


def downgrade() -> None:
    op.drop_table("recovery_cases")
