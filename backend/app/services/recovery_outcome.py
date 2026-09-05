from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Payment, RecoveryCase, RecoveryOutcome, WebhookEvent
from app.domain.recovery_states import ACTIVE_RECOVERY_CASE_STATUSES, advance_recovery_case_status


def close_cases_for_outcome(*, session: Session, event: WebhookEvent, now: datetime) -> int:
    """Close active cases only where a captured payment/order proves the underlying obligation is paid.

    Attribution is deliberately CAPTURED_OBSERVED: without a future intervention-to-payment mapping,
    RECOVER must not claim this payment was recovered by its own action.
    """
    if event.event_name not in ("payment.captured", "order.paid"):
        return 0
    # Projection writes occur earlier in this worker transaction with autoflush disabled.
    session.flush()
    payment = session.scalar(
        select(Payment)
        .where(Payment.merchant_id == event.merchant_id, Payment.source_webhook_event_id == event.id)
        .with_for_update()
    )
    if payment is None or payment.status != "CAPTURED":
        return 0

    cases = _candidate_cases(session=session, event=event, payment=payment)
    closed = 0
    for recovery_case in cases:
        existing_outcome = session.scalar(
            select(RecoveryOutcome).where(RecoveryOutcome.recovery_case_id == recovery_case.id).with_for_update()
        )
        if existing_outcome is not None:
            continue
        recovery_case.status = advance_recovery_case_status(recovery_case.status, "CLOSED")
        session.add(
            RecoveryOutcome(
                recovery_case_id=recovery_case.id,
                observed_payment_id=payment.id,
                source_webhook_event_id=event.id,
                outcome_type="PAYMENT_CAPTURED",
                attribution_status="CAPTURED_OBSERVED",
                attributed_intervention_reference=None,
                observed_at=now,
            )
        )
        session.add(
            AuditLog(
                actor="background_worker",
                action="RECOVERY_CASE_CLOSED_CAPTURED_OBSERVED",
                entity_type="recovery_case",
                entity_id=recovery_case.id,
                merchant_id=recovery_case.merchant_id,
                correlation_id=event.razorpay_event_id,
                metadata_json={
                    "observed_payment_id": str(payment.id),
                    "outcome_type": "PAYMENT_CAPTURED",
                    "attribution_status": "CAPTURED_OBSERVED",
                },
            )
        )
        closed += 1
    return closed


def _candidate_cases(*, session: Session, event: WebhookEvent, payment: Payment) -> list[RecoveryCase]:
    conditions = [
        RecoveryCase.merchant_id == event.merchant_id,
        RecoveryCase.status.in_(ACTIVE_RECOVERY_CASE_STATUSES),
    ]
    if event.event_name == "payment.captured":
        conditions.append(RecoveryCase.original_payment_id == payment.id)
    else:
        # order.paid provides a paid order snapshot (amount_due=0, validated during projection),
        # so every active failed attempt associated with that same order must stop.
        if payment.razorpay_order_id is None:
            return []
        return list(
            session.scalars(
                select(RecoveryCase)
                .join(Payment, RecoveryCase.original_payment_id == Payment.id)
                .where(*conditions, Payment.razorpay_order_id == payment.razorpay_order_id)
                .with_for_update()
            )
        )
    return list(session.scalars(select(RecoveryCase).where(*conditions).with_for_update()))
