"""create deterministic payment projections

Revision ID: 0003_payment_projections
Revises: 0002_job_leases
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_payment_projections"
down_revision = "0002_job_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("merchant_id", sa.String(128), nullable=False),
        sa.Column("razorpay_order_id", sa.String(255), nullable=False),
        sa.Column("amount", sa.Integer()),
        sa.Column("amount_paid", sa.Integer()),
        sa.Column("amount_due", sa.Integer()),
        sa.Column("currency", sa.String(8)),
        sa.Column("receipt", sa.String(255)),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("source_webhook_event_id", sa.Uuid(), sa.ForeignKey("webhook_events.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("merchant_id", "razorpay_order_id", name="uq_orders_merchant_razorpay_order"),
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("merchant_id", sa.String(128), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(255), nullable=False),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id")),
        sa.Column("razorpay_order_id", sa.String(255)),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("method", sa.String(64)),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(255)),
        sa.Column("error_reason", sa.String(255)),
        sa.Column("source_webhook_event_id", sa.Uuid(), sa.ForeignKey("webhook_events.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("merchant_id", "razorpay_payment_id", name="uq_payments_merchant_razorpay_payment"),
    )
    op.create_index("ix_payments_razorpay_order_id", "payments", ["razorpay_order_id"])
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("first_webhook_event_id", sa.Uuid(), sa.ForeignKey("webhook_events.id"), nullable=False),
        sa.Column("last_webhook_event_id", sa.Uuid(), sa.ForeignKey("webhook_events.id"), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("attempt_source", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("payment_id", name="uq_payment_attempts_payment"),
    )


def downgrade() -> None:
    op.drop_table("payment_attempts")
    op.drop_index("ix_payments_razorpay_order_id", table_name="payments")
    op.drop_table("payments")
    op.drop_table("orders")
