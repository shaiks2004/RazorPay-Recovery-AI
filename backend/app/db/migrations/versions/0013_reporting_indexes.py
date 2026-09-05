"""add reporting query indexes
Revision ID: 0013_reporting_indexes
Revises: 0012_synthetic_datasets
"""
from alembic import op
revision="0013_reporting_indexes"; down_revision="0012_synthetic_datasets"; branch_labels=None; depends_on=None
def upgrade():
    op.create_index("ix_recovery_cases_merchant_created","recovery_cases",["merchant_id","created_at"])
    op.create_index("ix_recovery_cases_merchant_status","recovery_cases",["merchant_id","status"])
    op.create_index("ix_payment_links_merchant_created","payment_links",["merchant_id","created_at"])
def downgrade():
    op.drop_index("ix_payment_links_merchant_created",table_name="payment_links");op.drop_index("ix_recovery_cases_merchant_status",table_name="recovery_cases");op.drop_index("ix_recovery_cases_merchant_created",table_name="recovery_cases")
