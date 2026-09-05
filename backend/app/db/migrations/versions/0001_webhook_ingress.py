"""create webhook ingress tables

Revision ID: 0001_webhook_ingress
Revises:
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0001_webhook_ingress"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("merchant_id", sa.String(128), nullable=False),
        sa.Column("razorpay_event_id", sa.String(255), nullable=False),
        sa.Column("event_name", sa.String(255), nullable=False),
        sa.Column("raw_body", sa.LargeBinary(), nullable=False),
        sa.Column("payload_json", json_type, nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("validation_status", sa.String(64), nullable=False),
        sa.Column("processing_status", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("merchant_id", "razorpay_event_id", name="uq_webhook_events_merchant_event"),
    )
    op.create_index("ix_webhook_events_processing_status", "webhook_events", ["processing_status"])
    op.create_table(
        "job_queue",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_type", sa.String(128), nullable=False),
        sa.Column("dedupe_key", sa.String(512), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(255)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_job_queue_dedupe_key"),
    )
    op.create_index("ix_job_queue_status_available", "job_queue", ["status", "available_at"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(128), nullable=False),
        sa.Column("entity_id", sa.Uuid()),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("metadata", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_correlation_id", "audit_logs", ["correlation_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("job_queue")
    op.drop_table("webhook_events")
