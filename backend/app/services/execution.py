from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, ExecutionRequest, Order, Payment, PolicyDecision, RecoveryAssessment, RecoveryCase
from app.domain.execution import ExecutionStatus
from app.integrations.razorpay_gateway import RazorpayGateway, RazorpayUnknownResult, RazorpayValidationError
from app.services.recovery_attribution import record_execution_payment_link


def request_execution(*, session: Session, policy_decision_id, now: datetime, expiry_seconds: int) -> ExecutionRequest | None:
    decision = session.scalar(select(PolicyDecision).where(PolicyDecision.id == policy_decision_id).with_for_update())
    if decision is None:
        return None
    existing = session.scalar(select(ExecutionRequest).where(ExecutionRequest.policy_decision_id == decision.id).with_for_update())
    if existing is not None:
        return existing
    recovery_case = session.scalar(select(RecoveryCase).where(RecoveryCase.id == decision.recovery_case_id).with_for_update())
    assessment = session.get(RecoveryAssessment, decision.assessment_id)
    payment = session.get(Payment, recovery_case.original_payment_id) if recovery_case else None
    order = session.get(Order, recovery_case.order_id) if recovery_case and recovery_case.order_id else None
    block = _validate(decision, recovery_case, assessment, payment, order, expiry_seconds)
    request_id = __import__("uuid").uuid4()
    reference_id = f"RC{request_id.hex[:30]}"  # 32 chars, below Razorpay's documented 40-char limit.
    payload = {"amount": recovery_case.amount if recovery_case else 0, "currency": recovery_case.currency if recovery_case else "", "reference_id": reference_id, "description": "RECOVER payment request", "expire_by": int((_utc(now) + timedelta(seconds=expiry_seconds)).timestamp()), "reminder_enable": False}
    request = ExecutionRequest(id=request_id, merchant_id=decision.merchant_id, recovery_case_id=decision.recovery_case_id, policy_decision_id=decision.id, assessment_id=decision.assessment_id, action=decision.candidate_action, status=ExecutionStatus.BLOCKED.value if block else ExecutionStatus.REQUESTED.value, idempotency_key=f"execution:{decision.id}", reference_id=reference_id, amount_minor_units=payload["amount"], currency=payload["currency"], expires_at=_utc(now) + timedelta(seconds=expiry_seconds), request_payload_hash=_hash(payload), failure_code=block)
    session.add(request)
    session.flush()
    _audit(session, request, "EXECUTION_BLOCKED" if block else "EXECUTION_REQUESTED", {"reason": block} if block else {})
    return request


def execute(*, session: Session, execution_id, gateway: RazorpayGateway, now: datetime) -> ExecutionRequest | None:
    request = session.scalar(select(ExecutionRequest).where(ExecutionRequest.id == execution_id).with_for_update())
    if request is None or request.status in (ExecutionStatus.AWAITING_PAYMENT.value, ExecutionStatus.BLOCKED.value, ExecutionStatus.FAILED.value, ExecutionStatus.RECONCILING.value):
        return request
    decision = session.get(PolicyDecision, request.policy_decision_id)
    recovery_case = session.get(RecoveryCase, request.recovery_case_id)
    assessment = session.get(RecoveryAssessment, request.assessment_id)
    payment = session.get(Payment, recovery_case.original_payment_id) if recovery_case else None
    order = session.get(Order, recovery_case.order_id) if recovery_case and recovery_case.order_id else None
    block = _validate(decision, recovery_case, assessment, payment, order, 1)
    if block or request.amount_minor_units != (decision.authorized_amount if decision else -1) or request.currency != (recovery_case.currency if recovery_case else ""):
        request.status, request.failure_code = ExecutionStatus.BLOCKED.value, block or "EXECUTION_AMOUNT_OR_CURRENCY_MISMATCH"
        _audit(session, request, "EXECUTION_BLOCKED", {"reason": request.failure_code})
        return request
    request.status = ExecutionStatus.EXECUTING.value
    payload = {"amount": request.amount_minor_units, "currency": request.currency, "reference_id": request.reference_id, "description": "RECOVER payment request", "expire_by": int(request.expires_at.timestamp()), "reminder_enable": False}
    try:
        remote = gateway.create_standard_payment_link(payload)
    except RazorpayUnknownResult:
        request.status, request.failure_code = ExecutionStatus.RECONCILING.value, "UNKNOWN_REMOTE_RESULT"
        _audit(session, request, "EXECUTION_RECONCILIATION_STARTED", {})
        return request
    except RazorpayValidationError as error:
        request.status, request.failure_code = ExecutionStatus.FAILED.value, str(error)
        _audit(session, request, "EXECUTION_FAILED", {"reason": request.failure_code})
        return request
    if remote.reference_id != request.reference_id or remote.amount != request.amount_minor_units or remote.currency != request.currency:
        request.status, request.failure_code = ExecutionStatus.RECONCILING.value, "REMOTE_RESPONSE_MISMATCH"
        _audit(session, request, "EXECUTION_RECONCILIATION_STARTED", {"reason": request.failure_code})
        return request
    request.status, request.razorpay_payment_link_id = ExecutionStatus.AWAITING_PAYMENT.value, remote.link_id
    request.response_metadata = {"short_url": remote.short_url, "status": remote.status, "reference_id": remote.reference_id}
    record_execution_payment_link(session=session, request=request, now=now)
    _audit(session, request, "RAZORPAY_PAYMENT_LINK_CREATED", {"razorpay_payment_link_id": remote.link_id})
    return request


