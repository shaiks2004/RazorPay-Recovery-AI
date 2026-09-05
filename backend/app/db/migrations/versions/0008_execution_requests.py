"""create bounded execution requests

Revision ID: 0008_execution_requests
Revises: 0007_policy_gate
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_execution_requests"
down_revision = "0007_policy_gate"
branch_labels = None
depends_on = None

def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("execution_requests",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("merchant_id", sa.String(128), nullable=False),
        sa.Column("recovery_case_id", sa.Uuid(), sa.ForeignKey("recovery_cases.id"), nullable=False),
        sa.Column("policy_decision_id", sa.Uuid(), sa.ForeignKey("policy_decisions.id"), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), sa.ForeignKey("recovery_assessments.id"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True), sa.Column("reference_id", sa.String(40), nullable=False, unique=True),
        sa.Column("razorpay_payment_link_id", sa.String(255), unique=True), sa.Column("amount_minor_units", sa.Integer(), nullable=False), sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("request_payload_hash", sa.String(64), nullable=False),
        sa.Column("response_metadata", json_type), sa.Column("failure_code", sa.String(64)), sa.Column("failure_reason", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_decision_id", name="uq_execution_requests_policy_decision"))

def downgrade() -> None:
    op.drop_table("execution_requests")
