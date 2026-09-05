"""add explicit reporting ownership
Revision ID: 0014_reporting_ownership
Revises: 0013_reporting_indexes
"""
from alembic import op
import sqlalchemy as sa
revision="0014_reporting_ownership"; down_revision="0013_reporting_indexes"; branch_labels=None; depends_on=None
def upgrade():
    op.add_column("evaluation_runs",sa.Column("merchant_id",sa.String(128),nullable=True));op.execute("UPDATE evaluation_runs SET merchant_id = 'local-test-merchant' WHERE merchant_id IS NULL");op.alter_column("evaluation_runs","merchant_id",nullable=False);op.create_index("ix_evaluation_runs_merchant_created","evaluation_runs",["merchant_id","created_at"])
    op.add_column("audit_logs",sa.Column("merchant_id",sa.String(128),nullable=True));op.create_index("ix_audit_logs_merchant_created","audit_logs",["merchant_id","created_at"])
def downgrade():
    op.drop_index("ix_audit_logs_merchant_created",table_name="audit_logs");op.drop_column("audit_logs","merchant_id");op.drop_index("ix_evaluation_runs_merchant_created",table_name="evaluation_runs");op.drop_column("evaluation_runs","merchant_id")