def reconcile(*, session: Session, execution_id, gateway: RazorpayGateway) -> ExecutionRequest | None:
    """Read-only provider reconciliation. Never creates a second link from an unknown outcome."""
    request = session.scalar(select(ExecutionRequest).where(ExecutionRequest.id == execution_id).with_for_update())
    if request is None or request.status != ExecutionStatus.RECONCILING.value:
        return request
    if not request.razorpay_payment_link_id:
        _audit(session, request, "EXECUTION_RECONCILIATION_STARTED", {"reason": "REFERENCE_ONLY_LOOKUP_UNAVAILABLE"})
        return request
    try:
        remote = gateway.fetch_payment_link(request.razorpay_payment_link_id)
    except (RazorpayUnknownResult, RazorpayValidationError):
        _audit(session, request, "EXECUTION_RECONCILIATION_STARTED", {"reason": "REMOTE_LINK_UNAVAILABLE"})
        return request
    if remote.reference_id != request.reference_id or remote.amount != request.amount_minor_units or remote.currency != request.currency:
        request.failure_code = "REMOTE_RESPONSE_MISMATCH"
        _audit(session, request, "EXECUTION_RECONCILIATION_STARTED", {"reason": request.failure_code})
        return request
    request.status = ExecutionStatus.AWAITING_PAYMENT.value
    request.response_metadata = {"short_url": remote.short_url, "status": remote.status, "reference_id": remote.reference_id}
    request.razorpay_payment_link_id = remote.link_id
    record_execution_payment_link(session=session, request=request, now=datetime.now(timezone.utc))
    _audit(session, request, "EXECUTION_RECONCILED", {"razorpay_payment_link_id": remote.link_id})
    return request


def _validate(decision, recovery_case, assessment, payment, order, expiry_seconds: int) -> str | None:
    if decision is None or decision.decision != "APPROVE": return "EXECUTION_BLOCKED_APPROVAL_INVALID"
    if decision.candidate_action != "PREPARE_PAYMENT_LINK" or assessment is None or decision.assessment_id != assessment.id: return "EXECUTION_BLOCKED_APPROVAL_INVALID"
    if recovery_case is None or recovery_case.status != "ASSESSING": return "EXECUTION_BLOCKED_CASE_INACTIVE"
    if payment is None or payment.status == "CAPTURED": return "EXECUTION_BLOCKED_PAYMENT_CAPTURED"
    if order is not None and order.status == "PAID": return "EXECUTION_BLOCKED_ORDER_PAID"
    if recovery_case.amount <= 0 or decision.authorized_amount != recovery_case.amount: return "EXECUTION_AMOUNT_MISMATCH"
    if not recovery_case.currency: return "EXECUTION_CURRENCY_MISMATCH"
    if expiry_seconds <= 0: return "EXECUTION_BLOCKED_MISSING_EXPIRY"
    return None


def _audit(session: Session, request: ExecutionRequest, action: str, metadata: dict) -> None:
    session.add(AuditLog(actor="execution_boundary", action=action, entity_type="execution_request", entity_id=request.id, merchant_id=request.merchant_id, correlation_id=str(request.policy_decision_id), metadata_json={"execution_request_id": str(request.id), "reference_id": request.reference_id, "status": request.status, **metadata}))


def _hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
