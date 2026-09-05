from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import AuditLog, JobQueue, Order, Payment, PaymentAttempt, WebhookEvent
from app.services.job_worker import JobQueueService, WebhookJobWorker, WorkerSettings, utc_now


WORKER_SETTINGS = WorkerSettings(lease_seconds=10, max_attempts=3, retry_base_seconds=5, retry_max_seconds=20)


def payment_entity(*, payment_id: str, status: str, order_id: str | None = "order_001") -> dict:
    return {
        "id": payment_id,
        "entity": "payment",
        "amount": 50000,
        "currency": "INR",
        "status": status.lower(),
        "order_id": order_id,
        "method": "card",
        "error_code": "BAD_REQUEST_ERROR" if status == "FAILED" else None,
        "error_reason": "payment_failed" if status == "FAILED" else None,
    }


def payload_for(event_name: str, *, payment_id: str = "pay_001", order_id: str = "order_001") -> dict:
    payment_status = {
        "payment.failed": "FAILED",
        "payment.authorized": "AUTHORIZED",
        "payment.captured": "CAPTURED",
        "order.paid": "CAPTURED",
    }[event_name]
    payload = {"event": event_name, "payload": {"payment": {"entity": payment_entity(payment_id=payment_id, status=payment_status, order_id=order_id)}}}
    if event_name == "order.paid":
        payload["payload"]["order"] = {
            "entity": {
                "id": order_id,
                "entity": "order",
                "amount": 50000,
                "amount_paid": 50000,
                "amount_due": 0,
                "currency": "INR",
                "receipt": "receipt-001",
                "status": "paid",
            }
        }
    return payload


def enqueue_event(session_factory, *, event_name: str, payload: dict, event_id: str | None = None) -> tuple[uuid.UUID, uuid.UUID]:
    webhook_id = uuid.uuid4()
    job_id = uuid.uuid4()
    with session_factory() as session:
        with session.begin():
            session.add(
                WebhookEvent(
                    id=webhook_id,
                    merchant_id="merchant-test",
                    razorpay_event_id=event_id or f"evt_{webhook_id.hex}",
                    event_name=event_name,
                    raw_body=b"raw-signed-webhook-fact",
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
                    dedupe_key=f"projection-job:{job_id}",
                    payload={"webhook_event_id": str(webhook_id)},
                    status="PENDING",
                )
            )
    return webhook_id, job_id


def worker(session_factory, name: str = "projection-worker") -> WebhookJobWorker:
    return WebhookJobWorker(JobQueueService(session_factory, WORKER_SETTINGS), name)


def payment_projection(session_factory, payment_id: str) -> Payment:
    with session_factory() as session:
        projected = session.scalar(select(Payment).where(Payment.razorpay_payment_id == payment_id))
        assert projected is not None
        return projected


@pytest.mark.parametrize(
    ("event_name", "expected_status"),
    [
        ("payment.failed", "FAILED"),
        ("payment.authorized", "AUTHORIZED"),
        ("payment.captured", "CAPTURED"),
    ],
)
def test_payment_events_create_deterministic_payment_projection(session_factory, event_name, expected_status):
    payment_id = f"pay_{event_name.replace('.', '_')}"
    event_id, job_id = enqueue_event(session_factory, event_name=event_name, payload=payload_for(event_name, payment_id=payment_id))

    result = worker(session_factory).process_once()

    assert result.outcome == "SUCCEEDED"
    assert payment_projection(session_factory, payment_id).status == expected_status
    with session_factory() as session:
        assert session.get(WebhookEvent, event_id).processing_status == "PROJECTED"
        assert session.get(JobQueue, job_id).status == "SUCCEEDED"
        assert session.scalar(select(func.count()).select_from(PaymentAttempt)) == 1


