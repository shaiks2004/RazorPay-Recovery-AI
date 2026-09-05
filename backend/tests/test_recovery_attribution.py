from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.models import PaymentLink, RecoveryAttribution, RecoveryOutcome
from app.integrations.razorpay_gateway import PaymentLinkRemote
from app.services.execution import execute
from app.services.job_worker import JobQueueService, WebhookJobWorker, WorkerSettings
from tests.test_execution import Gateway, approved_execution
from tests.test_payment_projection import enqueue_event


SETTINGS = WorkerSettings(lease_seconds=10, max_attempts=3, retry_base_seconds=1, retry_max_seconds=5)


def paid_payload(request, *, payment_id="pay_recovered", amount=None, currency=None, link_id=None) -> dict:
    amount = request.amount_minor_units if amount is None else amount
    currency = request.currency if currency is None else currency
    link_id = request.razorpay_payment_link_id if link_id is None else link_id
    return {"event": "payment_link.paid", "payload": {
        "payment": {"entity": {"id": payment_id, "entity": "payment", "amount": amount, "currency": currency, "status": "captured", "order_id": "order_recovery", "method": "card"}},
        "order": {"entity": {"id": "order_recovery", "entity": "order", "amount": amount, "amount_paid": amount, "amount_due": 0, "currency": currency, "status": "paid"}},
        "payment_link": {"entity": {"id": link_id, "entity": "payment_link", "reference_id": request.reference_id, "amount": amount, "currency": currency, "status": "paid"}},
    }}


def executed_request(session_factory):
    request = approved_execution(session_factory)
    remote = PaymentLinkRemote("plink_recover", "https://rzp.io/i/recover", "created", request.reference_id, request.amount_minor_units, request.currency)
    with session_factory() as session:
        with session.begin():
            execute(session=session, execution_id=request.id, gateway=Gateway(result=remote), now=datetime.now(timezone.utc))
    with session_factory() as session:
        return session.get(type(request), request.id)


def process(session_factory):
    return WebhookJobWorker(JobQueueService(session_factory, SETTINGS), "attribution-worker").process_once().outcome


def test_signed_payment_link_paid_captured_payment_is_attributed_once(session_factory):
    request = executed_request(session_factory)
    enqueue_event(session_factory, event_name="payment_link.paid", payload=paid_payload(request))
    assert process(session_factory) == "SUCCEEDED"
    with session_factory() as session:
        attribution = session.scalar(select(RecoveryAttribution))
        outcome = session.scalar(select(RecoveryOutcome))
        assert attribution is not None and attribution.attribution_status == "VERIFIED"
        assert attribution.attributed_amount_minor_units == request.amount_minor_units
        assert outcome is not None and outcome.attribution_status == "RECOVERY_INTERVENTION_ATTRIBUTED"
    assert process(session_factory) == "IDLE"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryAttribution)) == 1


def test_same_amount_with_unverified_link_creates_no_attribution(session_factory):
    request = executed_request(session_factory)
    enqueue_event(session_factory, event_name="payment_link.paid", payload=paid_payload(request, link_id="plink_not_ours"))
    assert process(session_factory) == "SUCCEEDED"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryAttribution)) == 0
        assert session.scalar(select(PaymentLink).where(PaymentLink.razorpay_payment_link_id == "plink_not_ours")) is not None


def test_amount_mismatch_is_immutable_rejection(session_factory):
    request = executed_request(session_factory)
    enqueue_event(session_factory, event_name="payment_link.paid", payload=paid_payload(request, amount=request.amount_minor_units + 1))
    assert process(session_factory) == "SUCCEEDED"
    with session_factory() as session:
        attribution = session.scalar(select(RecoveryAttribution))
        assert attribution is not None and attribution.attribution_status == "REJECTED"
        assert attribution.evidence["reason_codes"] == ["ATTRIBUTION_AMOUNT_MISMATCH"]


def test_late_failure_cannot_regress_verified_attribution(session_factory):
    request = executed_request(session_factory)
    enqueue_event(session_factory, event_name="payment_link.paid", payload=paid_payload(request, payment_id="pay_monotonic"))
    assert process(session_factory) == "SUCCEEDED"
    failed = {"event": "payment.failed", "payload": {"payment": {"entity": {"id": "pay_monotonic", "entity": "payment", "amount": request.amount_minor_units, "currency": request.currency, "status": "failed", "order_id": "order_recovery", "method": "card", "error_code": "X", "error_reason": "X"}}}}
    enqueue_event(session_factory, event_name="payment.failed", payload=failed)
    assert process(session_factory) == "SUCCEEDED"
    with session_factory() as session:
        assert session.scalar(select(RecoveryAttribution)).attribution_status == "VERIFIED"
