from __future__ import annotations

import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    AIAdvisory,
    AuditLog,
    ExecutionRequest,
    MerchantPolicy,
    Order,
    Payment,
    PaymentAttempt,
    PolicyDecision,
    RecoveryAssessment,
    RecoveryAttribution,
    RecoveryCase,
    RecoveryOutcome,
    WebhookEvent,
)
from app.domain.policy import PolicyDecisionKind
from app.services.execution import request_execution
from app.services.policy_decision import evaluate_and_persist
from app.services.recovery_assessment import assess_case

DEMO_SOURCE = "SYNTHETIC_DEMO"
DEMO_PREFIX = "demo-20260906-"
DEMO_COUNT = 300
DEMO_SEED = 20260906
DEMO_PROMPT_VERSION = "synthetic-demo-v1"
DEMO_SCHEMA_VERSION = "synthetic-demo-v1"

REASONS = {
    "TRANSIENT_FAILURE": ("GATEWAY_ERROR", "bank_technical_error"),
    "AUTHENTICATION_REQUIRED": ("BAD_REQUEST_ERROR", "incorrect_otp"),
    "CUSTOMER_ABANDONMENT": ("PAYMENT_CANCELLED", "payment_cancelled"),
    "PAYMENT_INSTRUMENT_FAILURE": ("BAD_REQUEST_ERROR", "bank_account_invalid"),
    "INSUFFICIENT_FACTS": (None, None),
    "UNKNOWN_FAILURE": ("SERVER_ERROR", "provider_specific_future_reason"),
}
CATEGORIES = (
    "TRANSIENT_FAILURE",
    "AUTHENTICATION_REQUIRED",
    "CUSTOMER_ABANDONMENT",
    "PAYMENT_INSTRUMENT_FAILURE",
    "INSUFFICIENT_FACTS",
    "UNKNOWN_FAILURE",
)
AMOUNTS = (4900, 9900, 14900, 19900, 29900, 49900, 79900, 99900, 149900, 249900, 499900, 999900)
METHODS = ("card", "upi", "netbanking", "wallet")


@dataclass(frozen=True)
class DemoSeedResult:
    generated: int
    assessments: int
    advisories: int
    approved: int
    denied: int
    escalated: int
    execution_requests: int
    verified_revenue_minor_units: int


class DemoDataUnavailable(Exception):
    pass


def demo_data_available(settings: Settings) -> bool:
    return settings.razorpay_mode.lower() == "test" and settings.merchant_id in {"local-test-merchant", "merchant-test"}


