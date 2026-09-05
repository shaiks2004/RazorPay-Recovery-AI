from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, ExecutionRequest, Payment, PaymentLink, PolicyDecision, RecoveryAttribution, RecoveryCase, RecoveryOutcome, WebhookEvent
from app.domain.execution import ExecutionStatus
from app.domain.recovery_states import advance_recovery_case_status


def record_execution_payment_link(*, session: Session, request: ExecutionRequest, now: datetime) -> PaymentLink:
    """Persist the provider identity at creation time and resolve a prior signed paid event."""
    assert request.razorpay_payment_link_id
    link = session.scalar(select(PaymentLink).where(PaymentLink.merchant_id == request.merchant_id, PaymentLink.razorpay_payment_link_id == request.razorpay_payment_link_id).with_for_update())
    if link is None:
        link = PaymentLink(merchant_id=request.merchant_id, execution_request_id=request.id, razorpay_payment_link_id=request.razorpay_payment_link_id, reference_id=request.reference_id, amount_minor_units=request.amount_minor_units, currency=request.currency, status="CREATED")
        session.add(link)
        session.flush()
    elif link.execution_request_id is None and link.reference_id == request.reference_id:
        link.execution_request_id = request.id
    elif link.execution_request_id != request.id:
        _audit(session, "ATTRIBUTION_CONFLICT", request.id, str(request.policy_decision_id), {"payment_link_id": request.razorpay_payment_link_id, "reason_codes": ["PAYMENT_LINK_EXECUTION_CONFLICT"]})
        return link
    evaluate_link(session=session, link=link, now=now)
    return link


def evaluate_event(*, session: Session, event: WebhookEvent, now: datetime) -> None:
    """Evaluate only a signed payment_link.paid fact. Ordinary payment/order facts lack the link edge."""
    if event.event_name != "payment_link.paid":
        return
    link = session.scalar(select(PaymentLink).where(PaymentLink.merchant_id == event.merchant_id, PaymentLink.source_webhook_event_id == event.id).with_for_update())
    if link is not None:
        evaluate_link(session=session, link=link, now=now)


def evaluate_link(*, session: Session, link: PaymentLink, now: datetime) -> RecoveryAttribution | None:
    if link.captured_payment_id is None:
        return None
    payment = session.scalar(select(Payment).where(Payment.id == link.captured_payment_id).with_for_update())
    execution = session.scalar(select(ExecutionRequest).where(ExecutionRequest.id == link.execution_request_id).with_for_update()) if link.execution_request_id else None
    if payment is None or execution is None:
        return None  # durable link/payment fact remains available for a later execution projection.
    existing = session.scalar(select(RecoveryAttribution).where(RecoveryAttribution.payment_id == payment.id).with_for_update())
    if existing is not None:
        if existing.execution_request_id != execution.id:
            _audit(session, "ATTRIBUTION_CONFLICT", execution.id, str(execution.policy_decision_id), {"payment_id": str(payment.id), "payment_link_id": link.razorpay_payment_link_id, "reason_codes": ["PAYMENT_ALREADY_ATTRIBUTED"]})
        return existing
    recovery_case = session.scalar(select(RecoveryCase).where(RecoveryCase.id == execution.recovery_case_id).with_for_update())
    decision = session.get(PolicyDecision, execution.policy_decision_id)
    reasons = _reasons(link, payment, execution, recovery_case, decision)
    status = "VERIFIED" if not reasons else "REJECTED"
    attribution = RecoveryAttribution(merchant_id=execution.merchant_id, execution_request_id=execution.id, recovery_case_id=execution.recovery_case_id, policy_decision_id=execution.policy_decision_id, payment_id=payment.id, razorpay_payment_id=payment.razorpay_payment_id, razorpay_payment_link_id=link.razorpay_payment_link_id, attribution_status=status, attributed_amount_minor_units=payment.amount if status == "VERIFIED" else None, currency=payment.currency, evidence={"provider_event": "payment_link.paid", "payment_link_id": link.razorpay_payment_link_id, "reference_id": link.reference_id, "payment_id": payment.razorpay_payment_id, "payment_status": payment.status, "reason_codes": reasons}, source_webhook_event_id=link.source_webhook_event_id)
    session.add(attribution)
    session.flush()
    _audit(session, "ATTRIBUTION_EVALUATED", execution.id, str(execution.policy_decision_id), {"payment_id": str(payment.id), "payment_link_id": link.razorpay_payment_link_id, "attribution_status": status, "reason_codes": reasons})
    if status == "REJECTED":
        _audit(session, "ATTRIBUTION_REJECTED", execution.id, str(execution.policy_decision_id), {"payment_id": str(payment.id), "payment_link_id": link.razorpay_payment_link_id, "reason_codes": reasons})
        return attribution
    _close_attributed(session=session, attribution=attribution, payment=payment, execution=execution, recovery_case=recovery_case, link=link, now=now)
    _audit(session, "ATTRIBUTION_VERIFIED", execution.id, str(execution.policy_decision_id), {"payment_id": str(payment.id), "payment_link_id": link.razorpay_payment_link_id, "attributed_amount_minor_units": payment.amount, "currency": payment.currency, "reason_codes": []})
    return attribution


