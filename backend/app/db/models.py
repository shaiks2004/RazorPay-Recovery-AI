from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("merchant_id", "razorpay_event_id", name="uq_webhook_events_merchant_event"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    razorpay_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_status: Mapped[str] = mapped_column(String(64), nullable=False, default="PENDING")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("merchant_id", "razorpay_order_id", name="uq_orders_merchant_razorpay_order"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    razorpay_order_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[int | None] = mapped_column(Integer)
    amount_paid: Mapped[int | None] = mapped_column(Integer)
    amount_due: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(8))
    receipt: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNKNOWN")
    source_webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("webhook_events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("merchant_id", "razorpay_payment_id", name="uq_payments_merchant_razorpay_payment"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    razorpay_payment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    razorpay_order_id: Mapped[str | None] = mapped_column(String(255), index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    method: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNKNOWN")
    error_code: Mapped[str | None] = mapped_column(String(255))
    error_reason: Mapped[str | None] = mapped_column(String(255))
    source_webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("webhook_events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (UniqueConstraint("payment_id", name="uq_payment_attempts_payment"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"), nullable=False)
    first_webhook_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_events.id"), nullable=False)
    last_webhook_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_events.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNKNOWN")
    attempt_source: Mapped[str] = mapped_column(String(32), nullable=False, default="RAZORPAY_WEBHOOK")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class RecoveryCase(Base):
    __tablename__ = "recovery_cases"
    __table_args__ = (
        Index(
            "uq_recovery_cases_active_payment",
            "merchant_id",
            "original_payment_id",
            unique=True,
            postgresql_where=text("status IN ('NEW', 'ASSESSING')"),
            sqlite_where=text("status IN ('NEW', 'ASSESSING')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_reference: Mapped[str | None] = mapped_column(String(255))
    original_payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="RAZORPAY_TEST")
    initial_error_code: Mapped[str | None] = mapped_column(String(255))
    initial_error_reason: Mapped[str | None] = mapped_column(String(255))
    eligibility_result: Mapped[str] = mapped_column(String(64), nullable=False)
    eligibility_reason_codes: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="NEW")
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class RecoveryOutcome(Base):
    __tablename__ = "recovery_outcomes"
    __table_args__ = (UniqueConstraint("recovery_case_id", name="uq_recovery_outcomes_case"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id"), nullable=False)
    observed_payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"))
    source_webhook_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_events.id"), nullable=False)
    outcome_type: Mapped[str] = mapped_column(String(64), nullable=False)
    attribution_status: Mapped[str] = mapped_column(String(64), nullable=False)
    attributed_intervention_reference: Mapped[str | None] = mapped_column(String(255))
    execution_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("execution_requests.id"))
    razorpay_payment_link_id: Mapped[str | None] = mapped_column(String(255))
    attributed_amount_minor_units: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(8))
    recovery_attribution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recovery_attributions.id"), unique=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class RecoveryAssessment(Base):
    __tablename__ = "recovery_assessments"
    __table_args__ = (UniqueConstraint("recovery_case_id", "score_version", name="uq_recovery_assessments_case_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id"), nullable=False)
    diagnosis: Mapped[str] = mapped_column(String(64), nullable=False)
    diagnosis_confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    recovery_probability: Mapped[object] = mapped_column(Numeric(5, 4), nullable=False)
    payment_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_recovery_value: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    feature_snapshot: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class MerchantPolicy(Base):
    __tablename__ = "merchant_policies"
    __table_args__ = (UniqueConstraint("merchant_id", "policy_version", name="uq_merchant_policies_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allowed_actions: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    max_amount_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    min_recovery_probability: Mapped[object] = mapped_column(Numeric(5, 4), nullable=False)
    min_expected_recovery_value_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_recovery_budget_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"
    __table_args__ = (UniqueConstraint("recovery_case_id", "assessment_id", "policy_version", name="uq_policy_decisions_assessment_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id"), nullable=False)
    assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_assessments.id"), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_action: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    evaluated_facts: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    authorized_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ExecutionRequest(Base):
    __tablename__ = "execution_requests"
    __table_args__ = (UniqueConstraint("policy_decision_id", name="uq_execution_requests_policy_decision"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id"), nullable=False)
    policy_decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy_decisions.id"), nullable=False)
    assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_assessments.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    reference_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    razorpay_payment_link_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    amount_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_metadata: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class PaymentLink(Base):
    """Provider facts about a Razorpay Payment Link; never inferred from an order."""
    __tablename__ = "payment_links"
    __table_args__ = (
        UniqueConstraint("merchant_id", "razorpay_payment_link_id", name="uq_payment_links_merchant_link"),
        UniqueConstraint("captured_payment_id", name="uq_payment_links_captured_payment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("execution_requests.id"), unique=True)
    razorpay_payment_link_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(40), nullable=False)
    razorpay_order_id: Mapped[str | None] = mapped_column(String(255))
    captured_payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"))
    amount_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    source_webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("webhook_events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class RecoveryAttribution(Base):
    """Append-only proof that one captured payment came through one RECOVER link."""
    __tablename__ = "recovery_attributions"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_recovery_attributions_payment"),
        UniqueConstraint("execution_request_id", "payment_id", name="uq_recovery_attributions_execution_payment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("execution_requests.id"), nullable=False)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id"), nullable=False)
    policy_decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy_decisions.id"), nullable=False)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"), nullable=False)
    razorpay_payment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    razorpay_payment_link_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attribution_status: Mapped[str] = mapped_column(String(16), nullable=False)
    attributed_amount_minor_units: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    source_webhook_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_events.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (UniqueConstraint("run_name", "dataset_id", "dataset_version", name="uq_evaluation_runs_dataset"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="local-test-merchant", index=True)
    run_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CREATED")
    configuration_snapshot: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class EvaluationRunCase(Base):
    __tablename__ = "evaluation_run_cases"
    __table_args__ = (UniqueConstraint("evaluation_run_id", "recovery_case_id", name="uq_evaluation_run_case"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_runs.id"), nullable=False)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id"), nullable=False)
    source_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (UniqueConstraint("evaluation_run_id", "schema_version", name="uq_metric_snapshots_run_schema"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_runs.id"), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AIAdvisory(Base):
    __tablename__ = "ai_advisories"
    __table_args__ = (UniqueConstraint("recovery_case_id", "assessment_id", "prompt_version", "model", name="uq_ai_advisories_assessment_prompt_model"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id"), nullable=False)
    assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_assessments.id"), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    diagnosis_label: Mapped[str | None] = mapped_column(String(64))
    recommended_action: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[object | None] = mapped_column(Numeric(4, 3))
    rationale_codes: Mapped[list | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    explanation: Mapped[str | None] = mapped_column(Text)
    uncertainty: Mapped[list | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SyntheticDataset(Base):
    __tablename__ = "synthetic_datasets"
    __table_args__ = (UniqueConstraint("dataset_id", "dataset_version", name="uq_synthetic_datasets_identity"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class JobQueue(Base):
    __tablename__ = "job_queue"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_job_queue_dedupe_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(128), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON().with_variant(JSONB, "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