def seed_demo_data(*, session: Session, settings: Settings, now: datetime | None = None, count: int = DEMO_COUNT, seed: int = DEMO_SEED) -> DemoSeedResult:
    if not demo_data_available(settings):
        raise DemoDataUnavailable("synthetic demo data is available only for the local test merchant in Test Mode")
    if count != DEMO_COUNT:
        raise ValueError(f"demo seeding requires exactly {DEMO_COUNT} records")
    clear_demo_data(session=session, merchant_id=settings.merchant_id)
    current = _utc(now or datetime.now(timezone.utc))
    rng = random.Random(seed)
    policy = session.scalar(select(MerchantPolicy).where(MerchantPolicy.merchant_id == settings.merchant_id).order_by(MerchantPolicy.created_at.desc()))
    generated_cases: list[RecoveryCase] = []

    for index in range(count):
        category = CATEGORIES[index % len(CATEGORIES)]
        error_code, error_reason = REASONS[category]
        amount = rng.choice(AMOUNTS)
        created_at = current - timedelta(minutes=rng.randint(20, 45 * 24 * 60))
        event_id = f"{DEMO_PREFIX}event-{index:04d}"
        event_uuid = uuid.uuid5(uuid.NAMESPACE_URL, event_id)
        payment_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:payment")
        order_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:order")
        case_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:case")
        order_id = f"order_demo_{index:04d}"
        payment_id = f"pay_demo_{index:04d}"
        order = Order(id=order_uuid, merchant_id=settings.merchant_id, razorpay_order_id=order_id, amount=amount, amount_paid=0, amount_due=amount, currency="INR", receipt=f"demo-receipt-{index:04d}", status="UNKNOWN", created_at=created_at, updated_at=created_at)
        payment = Payment(id=payment_uuid, merchant_id=settings.merchant_id, razorpay_payment_id=payment_id, order_id=order_uuid, razorpay_order_id=order_id, amount=amount, currency="INR", method=None if category == "INSUFFICIENT_FACTS" else rng.choice(METHODS), status="FAILED", error_code=error_code, error_reason=error_reason, created_at=created_at, updated_at=created_at)
        body = _payload(payment_id=payment_id, order_id=order_id, amount=amount, error_code=error_code, error_reason=error_reason, method=payment.method)
        event = WebhookEvent(id=event_uuid, merchant_id=settings.merchant_id, razorpay_event_id=event_id, event_name="payment.failed", raw_body=json.dumps(body, sort_keys=True).encode(), payload_json=body, signature_valid=True, validation_status="SYNTHETIC_DEMO", processing_status="PROJECTED", received_at=created_at, processed_at=created_at, created_at=created_at, updated_at=created_at)
        attempt = PaymentAttempt(payment_id=payment_uuid, first_webhook_event_id=event_uuid, last_webhook_event_id=event_uuid, status="FAILED", attempt_source=DEMO_SOURCE, created_at=created_at, updated_at=created_at)
        case = RecoveryCase(id=case_uuid, merchant_id=settings.merchant_id, customer_reference=f"demo-customer-{index:04d}", original_payment_id=payment_uuid, order_id=order_uuid, amount=amount, currency="INR", source=DEMO_SOURCE, initial_error_code=error_code, initial_error_reason=error_reason, eligibility_result="ELIGIBLE", eligibility_reason_codes=["FAILED_PAYMENT_UNPAID", "SYNTHETIC_DEMO"], status="ASSESSING", correlation_id=f"{DEMO_PREFIX}case-{index:04d}", created_at=created_at, updated_at=created_at)
        session.add_all((order, payment, event, case))
        session.flush()
        session.add(attempt)
        generated_cases.append(case)
        session.add(AuditLog(actor="synthetic_demo_seeder", action="WEBHOOK_PROJECTED", entity_type="webhook_event", entity_id=event_uuid, merchant_id=settings.merchant_id, correlation_id=event_id, metadata_json={"synthetic_demo": True, "source": DEMO_SOURCE, "event_name": "payment.failed"}, created_at=created_at))
        session.add(AuditLog(actor="synthetic_demo_seeder", action="RECOVERY_CASE_CREATED", entity_type="recovery_case", entity_id=case_uuid, merchant_id=settings.merchant_id, correlation_id=case.correlation_id, metadata_json={"synthetic_demo": True, "source": DEMO_SOURCE}, created_at=created_at))

    session.flush()
    assessments: list[RecoveryAssessment] = []
    # Keep roughly two thirds as assessment-only operational work; run the policy gate
    # on the remainder so the existing merchant policy, not the demo generator, decides.
    policy_indices = set(range(0, count, 3)) | set(range(1, count, 10))
    for index, case in enumerate(generated_cases):
        assessment = assess_case(session=session, recovery_case_id=case.id, now=current)
        if assessment is None:
            continue
        assessments.append(assessment)
        _mark_demo_assessment(session, assessment, case.merchant_id)
        _synthetic_advisory(session=session, case=case, assessment=assessment, index=index, created_at=assessment.created_at)
        if index in policy_indices and policy is not None:
            decision = evaluate_and_persist(session=session, recovery_case_id=case.id, assessment_id=assessment.id, now=current)
            if decision is not None:
                _mark_demo_policy(session, decision)
                if decision.decision == PolicyDecisionKind.APPROVE.value and index % 12 == 0:
                    request = request_execution(session=session, policy_decision_id=decision.id, now=current, expiry_seconds=settings.payment_link_expiry_seconds)
                    if request is not None:
                        _mark_demo_execution(session, request)
    session.flush()
    decisions = list(session.scalars(select(PolicyDecision).where(PolicyDecision.merchant_id == settings.merchant_id, PolicyDecision.created_at >= current - timedelta(days=46))))
    executions = list(session.scalars(select(ExecutionRequest).where(ExecutionRequest.merchant_id == settings.merchant_id, ExecutionRequest.created_at >= current - timedelta(days=46))))
    return DemoSeedResult(generated=count, assessments=len(assessments), advisories=len(assessments), approved=sum(x.decision == "APPROVE" for x in decisions if _is_demo(x)), denied=sum(x.decision == "DENY" for x in decisions if _is_demo(x)), escalated=sum(x.decision == "ESCALATE" for x in decisions if _is_demo(x)), execution_requests=sum(_is_demo(x) for x in executions), verified_revenue_minor_units=0)


