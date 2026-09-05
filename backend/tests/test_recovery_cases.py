from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.db.models import AuditLog, JobQueue, Order, Payment, RecoveryAssessment, RecoveryCase, WebhookEvent
from app.services.job_worker import JobQueueService, WebhookJobWorker, WorkerSettings


WORKER_SETTINGS = WorkerSettings(lease_seconds=10, max_attempts=3, retry_base_seconds=1, retry_max_seconds=5)


def failed_payload(*, payment_id: str = "pay_recovery", order_id: str | None = None, amount: int = 50000) -> dict:
    return {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": amount,
                    "currency": "INR",
                    "status": "failed",
                    "order_id": order_id,
                    "method": "card",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_reason": "payment_failed",
                }
            }
        },
    }


def captured_payload(*, payment_id: str, order_id: str | None = None) -> dict:
    payload = failed_payload(payment_id=payment_id, order_id=order_id)
    payload["event"] = "payment.captured"
    payload["payload"]["payment"]["entity"]["status"] = "captured"
    payload["payload"]["payment"]["entity"]["error_code"] = None
    payload["payload"]["payment"]["entity"]["error_reason"] = None
    return payload


def order_paid_payload(*, payment_id: str, order_id: str) -> dict:
    payload = captured_payload(payment_id=payment_id, order_id=order_id)
    payload["event"] = "order.paid"
    payload["payload"]["order"] = {
        "entity": {
            "id": order_id,
            "entity": "order",
            "amount": 50000,
            "amount_paid": 50000,
            "amount_due": 0,
            "currency": "INR",
            "receipt": "receipt-recovery",
            "status": "paid",
        }
    }
    return payload


def enqueue(session_factory, *, event_name: str, payload: dict) -> tuple[uuid.UUID, uuid.UUID]:
    event_id, job_id = uuid.uuid4(), uuid.uuid4()
    with session_factory() as session:
        with session.begin():
            session.add(
                WebhookEvent(
                    id=event_id,
                    merchant_id="merchant-test",
                    razorpay_event_id=f"evt_{event_id.hex}",
                    event_name=event_name,
                    raw_body=b"verified raw body",
                    payload_json=payload,
                    signature_valid=True,
                    validation_status="VALID",
                    processing_status="PENDING",
                )
            )
            session.add(
                JobQueue(
                    id=job_id,
                    job_type="PROCESS_RAZORPAY_WEBHOOK",
                    dedupe_key=f"case-job:{job_id}",
                    payload={"webhook_event_id": str(event_id)},
                    status="PENDING",
                )
            )
    return event_id, job_id


def run_worker(session_factory, worker_id: str = "case-worker"):
    return WebhookJobWorker(JobQueueService(session_factory, WORKER_SETTINGS), worker_id).process_once()


def cases(session_factory) -> list[RecoveryCase]:
    with session_factory() as session:
        return list(session.scalars(select(RecoveryCase)))


def test_eligible_failed_payment_creates_assessing_case_with_payment_amount_and_currency(session_factory):
    event_id, _ = enqueue(session_factory, event_name="payment.failed", payload=failed_payload())

    assert run_worker(session_factory).outcome == "SUCCEEDED"
    created = cases(session_factory)
    assert len(created) == 1
    recovery_case = created[0]
    assert recovery_case.status == "ASSESSING"
    assert recovery_case.amount == 50000
    assert recovery_case.currency == "INR"
    assert recovery_case.eligibility_result == "ELIGIBLE"
    assert recovery_case.eligibility_reason_codes == ["FAILED_PAYMENT_UNPAID", "POSITIVE_OUTSTANDING_AMOUNT"]
    with session_factory() as session:
        event = session.get(WebhookEvent, event_id)
        payment = session.get(Payment, recovery_case.original_payment_id)
        assert event is not None and payment is not None
        assert payment.razorpay_payment_id == "pay_recovery"
        assert recovery_case.correlation_id == event.razorpay_event_id


def test_duplicate_failure_events_create_one_active_case(session_factory):
    enqueue(session_factory, event_name="payment.failed", payload=failed_payload(payment_id="pay_duplicate"))
    enqueue(session_factory, event_name="payment.failed", payload=failed_payload(payment_id="pay_duplicate"))

    assert run_worker(session_factory).outcome == "SUCCEEDED"
    assert run_worker(session_factory).outcome == "SUCCEEDED"
    assert len(cases(session_factory)) == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "RECOVERY_CASE_CREATED")) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "RECOVERY_CASE_ELIGIBILITY_EVALUATED")) == 1


