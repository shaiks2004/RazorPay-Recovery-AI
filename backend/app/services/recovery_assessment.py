from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Order, Payment, RecoveryCase, RecoveryAssessment

SCORE_VERSION = "deterministic-v1"
# Scores are bounded [0,1]. The baseline represents an unpaid failed attempt with no
# diagnosis; documented error taxonomy and payment facts move it predictably. Changes
# require a new SCORE_VERSION so historical financial assessments stay immutable.
STALE_CASE_SECONDS = 72 * 60 * 60
WEIGHTS = {
    "BASE": Decimal("0.50"),
    "TRANSIENT_FAILURE": Decimal("0.25"),
    "AUTHENTICATION_REQUIRED": Decimal("0.20"),
    "CUSTOMER_ABANDONMENT": Decimal("-0.10"),
    "PAYMENT_INSTRUMENT_FAILURE": Decimal("-0.30"),
    "UNKNOWN_FAILURE": Decimal("-0.15"),
    "HAS_ORDER": Decimal("0.05"),
    "METHOD_AVAILABLE": Decimal("0.05"),
    "ERROR_TAXONOMY_PRESENT": Decimal("0.05"),
    "STALE_CASE": Decimal("-0.20"),
}

TRANSIENT_REASONS = frozenset({"bank_technical_error", "bank_not_available", "bank_cutoff_in_progress", "payment_timed_out", "payment_declined_due_to_high_traffic"})
AUTH_REASONS = frozenset({"authentication_failed", "incorrect_otp", "otp_expired", "otp_attempts_exceeded"})
ABANDONMENT_REASONS = frozenset({"payment_cancelled", "payment_session_expired"})
INSTRUMENT_REASONS = frozenset({"bank_account_invalid", "bank_account_validation_failed", "invalid_vpa", "card_not_enrolled", "international_transaction_not_allowed"})


@dataclass(frozen=True)
class Diagnosis:
    label: str
    confidence: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryFeatures:
    payment_amount: int
    currency: str
    payment_status: str
    error_code_present: bool
    error_reason_present: bool
    has_order: bool
    order_paid: bool
    time_since_failure_seconds: int
    payment_method_available: bool
    failure_repeat_count: int
    existing_case_age_seconds: int

    def snapshot(self) -> dict[str, object]:
        return {
            "payment_amount": self.payment_amount,
            "currency": self.currency,
            "payment_status": self.payment_status,
            "error_code_present": self.error_code_present,
            "error_reason_present": self.error_reason_present,
            "has_order": self.has_order,
            "order_paid": self.order_paid,
            "time_since_failure_seconds": self.time_since_failure_seconds,
            "payment_method_available": self.payment_method_available,
            "failure_repeat_count": self.failure_repeat_count,
            "existing_case_age_seconds": self.existing_case_age_seconds,
        }


class AssessmentNotEligible(Exception):
    """Assessment is not safe for a non-active/non-failed recovery case."""


def diagnose(payment: Payment) -> Diagnosis:
    reason = (payment.error_reason or "").lower()
    if not payment.error_code and not payment.error_reason:
        return Diagnosis("INSUFFICIENT_FACTS", "LOW", ("ERROR_TAXONOMY_MISSING",))
    if reason in TRANSIENT_REASONS:
        return Diagnosis("TRANSIENT_FAILURE", "HIGH", ("DOCUMENTED_TRANSIENT_ERROR_REASON",))
    if reason in AUTH_REASONS:
        return Diagnosis("AUTHENTICATION_REQUIRED", "HIGH", ("DOCUMENTED_AUTHENTICATION_ERROR_REASON",))
    if reason in ABANDONMENT_REASONS:
        return Diagnosis("CUSTOMER_ABANDONMENT", "MEDIUM", ("DOCUMENTED_CUSTOMER_CANCEL_OR_EXPIRY",))
    if reason in INSTRUMENT_REASONS:
        return Diagnosis("PAYMENT_INSTRUMENT_FAILURE", "HIGH", ("DOCUMENTED_PAYMENT_INSTRUMENT_ERROR_REASON",))
    return Diagnosis("UNKNOWN_FAILURE", "LOW", ("UNMAPPED_RAZORPAY_ERROR_TAXONOMY",))


def extract_features(*, recovery_case: RecoveryCase, payment: Payment, order: Order | None, now: datetime) -> RecoveryFeatures:
    created_at = _as_utc(recovery_case.created_at)
    age_seconds = max(0, int((_as_utc(now) - created_at).total_seconds()))
    return RecoveryFeatures(
        payment_amount=payment.amount,
        currency=payment.currency,
        payment_status=payment.status,
        error_code_present=payment.error_code is not None,
        error_reason_present=payment.error_reason is not None,
        has_order=order is not None,
        order_paid=order.status == "PAID" if order is not None else False,
        time_since_failure_seconds=age_seconds,
        payment_method_available=payment.method is not None,
        failure_repeat_count=1,
        existing_case_age_seconds=age_seconds,
    )


