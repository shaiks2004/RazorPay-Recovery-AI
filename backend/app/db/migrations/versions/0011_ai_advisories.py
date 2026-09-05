"""persist advisory-only AI responses
Revision ID: 0011_ai_advisories
Revises: 0010_evaluation_metrics
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision="0011_ai_advisories"; down_revision="0010_evaluation_metrics"; branch_labels=None; depends_on=None
def upgrade():
    js=sa.JSON().with_variant(JSONB(),"postgresql")
    op.create_table("ai_advisories", sa.Column("id",sa.Uuid(),primary_key=True),sa.Column("recovery_case_id",sa.Uuid(),sa.ForeignKey("recovery_cases.id"),nullable=False),sa.Column("assessment_id",sa.Uuid(),sa.ForeignKey("recovery_assessments.id"),nullable=False),sa.Column("schema_version",sa.String(32),nullable=False),sa.Column("provider",sa.String(64),nullable=False),sa.Column("model",sa.String(128),nullable=False),sa.Column("prompt_version",sa.String(64),nullable=False),sa.Column("input_hash",sa.String(64),nullable=False),sa.Column("output_hash",sa.String(64)),sa.Column("diagnosis_label",sa.String(64)),sa.Column("recommended_action",sa.String(64)),sa.Column("confidence",sa.Numeric(4,3)),sa.Column("rationale_codes",js),sa.Column("explanation",sa.Text()),sa.Column("uncertainty",js),sa.Column("status",sa.String(32),nullable=False),sa.Column("failure_code",sa.String(64)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("recovery_case_id","assessment_id","prompt_version","model",name="uq_ai_advisories_assessment_prompt_model"))
def downgrade(): op.drop_table("ai_advisories")