def test_worker_replay_does_not_create_duplicate_case(session_factory):
    _, job_id = enqueue(session_factory, event_name="payment.failed", payload=failed_payload(payment_id="pay_replay"))
    assert run_worker(session_factory).outcome == "SUCCEEDED"
    with session_factory() as session:
        with session.begin():
            job = session.get(JobQueue, job_id)
            assert job is not None
            job.status = "PENDING"
            job.locked_at = job.locked_by = job.lease_expires_at = None
    assert run_worker(session_factory).outcome == "SUCCEEDED"
    assert len(cases(session_factory)) == 1


def test_captured_payment_before_failed_event_creates_no_case(session_factory):
    enqueue(session_factory, event_name="payment.captured", payload=captured_payload(payment_id="pay_captured"))
    enqueue(session_factory, event_name="payment.failed", payload=failed_payload(payment_id="pay_captured"))
    assert run_worker(session_factory).outcome == "SUCCEEDED"
    assert run_worker(session_factory).outcome == "SUCCEEDED"

    assert cases(session_factory) == []
    with session_factory() as session:
        assert session.scalar(select(AuditLog.metadata_json).where(AuditLog.action == "RECOVERY_CASE_ELIGIBILITY_EVALUATED"))["eligibility_result"] == "ALREADY_PAID"


def test_paid_order_before_failed_event_creates_no_case(session_factory):
    enqueue(session_factory, event_name="order.paid", payload=order_paid_payload(payment_id="pay_order_paid", order_id="order_paid"))
    enqueue(session_factory, event_name="payment.failed", payload=failed_payload(payment_id="pay_order_paid", order_id="order_paid"))
    assert run_worker(session_factory).outcome == "SUCCEEDED"
    assert run_worker(session_factory).outcome == "SUCCEEDED"

    assert cases(session_factory) == []


def test_unsupported_failure_projection_does_not_create_case(session_factory):
    payload = failed_payload(payment_id="pay_bad")
    del payload["payload"]["payment"]["entity"]["id"]
    event_id, _ = enqueue(session_factory, event_name="payment.failed", payload=payload)

    assert run_worker(session_factory).outcome == "FAILED"
    assert cases(session_factory) == []
    with session_factory() as session:
        assert session.get(WebhookEvent, event_id).processing_status == "PROJECTION_REJECTED"


def test_non_positive_failure_is_ineligible_without_case(session_factory):
    enqueue(session_factory, event_name="payment.failed", payload=failed_payload(payment_id="pay_zero", amount=0))
    assert run_worker(session_factory).outcome == "SUCCEEDED"

    assert cases(session_factory) == []
    with session_factory() as session:
        metadata = session.scalar(select(AuditLog.metadata_json).where(AuditLog.action == "RECOVERY_CASE_ELIGIBILITY_EVALUATED"))
        assert metadata["eligibility_result"] == "INELIGIBLE"
        assert metadata["reason_codes"] == ["NON_POSITIVE_AMOUNT"]


def test_concurrent_worker_processing_of_same_payment_never_creates_two_active_cases(session_factory):
    enqueue(session_factory, event_name="payment.failed", payload=failed_payload(payment_id="pay_concurrent"))
    enqueue(session_factory, event_name="payment.failed", payload=failed_payload(payment_id="pay_concurrent"))
    workers = [WebhookJobWorker(JobQueueService(session_factory, WORKER_SETTINGS), f"worker-{index}") for index in (1, 2)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda current: current.process_once().outcome, workers))

    # A worker can legitimately observe no claimable row when another SQLite test worker holds the write lock.
    # PostgreSQL production claiming uses SKIP LOCKED; either outcome preserves the one-active-case invariant.
    assert all(outcome in ("SUCCEEDED", "RETRY_SCHEDULED", "IDLE") for outcome in outcomes)
    # A retry is processed only after the active case transaction commits.
    for _ in range(2):
        run_worker(session_factory, "recovery-worker")
    assert len(cases(session_factory)) == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryAssessment)) == 1