def _reasons(link: PaymentLink, payment: Payment, execution: ExecutionRequest, recovery_case: RecoveryCase | None, decision: PolicyDecision | None) -> list[str]:
    reasons: list[str] = []
    if execution.status != ExecutionStatus.AWAITING_PAYMENT.value: reasons.append("EXECUTION_NOT_CREATED")
    if recovery_case is None or decision is None or recovery_case.merchant_id != execution.merchant_id or decision.merchant_id != execution.merchant_id or payment.merchant_id != execution.merchant_id or link.merchant_id != execution.merchant_id: reasons.append("MERCHANT_MISMATCH")
    if link.execution_request_id != execution.id or link.reference_id != execution.reference_id or link.razorpay_payment_link_id != execution.razorpay_payment_link_id: reasons.append("PAYMENT_LINK_IDENTITY_MISMATCH")
    if payment.status != "CAPTURED": reasons.append("PAYMENT_NOT_CAPTURED")
    if payment.currency != link.currency or payment.currency != execution.currency: reasons.append("ATTRIBUTION_CURRENCY_MISMATCH")
    if payment.amount != link.amount_minor_units or payment.amount != execution.amount_minor_units: reasons.append("ATTRIBUTION_AMOUNT_MISMATCH")
    return reasons


def _close_attributed(*, session: Session, attribution: RecoveryAttribution, payment: Payment, execution: ExecutionRequest, recovery_case: RecoveryCase | None, link: PaymentLink, now: datetime) -> None:
    if recovery_case is None:
        return
    outcome = session.scalar(select(RecoveryOutcome).where(RecoveryOutcome.recovery_case_id == recovery_case.id).with_for_update())
    if outcome is None:
        recovery_case.status = advance_recovery_case_status(recovery_case.status, "CLOSED")
        outcome = RecoveryOutcome(recovery_case_id=recovery_case.id, observed_payment_id=payment.id, source_webhook_event_id=attribution.source_webhook_event_id, outcome_type="PAYMENT_CAPTURED", attribution_status="RECOVERY_INTERVENTION_ATTRIBUTED", attributed_intervention_reference=execution.reference_id, execution_request_id=execution.id, razorpay_payment_link_id=link.razorpay_payment_link_id, attributed_amount_minor_units=payment.amount, currency=payment.currency, recovery_attribution_id=attribution.id, observed_at=now)
        session.add(outcome)
    elif outcome.attribution_status == "CAPTURED_OBSERVED":
        outcome.observed_payment_id = payment.id
        outcome.source_webhook_event_id = attribution.source_webhook_event_id
        outcome.attribution_status = "RECOVERY_INTERVENTION_ATTRIBUTED"
        outcome.attributed_intervention_reference = execution.reference_id
        outcome.execution_request_id = execution.id
        outcome.razorpay_payment_link_id = link.razorpay_payment_link_id
        outcome.attributed_amount_minor_units = payment.amount
        outcome.currency = payment.currency
        outcome.recovery_attribution_id = attribution.id
    _audit(session, "RECOVERY_REVENUE_RECORDED", execution.id, str(execution.policy_decision_id), {"payment_id": str(payment.id), "payment_link_id": link.razorpay_payment_link_id, "attributed_amount_minor_units": payment.amount, "currency": payment.currency, "reason_codes": []})


def _audit(session: Session, action: str, entity_id, correlation_id: str, metadata: dict[str, Any]) -> None:
    session.add(AuditLog(actor="background_worker", action=action, entity_type="recovery_attribution", entity_id=entity_id, correlation_id=correlation_id, metadata_json=metadata))