def clear_demo_data(*, session: Session, merchant_id: str) -> int:
    cases = list(session.scalars(select(RecoveryCase).where(RecoveryCase.merchant_id == merchant_id, RecoveryCase.source == DEMO_SOURCE)))
    case_ids = [case.id for case in cases]
    payment_ids = [case.original_payment_id for case in cases]
    event_ids = list(session.scalars(select(WebhookEvent.id).where(WebhookEvent.merchant_id == merchant_id, WebhookEvent.razorpay_event_id.like(f"{DEMO_PREFIX}%"))))
    decision_ids = list(session.scalars(select(PolicyDecision.id).where(PolicyDecision.merchant_id == merchant_id, PolicyDecision.recovery_case_id.in_(case_ids)))) if case_ids else []
    execution_ids = list(session.scalars(select(ExecutionRequest.id).where(ExecutionRequest.merchant_id == merchant_id, ExecutionRequest.recovery_case_id.in_(case_ids)))) if case_ids else []
    attribution_ids = list(session.scalars(select(RecoveryAttribution.id).where(RecoveryAttribution.merchant_id == merchant_id, RecoveryAttribution.recovery_case_id.in_(case_ids)))) if case_ids else []
    outcome_ids = list(session.scalars(select(RecoveryOutcome.id).where(RecoveryOutcome.recovery_case_id.in_(case_ids)))) if case_ids else []
    order_ids = list(session.scalars(select(Order.id).where(Order.merchant_id == merchant_id, Order.razorpay_order_id.like("order_demo_%"))))
    if attribution_ids:
        session.execute(delete(RecoveryAttribution).where(RecoveryAttribution.id.in_(attribution_ids)))
    if outcome_ids:
        session.execute(delete(RecoveryOutcome).where(RecoveryOutcome.id.in_(outcome_ids)))
    if execution_ids:
        session.execute(delete(ExecutionRequest).where(ExecutionRequest.id.in_(execution_ids)))
    if case_ids:
        session.execute(delete(AIAdvisory).where(AIAdvisory.recovery_case_id.in_(case_ids)))
    if decision_ids:
        session.execute(delete(PolicyDecision).where(PolicyDecision.id.in_(decision_ids)))
    if case_ids:
        session.execute(delete(RecoveryAssessment).where(RecoveryAssessment.recovery_case_id.in_(case_ids)))
        audit_ids = case_ids + decision_ids + execution_ids + event_ids
        session.execute(delete(AuditLog).where(AuditLog.merchant_id == merchant_id, or_(AuditLog.correlation_id.like(f"{DEMO_PREFIX}%"), AuditLog.entity_id.in_(audit_ids))))
        session.execute(delete(PaymentAttempt).where(PaymentAttempt.payment_id.in_(payment_ids)))
        session.execute(delete(RecoveryCase).where(RecoveryCase.id.in_(case_ids)))
    if event_ids:
        session.execute(delete(WebhookEvent).where(WebhookEvent.id.in_(event_ids)))
    if payment_ids:
        session.execute(delete(Payment).where(Payment.id.in_(payment_ids)))
    if order_ids:
        session.execute(delete(Order).where(Order.id.in_(order_ids)))
    return len(cases)


