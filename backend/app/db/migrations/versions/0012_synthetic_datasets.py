"""register immutable synthetic datasets
Revision ID: 0012_synthetic_datasets
Revises: 0011_ai_advisories
"""
from alembic import op
import sqlalchemy as sa
revision="0012_synthetic_datasets"; down_revision="0011_ai_advisories"; branch_labels=None; depends_on=None
def upgrade(): op.create_table("synthetic_datasets",sa.Column("id",sa.Uuid(),primary_key=True),sa.Column("dataset_id",sa.String(128),nullable=False),sa.Column("dataset_version",sa.String(64),nullable=False),sa.Column("generator_version",sa.String(64),nullable=False),sa.Column("seed",sa.Integer(),nullable=False),sa.Column("case_count",sa.Integer(),nullable=False),sa.Column("payload_hash",sa.String(64),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("dataset_id","dataset_version",name="uq_synthetic_datasets_identity"))
def downgrade(): op.drop_table("synthetic_datasets")
