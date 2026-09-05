from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.models import AuditLog, JobQueue, Payment, RecoveryCase, RecoveryOutcome, WebhookEvent
from app.services.job_worker import JobQueueService, WebhookJobWorker, WorkerSettings


SETTINGS = WorkerSettings(lease_seconds=10, max_attempts=3, retry_base_seconds=1, retry_max_seconds=5)


def payment_payload(event_name: str, *, payment_id: str, order_id: str | None = None) -> dict:
    status = "failed" if event_name == "payment.failed" else "captured"
    return {
        "event": event_name,
        "payload": {"payment": {"entity": {
            "id": payment_id, "entity": "payment", "amount": 50000, "currency": "INR",
            "status": status, "order_id": order_id, "method": "card",
            "error_code": "BAD_REQUEST_ERROR" if status == "failed" else None,
            "error_reason": "payment_failed" if status == "failed" else None,
        }}},
    }


def order_paid_payload(*, payment_id: str, order_id: str) -> dict:
    result = payment_payload("order.paid", payment_id=payment_id, order_id=order_id)
    result["payload"]["order"] = {"entity": {
        "id": order_id, "entity": "order", "amount": 50000, "amount_paid": 50000,
        "amount_due": 0, "currency": "INR", "receipt": "receipt-outcome", "status": "paid",
    }}
    return result


def enqueue(session_factory, *, event_name: str, payload: dict) -> tuple[uuid.UUID, uuid.UUID]:
    event_id, job_id = uuid.uuid4(), uuid.uuid4()
    with session_factory() as session:
        with session.begin():
            session.add(WebhookEvent(
                id=event_id, merchant_id="merchant-test", razorpay_event_id=f"evt_{event_id.hex}",
                event_name=event_name, raw_body=b"verified", payload_json=payload, signature_valid=True,
                validation_status="VALID", processing_status="PENDING",
            ))
            session.add(JobQueue(
                id=job_id, job_type="PROCESS_RAZORPAY_WEBHOOK", dedupe_key=f"outcome:{job_id}",
                payload={"webhook_event_id": str(event_id)}, status="PENDING",
            ))
    return event_id, job_id


def process(session_factory, worker_id: str = "outcome-worker") -> str:
    return WebhookJobWorker(JobQueueService(session_factory, SETTINGS), worker_id).process_once().outcome


def recovery_case(session_factory) -> RecoveryCase:
    with session_factory() as session:
        result = session.scalar(select(RecoveryCase))
        assert result is not None
        return result


def test_captured_original_payment_closes_active_case_as_observed_not_attributed(session_factory):
    enqueue(session_factory, event_name="payment.failed", payload=payment_payload("payment.failed", payment_id="pay_close"))
    assert process(session_factory) == "SUCCEEDED"
    enqueue(session_factory, event_name="payment.captured", payload=payment_payload("payment.captured", payment_id="pay_close"))

    assert process(session_factory) == "SUCCEEDED"
    case = recovery_case(session_factory)
    assert case.status == "CLOSED"
    with session_factory() as session:
        outcome = session.scalar(select(RecoveryOutcome))
        assert outcome is not None
        assert outcome.outcome_type == "PAYMENT_CAPTURED"
        assert outcome.attribution_status == "CAPTURED_OBSERVED"
        assert outcome.attributed_intervention_reference is None


def test_order_paid_closes_active_case_by_external_order_relationship(session_factory):
    enqueue(session_factory, event_name="payment.failed", payload=payment_payload("payment.failed", payment_id="pay_failed", order_id="order_close"))
    assert process(session_factory) == "SUCCEEDED"
    enqueue(session_factory, event_name="order.paid", payload=order_paid_payload(payment_id="pay_success", order_id="order_close"))

    assert process(session_factory) == "SUCCEEDED"
    assert recovery_case(session_factory).status == "CLOSED"


