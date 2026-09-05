"""create deterministic evaluation metrics snapshots

Revision ID: 0010_evaluation_metrics
Revises: 0009_payment_link_attribution
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_evaluation_metrics"
down_revision = "0009_payment_link_attribution"
branch_labels = None
depends_on = None

def upgrade() -> None:
    js = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("evaluation_runs", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("run_name", sa.String(128), nullable=False), sa.Column("dataset_id", sa.String(128), nullable=False), sa.Column("dataset_version", sa.String(64), nullable=False), sa.Column("run_type", sa.String(16), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("configuration_snapshot", js, nullable=False), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("run_name", "dataset_id", "dataset_version", name="uq_evaluation_runs_dataset"))
    op.create_table("evaluation_run_cases", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("evaluation_run_id", sa.Uuid(), sa.ForeignKey("evaluation_runs.id"), nullable=False), sa.Column("recovery_case_id", sa.Uuid(), sa.ForeignKey("recovery_cases.id"), nullable=False), sa.Column("source_scope", sa.String(16), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("evaluation_run_id", "recovery_case_id", name="uq_evaluation_run_case"))
    op.create_table("metric_snapshots", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("evaluation_run_id", sa.Uuid(), sa.ForeignKey("evaluation_runs.id"), nullable=False), sa.Column("schema_version", sa.String(32), nullable=False), sa.Column("source_scope", sa.String(16), nullable=False), sa.Column("metrics_json", js, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("evaluation_run_id", "schema_version", name="uq_metric_snapshots_run_schema"))

def downgrade() -> None:
    op.drop_table("metric_snapshots")
    op.drop_table("evaluation_run_cases")
    op.drop_table("evaluation_runs")