def test_order_paid_projects_payment_attempt_and_order(session_factory):
    _, _ = enqueue_event(session_factory, event_name="order.paid", payload=payload_for("order.paid"))

    assert worker(session_factory).process_once().outcome == "SUCCEEDED"
    assert payment_projection(session_factory, "pay_001").status == "CAPTURED"
    with session_factory() as session:
        order = session.scalar(select(Order).where(Order.razorpay_order_id == "order_001"))
        attempt = session.scalar(select(PaymentAttempt))
        assert order is not None
        assert order.status == "PAID"
        assert order.amount_paid == 50000
        assert attempt is not None and attempt.status == "CAPTURED"


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("payment.failed", "payment.captured", "CAPTURED"),
        ("payment.authorized", "payment.captured", "CAPTURED"),
        ("payment.captured", "payment.failed", "CAPTURED"),
        ("payment.captured", "payment.authorized", "CAPTURED"),
    ],
)
def test_payment_state_is_monotonic_regardless_of_delivery_order(session_factory, first, second, expected):
    payment_id = f"pay_{first}_{second}".replace(".", "_")
    enqueue_event(session_factory, event_name=first, payload=payload_for(first, payment_id=payment_id))
    enqueue_event(session_factory, event_name=second, payload=payload_for(second, payment_id=payment_id))
    worker_instance = worker(session_factory)

    assert worker_instance.process_once().outcome == "SUCCEEDED"
    assert worker_instance.process_once().outcome == "SUCCEEDED"
    assert payment_projection(session_factory, payment_id).status == expected


def test_reprocessing_already_projected_event_has_no_duplicate_projection_side_effect(session_factory):
    event_id, job_id = enqueue_event(session_factory, event_name="payment.failed", payload=payload_for("payment.failed"))
    worker_instance = worker(session_factory)
    assert worker_instance.process_once().outcome == "SUCCEEDED"

    with session_factory() as session:
        with session.begin():
            job = session.get(JobQueue, job_id)
            assert job is not None
            job.status = "PENDING"
            job.available_at = utc_now()
            job.locked_by = None
            job.locked_at = None
            job.lease_expires_at = None

    assert worker_instance.process_once().outcome == "SUCCEEDED"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(PaymentAttempt)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "WEBHOOK_PROJECTED")) == 1
        assert session.get(WebhookEvent, event_id).processing_status == "PROJECTED"


def test_unknown_valid_event_is_ignored_without_projection_side_effect(session_factory):
    event_id, job_id = enqueue_event(session_factory, event_name="payment.downtime.started", payload={"event": "payment.downtime.started", "payload": {}})

    assert worker(session_factory).process_once().outcome == "SUCCEEDED"
    with session_factory() as session:
        assert session.get(WebhookEvent, event_id).processing_status == "IGNORED_UNKNOWN"
        assert session.get(JobQueue, job_id).status == "SUCCEEDED"
        assert session.scalar(select(func.count()).select_from(Payment)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "WEBHOOK_PROJECTION_IGNORED_UNKNOWN")) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"event": "payment.failed", "payload": {"payment": {"entity": {"entity": "payment", "amount": 1, "currency": "INR", "status": "failed"}}}},
        {"event": "payment.captured", "payload": {"payment": {"entity": payment_entity(payment_id="pay_wrong", status="FAILED")}}},
        {"event": "order.paid", "payload": {"payment": {"entity": payment_entity(payment_id="pay_missing_order", status="CAPTURED")}}},
    ],
)
def test_unsupported_or_missing_required_payload_data_fails_permanently(session_factory, payload):
    event_name = payload["event"]
    event_id, job_id = enqueue_event(session_factory, event_name=event_name, payload=payload)

    assert worker(session_factory).process_once().outcome == "FAILED"
    with session_factory() as session:
        assert session.get(JobQueue, job_id).status == "FAILED"
        assert session.get(WebhookEvent, event_id).processing_status == "PROJECTION_REJECTED"
        assert session.scalar(select(func.count()).select_from(Payment)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "WEBHOOK_PROJECTION_REJECTED")) == 1


def test_crash_recovery_projects_once_after_lease_expiry(session_factory):
    event_id, job_id = enqueue_event(session_factory, event_name="payment.failed", payload=payload_for("payment.failed"))
    queue = JobQueueService(session_factory, WORKER_SETTINGS)
    claimed_at = utc_now()
    assert queue.claim_next(worker_id="crashed-worker", now=claimed_at) is not None
    recovery_time = claimed_at + timedelta(seconds=WORKER_SETTINGS.lease_seconds + 1)
    assert queue.recover_expired_leases(now=recovery_time) == 1

    with session_factory() as session:
        recovered = session.get(JobQueue, job_id)
        assert recovered is not None
        retry_time = recovered.available_at
    assert worker(session_factory, "restarted-worker").process_once(now=retry_time).outcome == "SUCCEEDED"
    with session_factory() as session:
        assert session.get(WebhookEvent, event_id).processing_status == "PROJECTED"
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "WEBHOOK_PROJECTED")) == 1