def test_repeated_captured_replay_creates_one_outcome_and_cannot_reopen_case(session_factory):
    _, failed_job = enqueue(session_factory, event_name="payment.failed", payload=payment_payload("payment.failed", payment_id="pay_replay"))
    assert process(session_factory) == "SUCCEEDED"
    _, captured_job = enqueue(session_factory, event_name="payment.captured", payload=payment_payload("payment.captured", payment_id="pay_replay"))
    assert process(session_factory) == "SUCCEEDED"
    with session_factory() as session:
        with session.begin():
            job = session.get(JobQueue, captured_job)
            assert job is not None
            job.status, job.locked_at, job.locked_by, job.lease_expires_at = "PENDING", None, None, None
    assert process(session_factory) == "SUCCEEDED"
    # A late failed replay must see CAPTURED projection and cannot create a new active case.
    with session_factory() as session:
        with session.begin():
            job = session.get(JobQueue, failed_job)
            assert job is not None
            job.status, job.locked_at, job.locked_by, job.lease_expires_at = "PENDING", None, None, None
    assert process(session_factory) == "SUCCEEDED"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryOutcome)) == 1
        assert session.scalar(select(func.count()).select_from(RecoveryCase)) == 1
        assert recovery_case(session_factory).status == "CLOSED"


def test_repeated_order_paid_is_idempotent(session_factory):
    enqueue(session_factory, event_name="payment.failed", payload=payment_payload("payment.failed", payment_id="pay_order_fail", order_id="order_replay"))
    assert process(session_factory) == "SUCCEEDED"
    _, job_id = enqueue(session_factory, event_name="order.paid", payload=order_paid_payload(payment_id="pay_order_success", order_id="order_replay"))
    assert process(session_factory) == "SUCCEEDED"
    with session_factory() as session:
        with session.begin():
            job = session.get(JobQueue, job_id)
            assert job is not None
            job.status, job.locked_at, job.locked_by, job.lease_expires_at = "PENDING", None, None, None
    assert process(session_factory) == "SUCCEEDED"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryOutcome)) == 1


def test_unrelated_captured_payment_does_not_close_or_attribute_case(session_factory):
    enqueue(session_factory, event_name="payment.failed", payload=payment_payload("payment.failed", payment_id="pay_original", order_id="order_original"))
    assert process(session_factory) == "SUCCEEDED"
    enqueue(session_factory, event_name="payment.captured", payload=payment_payload("payment.captured", payment_id="pay_unrelated", order_id="order_other"))
    assert process(session_factory) == "SUCCEEDED"
    assert recovery_case(session_factory).status == "ASSESSING"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryOutcome)) == 0


def test_order_paid_only_closes_cases_for_its_order(session_factory):
    enqueue(session_factory, event_name="payment.failed", payload=payment_payload("payment.failed", payment_id="pay_a", order_id="order_a"))
    enqueue(session_factory, event_name="payment.failed", payload=payment_payload("payment.failed", payment_id="pay_b", order_id="order_b"))
    assert process(session_factory) == "SUCCEEDED"
    assert process(session_factory) == "SUCCEEDED"
    enqueue(session_factory, event_name="order.paid", payload=order_paid_payload(payment_id="pay_c", order_id="order_a"))
    assert process(session_factory) == "SUCCEEDED"
    with session_factory() as session:
        statuses = dict(session.execute(select(Payment.razorpay_payment_id, RecoveryCase.status).join(RecoveryCase, RecoveryCase.original_payment_id == Payment.id)).all())
        assert statuses["pay_a"] == "CLOSED"
        assert statuses["pay_b"] == "ASSESSING"


def test_outcome_audit_is_written_once(session_factory):
    enqueue(session_factory, event_name="payment.failed", payload=payment_payload("payment.failed", payment_id="pay_audit"))
    assert process(session_factory) == "SUCCEEDED"
    enqueue(session_factory, event_name="payment.captured", payload=payment_payload("payment.captured", payment_id="pay_audit"))
    assert process(session_factory) == "SUCCEEDED"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "RECOVERY_CASE_CLOSED_CAPTURED_OBSERVED")) == 1
