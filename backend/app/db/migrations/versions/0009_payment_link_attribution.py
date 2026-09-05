"""persist verified Payment Link attribution

Revision ID: 0009_payment_link_attribution
Revises: 0008_execution_requests
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_payment_link_attribution"
down_revision = "0008_execution_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("payment_links",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("merchant_id", sa.String(128), nullable=False),
        sa.Column("execution_request_id", sa.Uuid(), sa.ForeignKey("execution_requests.id"), unique=True),
        sa.Column("razorpay_payment_link_id", sa.String(255), nullable=False), sa.Column("reference_id", sa.String(40), nullable=False),
        sa.Column("razorpay_order_id", sa.String(255)), sa.Column("captured_payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), unique=True),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False), sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("source_webhook_event_id", sa.Uuid(), sa.ForeignKey("webhook_events.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("merchant_id", "razorpay_payment_link_id", name="uq_payment_links_merchant_link"))
    op.create_table("recovery_attributions",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("merchant_id", sa.String(128), nullable=False),
        sa.Column("execution_request_id", sa.Uuid(), sa.ForeignKey("execution_requests.id"), nullable=False),
        sa.Column("recovery_case_id", sa.Uuid(), sa.ForeignKey("recovery_cases.id"), nullable=False),
        sa.Column("policy_decision_id", sa.Uuid(), sa.ForeignKey("policy_decisions.id"), nullable=False),
        sa.Column("payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(255), nullable=False), sa.Column("razorpay_payment_link_id", sa.String(255), nullable=False),
        sa.Column("attribution_status", sa.String(16), nullable=False), sa.Column("attributed_amount_minor_units", sa.Integer()), sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("evidence", json_type, nullable=False), sa.Column("source_webhook_event_id", sa.Uuid(), sa.ForeignKey("webhook_events.id"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("payment_id", name="uq_recovery_attributions_payment"),
        sa.UniqueConstraint("execution_request_id", "payment_id", name="uq_recovery_attributions_execution_payment"))
    op.add_column("recovery_outcomes", sa.Column("execution_request_id", sa.Uuid(), sa.ForeignKey("execution_requests.id")))
    op.add_column("recovery_outcomes", sa.Column("razorpay_payment_link_id", sa.String(255)))
    op.add_column("recovery_outcomes", sa.Column("attributed_amount_minor_units", sa.Integer()))
    op.add_column("recovery_outcomes", sa.Column("currency", sa.String(8)))
    op.add_column("recovery_outcomes", sa.Column("recovery_attribution_id", sa.Uuid(), sa.ForeignKey("recovery_attributions.id"), unique=True))


def downgrade() -> None:
    op.drop_column("recovery_outcomes", "recovery_attribution_id")
    op.drop_column("recovery_outcomes", "currency")
    op.drop_column("recovery_outcomes", "attributed_amount_minor_units")
    op.drop_column("recovery_outcomes", "razorpay_payment_link_id")
    op.drop_column("recovery_outcomes", "execution_request_id")
    op.drop_table("recovery_attributions")
    op.drop_table("payment_links")
