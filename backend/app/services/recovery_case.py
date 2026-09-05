from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Order, Payment, RecoveryCase, WebhookEvent
from app.domain.recovery_states import ACTIVE_RECOVERY_CASE_STATUSES, advance_recovery_case_status
from app.services.recovery_eligibility import EligibilityDecision, evaluate_recovery_eligibility


@dataclass(frozen=True)
class RecoveryCaseResult:
    decision: EligibilityDecision
    case_id: uuid.UUID | None
    created: bool


def evaluate_and_create_case(*, session: Session, event: WebhookEvent, now: datetime) -> RecoveryCaseResult:
    """Run only after a payment.failed event has been safely projected, in the same transaction."""
    # Sessions intentionally disable autoflush; make the just-derived projection queryable in this transaction.
    session.flush()
    payment = session.scalar(
        select(Payment)
        .where(Payment.merchant_id == event.merchant_id, Payment.source_webhook_event_id == event.id)
        .with_for_update()
    )
    if payment is None:
        return _audit_non_creation(session=session, event=event, decision=EligibilityDecision("INSUFFICIENT_FACTS", ("FAILED_EVENT_NOT_PROJECTED",)))
    order = session.get(Order, payment.order_id) if payment.order_id else None
    active_case = session.scalar(
        select(RecoveryCase)
        .where(
            RecoveryCase.merchant_id == event.merchant_id,
            RecoveryCase.original_payment_id == payment.id,
            RecoveryCase.status.in_(ACTIVE_RECOVERY_CASE_STATUSES),
        )
        .with_for_update()
    )
    decision = evaluate_recovery_eligibility(payment=payment, order=order, has_active_case=active_case is not None)
    if decision.result != "ELIGIBLE":
        return _audit_non_creation(session=session, event=event, decision=decision, case_id=active_case.id if active_case else None)

    recovery_case = RecoveryCase(
        merchant_id=event.merchant_id,
        customer_reference=None,
        original_payment_id=payment.id,
        order_id=payment.order_id,
        amount=payment.amount,
        currency=payment.currency,
        source="RAZORPAY_TEST",
        initial_error_code=payment.error_code,
        initial_error_reason=payment.error_reason,
        eligibility_result=decision.result,
        eligibility_reason_codes=list(decision.reason_codes),
        status="NEW",
        correlation_id=event.razorpay_event_id,
    )
    session.add(recovery_case)
    try:
        session.flush()
    except IntegrityError:
        # The database partial unique index is the final race guard. This transaction will be retried by caller only if needed.
        raise
    recovery_case.status = advance_recovery_case_status("NEW", "ASSESSING")
    session.add(
        AuditLog(
            actor="background_worker",
            action="RECOVERY_CASE_CREATED",
            entity_type="recovery_case",
            entity_id=recovery_case.id,
            merchant_id=recovery_case.merchant_id,
            correlation_id=event.razorpay_event_id,
            metadata_json={"payment_id": str(payment.id), "eligibility_result": decision.result, "reason_codes": list(decision.reason_codes)},
        )
    )
    session.add(
        AuditLog(
            actor="background_worker",
            action="RECOVERY_CASE_ASSESSING",
            entity_type="recovery_case",
            entity_id=recovery_case.id,
            merchant_id=recovery_case.merchant_id,
            correlation_id=event.razorpay_event_id,
            metadata_json={},
        )
    )
    return RecoveryCaseResult(decision=decision, case_id=recovery_case.id, created=True)


def _audit_non_creation(*, session: Session, event: WebhookEvent, decision: EligibilityDecision, case_id: uuid.UUID | None = None) -> RecoveryCaseResult:
    session.add(
        AuditLog(
            actor="background_worker",
            action="RECOVERY_CASE_ELIGIBILITY_EVALUATED",
            entity_type="recovery_case",
            entity_id=case_id,
            merchant_id=event.merchant_id,
            correlation_id=event.razorpay_event_id,
            metadata_json={"eligibility_result": decision.result, "reason_codes": list(decision.reason_codes)},
        )
    )
    return RecoveryCaseResult(decision=decision, case_id=case_id, created=False)
