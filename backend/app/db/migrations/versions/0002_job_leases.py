"""add durable job leases

Revision ID: 0002_job_leases
Revises: 0001_webhook_ingress
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_job_leases"
down_revision = "0001_webhook_ingress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_queue", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.create_index("ix_job_queue_claimable", "job_queue", ["status", "available_at"])
    op.create_index("ix_job_queue_lease_expiry", "job_queue", ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_job_queue_lease_expiry", table_name="job_queue")
    op.drop_index("ix_job_queue_claimable", table_name="job_queue")
    op.drop_column("job_queue", "lease_expires_at")