def assess_case(*, session: Session, recovery_case_id, now: datetime) -> RecoveryAssessment | None:
    """Persist one immutable deterministic assessment for the active score version."""
    existing = session.scalar(
        select(RecoveryAssessment)
        .where(RecoveryAssessment.recovery_case_id == recovery_case_id, RecoveryAssessment.score_version == SCORE_VERSION)
        .with_for_update()
    )
    if existing is not None:
        return existing
    recovery_case = session.scalar(select(RecoveryCase).where(RecoveryCase.id == recovery_case_id).with_for_update())
    if recovery_case is None:
        raise AssessmentNotEligible("recovery case missing")
    if recovery_case.status != "ASSESSING":
        return None
    payment = session.get(Payment, recovery_case.original_payment_id)
    if payment is None or payment.status != "FAILED":
        return None
    order = session.get(Order, recovery_case.order_id) if recovery_case.order_id else None
    if order is not None and order.status == "PAID":
        return None
    if payment.amount <= 0:
        return None

    diagnosis = diagnose(payment)
    features = extract_features(recovery_case=recovery_case, payment=payment, order=order, now=now)
    probability, score_codes = score(diagnosis=diagnosis, features=features)
    expected_value = expected_recovery_value(amount=payment.amount, probability=probability)
    action, action_codes = recommend_action(diagnosis=diagnosis, probability=probability, features=features)
    reason_codes = list(diagnosis.reason_codes + tuple(score_codes) + tuple(action_codes))
    assessment = RecoveryAssessment(
        recovery_case_id=recovery_case.id,
        diagnosis=diagnosis.label,
        diagnosis_confidence=diagnosis.confidence,
        recovery_probability=probability,
        payment_amount=payment.amount,
        expected_recovery_value=expected_value,
        candidate_action=action,
        reason_codes=reason_codes,
        feature_snapshot=features.snapshot(),
        score_version=SCORE_VERSION,
    )
    session.add(assessment)
    session.flush()
    _audit(session, recovery_case, "RECOVERY_DIAGNOSED", {"assessment_id": str(assessment.id), "diagnosis": diagnosis.label, "reason_codes": list(diagnosis.reason_codes)})
    _audit(session, recovery_case, "RECOVERY_FEATURES_EXTRACTED", {"assessment_id": str(assessment.id), "feature_names": sorted(features.snapshot())})
    _audit(session, recovery_case, "RECOVERY_SCORED", {"assessment_id": str(assessment.id), "score_version": SCORE_VERSION, "reason_codes": list(score_codes)})
    _audit(session, recovery_case, "RECOVERY_ACTION_RECOMMENDED", {"assessment_id": str(assessment.id), "candidate_action": action, "reason_codes": list(action_codes)})
    return assessment


def score(*, diagnosis: Diagnosis, features: RecoveryFeatures) -> tuple[Decimal, tuple[str, ...]]:
    points = WEIGHTS["BASE"]
    codes = ["BASE_SCORE"]
    if diagnosis.label in WEIGHTS:
        points += WEIGHTS[diagnosis.label]
        codes.append(f"DIAGNOSIS_{diagnosis.label}")
    if features.has_order and not features.order_paid:
        points += WEIGHTS["HAS_ORDER"]
        codes.append("UNPAID_ORDER_PRESENT")
    if features.payment_method_available:
        points += WEIGHTS["METHOD_AVAILABLE"]
        codes.append("PAYMENT_METHOD_PRESENT")
    if features.error_code_present and features.error_reason_present:
        points += WEIGHTS["ERROR_TAXONOMY_PRESENT"]
        codes.append("ERROR_TAXONOMY_PRESENT")
    if features.existing_case_age_seconds >= STALE_CASE_SECONDS:
        points += WEIGHTS["STALE_CASE"]
        codes.append("STALE_CASE")
    return max(Decimal("0.00"), min(Decimal("1.00"), points)).quantize(Decimal("0.0001")), tuple(codes)


def expected_recovery_value(*, amount: int, probability: Decimal) -> int:
    if amount <= 0:
        return 0
    return int((Decimal(amount) * probability).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def recommend_action(*, diagnosis: Diagnosis, probability: Decimal, features: RecoveryFeatures) -> tuple[str, tuple[str, ...]]:
    if diagnosis.label == "INSUFFICIENT_FACTS":
        return "WAIT_FOR_MORE_FACTS", ("INSUFFICIENT_DIAGNOSIS_FACTS",)
    if features.existing_case_age_seconds >= STALE_CASE_SECONDS:
        return "ESCALATE_FOR_REVIEW", ("STALE_CASE_REQUIRES_REVIEW",)
    if probability >= Decimal("0.65"):
        return "PREPARE_PAYMENT_LINK", ("HIGH_DETERMINISTIC_RECOVERY_SCORE",)
    if probability <= Decimal("0.25"):
        return "DO_NOTHING", ("LOW_DETERMINISTIC_RECOVERY_SCORE",)
    return "ESCALATE_FOR_REVIEW", ("MID_RANGE_SCORE_REQUIRES_REVIEW",)


def _audit(session: Session, recovery_case: RecoveryCase, action: str, metadata: dict) -> None:
    session.add(AuditLog(actor="assessment_engine", action=action, entity_type="recovery_case", entity_id=recovery_case.id, merchant_id=recovery_case.merchant_id, correlation_id=recovery_case.correlation_id, metadata_json=metadata))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