def _synthetic_advisory(*, session: Session, case: RecoveryCase, assessment: RecoveryAssessment, index: int, created_at: datetime) -> None:
    status = "UNAVAILABLE" if index % 17 == 0 else "UNCERTAIN" if index % 5 == 0 else "COMPLETED"
    action = assessment.candidate_action
    explanation = f"Synthetic demo advisory for {assessment.diagnosis.lower().replace('_', ' ')}; this is not external model output."
    output = {"diagnosis_label": assessment.diagnosis, "recommended_action": action, "confidence": 0.82 if status == "COMPLETED" else 0.54, "rationale_codes": ["CUSTOMER_INTENT_UNKNOWN"], "explanation": explanation, "uncertainty": ["SYNTHETIC_DEMO_DATA"]}
    session.add(AIAdvisory(recovery_case_id=case.id, assessment_id=assessment.id, schema_version=DEMO_SCHEMA_VERSION, provider="SYNTHETIC_DEMO", model="synthetic-demo-v1", prompt_version=DEMO_PROMPT_VERSION, input_hash=_hash({"case_id": str(case.id), "assessment_id": str(assessment.id)}), output_hash=None if status == "UNAVAILABLE" else _hash(output), diagnosis_label=None if status == "UNAVAILABLE" else assessment.diagnosis, recommended_action=None if status == "UNAVAILABLE" else action, confidence=None if status == "UNAVAILABLE" else output["confidence"], rationale_codes=None if status == "UNAVAILABLE" else output["rationale_codes"], explanation=None if status == "UNAVAILABLE" else explanation, uncertainty=["SYNTHETIC_DEMO_DATA"] if status == "UNAVAILABLE" else output["uncertainty"], status=status, failure_code="SYNTHETIC_ADVISORY_UNAVAILABLE" if status == "UNAVAILABLE" else None, created_at=created_at))
    session.add(AuditLog(actor="synthetic_demo_seeder", action="AI_ADVISORY_COMPLETED" if status != "UNAVAILABLE" else "AI_ADVISORY_FAILED", entity_type="ai_advisory", entity_id=None, merchant_id=case.merchant_id, correlation_id=case.correlation_id, metadata_json={"synthetic_demo": True, "status": status, "provider": "SYNTHETIC_DEMO"}, created_at=created_at))


def _mark_demo_assessment(session: Session, assessment: RecoveryAssessment, merchant_id: str) -> None:
    assessment.reason_codes = list(assessment.reason_codes) + ["SYNTHETIC_DEMO"]
    assessment.feature_snapshot = {**assessment.feature_snapshot, "synthetic_demo": True}
    session.add(AuditLog(actor="synthetic_demo_seeder", action="RECOVERY_SCORED", entity_type="recovery_assessment", entity_id=assessment.id, merchant_id=merchant_id, correlation_id=f"{DEMO_PREFIX}assessment-{assessment.id}", metadata_json={"synthetic_demo": True, "score_version": assessment.score_version}, created_at=assessment.created_at))


def _mark_demo_policy(session: Session, decision: PolicyDecision) -> None:
    decision.reason_codes = list(decision.reason_codes) + ["SYNTHETIC_DEMO"]
    decision.evaluated_facts = {**decision.evaluated_facts, "synthetic_demo": True}


def _mark_demo_execution(session: Session, request: ExecutionRequest) -> None:
    request.failure_reason = "Synthetic demo request; no Razorpay payment link was created."
    session.add(AuditLog(actor="synthetic_demo_seeder", action="EXECUTION_REQUESTED", entity_type="execution_request", entity_id=request.id, merchant_id=request.merchant_id, correlation_id=f"{DEMO_PREFIX}execution-{request.id}", metadata_json={"synthetic_demo": True, "provider_call": False}, created_at=request.created_at))


def _is_demo(record: object) -> bool:
    return bool(getattr(record, "reason_codes", None) and "SYNTHETIC_DEMO" in getattr(record, "reason_codes")) or bool(getattr(record, "failure_reason", None) and "Synthetic demo" in getattr(record, "failure_reason"))


def _payload(*, payment_id: str, order_id: str, amount: int, error_code: str | None, error_reason: str | None, method: str | None) -> dict:
    return {"event": "payment.failed", "payload": {"payment": {"entity": {"id": payment_id, "entity": "payment", "amount": amount, "currency": "INR", "status": "failed", "order_id": order_id, "method": method, "error_code": error_code, "error_reason": error_reason}}}, "synthetic_demo": True}


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
