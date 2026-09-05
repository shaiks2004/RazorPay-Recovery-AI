from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import func, select

from app.db.models import AIAdvisory, AuditLog, JobQueue, WebhookEvent
from app.domain.ai_advisor import AIAdvice
from app.integrations.ai_provider import FakeAIAdvisor
from app.services.job_worker import JobQueueService, WebhookJobWorker, WorkerSettings, retry_delay_seconds, utc_now


WORKER_SETTINGS = WorkerSettings(lease_seconds=10, max_attempts=3, retry_base_seconds=5, retry_max_seconds=20)


def create_webhook_job(session_factory, *, event_status: str = "PENDING", include_event: bool = True) -> tuple[uuid.UUID, uuid.UUID | None]:
    event_id = uuid.uuid4()
    job_id = uuid.uuid4()
    razorpay_payment_id = f"pay_{event_id.hex}"
    razorpay_order_id = f"order_{event_id.hex}"
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": razorpay_payment_id,
                    "entity": "payment",
                    "amount": 100,
                    "currency": "INR",
                    "status": "failed",
                    "order_id": razorpay_order_id,
                    "method": "card",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_reason": "payment_failed",
                }
            }
        },
    }
    with session_factory() as session:
        with session.begin():
            if include_event:
                session.add(
                    WebhookEvent(
                        id=event_id,
                        merchant_id="merchant-test",
                        razorpay_event_id=f"evt_{event_id}",
                        event_name="payment.failed",
                        raw_body=b'{"event":"payment.failed","payload":{"payment":{"entity":{}}}}',
                        payload_json=payload,
                        signature_valid=True,
                        validation_status="VALID",
                        processing_status=event_status,
                    )
                )
            session.add(
                JobQueue(
                    id=job_id,
                    job_type="PROCESS_RAZORPAY_WEBHOOK",
                    dedupe_key=f"job:{job_id}",
                    payload={"webhook_event_id": str(event_id)},
                    status="PENDING",
                )
            )
    return job_id, event_id if include_event else None


def load_job(session_factory, job_id: uuid.UUID) -> JobQueue:
    with session_factory() as session:
        job = session.get(JobQueue, job_id)
        assert job is not None
        return job


def assert_same_timestamp(actual, expected) -> None:
    """SQLite test storage returns naive datetimes; PostgreSQL preserves UTC tzinfo."""
    assert actual.replace(tzinfo=None) == expected.replace(tzinfo=None)


def test_process_webhook_job_marks_event_ready_and_job_succeeded(session_factory):
    job_id, event_id = create_webhook_job(session_factory)
    worker = WebhookJobWorker(JobQueueService(session_factory, WORKER_SETTINGS), "worker-a")

    result = worker.process_once()

    assert result == type(result)(job_id=job_id, outcome="SUCCEEDED")
    assert load_job(session_factory, job_id).status == "SUCCEEDED"
    with session_factory() as session:
        event = session.get(WebhookEvent, event_id)
        assert event is not None
        assert event.processing_status == "PROJECTED"
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "WEBHOOK_PROJECTED")) == 1


def test_completed_job_is_not_processed_again(session_factory):
    job_id, _ = create_webhook_job(session_factory)
    worker = WebhookJobWorker(JobQueueService(session_factory, WORKER_SETTINGS), "worker-a")

    assert worker.process_once().outcome == "SUCCEEDED"
    assert worker.process_once().outcome == "IDLE"
    assert load_job(session_factory, job_id).attempts == 1


def test_concurrent_workers_lease_a_job_once(session_factory):
    job_id, _ = create_webhook_job(session_factory)
    queue = JobQueueService(session_factory, WORKER_SETTINGS)
    current_time = utc_now()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda worker_id: queue.claim_next(worker_id=worker_id, now=current_time), ("worker-a", "worker-b")))

    claimed = [job for job in claims if job is not None]
    assert len(claimed) == 1
    assert claimed[0].id == job_id
    stored = load_job(session_factory, job_id)
    assert stored.status == "RUNNING"
    assert stored.attempts == 1


def test_expired_lease_is_requeued_then_processed_by_restarted_worker(session_factory):
    job_id, _ = create_webhook_job(session_factory)
    queue = JobQueueService(session_factory, WORKER_SETTINGS)
    claimed_at = utc_now()
    claimed = queue.claim_next(worker_id="crashed-worker", now=claimed_at)
    assert claimed is not None

    expiry_time = claimed_at + timedelta(seconds=WORKER_SETTINGS.lease_seconds + 1)
    assert queue.recover_expired_leases(now=expiry_time) == 1
    recovered = load_job(session_factory, job_id)
    assert recovered.status == "RETRY_SCHEDULED"
    assert recovered.locked_by is None
    assert_same_timestamp(recovered.available_at, expiry_time + timedelta(seconds=5))

    restarted = WebhookJobWorker(queue, "restarted-worker")
    assert restarted.process_once(now=recovered.available_at).outcome == "SUCCEEDED"
    assert load_job(session_factory, job_id).status == "SUCCEEDED"


def test_expired_lease_at_attempt_limit_fails_without_reclaim(session_factory):
    job_id, _ = create_webhook_job(session_factory)
    queue = JobQueueService(session_factory, WORKER_SETTINGS)
    claimed_at = utc_now()
    assert queue.claim_next(worker_id="worker-a", now=claimed_at) is not None
    assert queue.claim_next(worker_id="worker-a", now=claimed_at) is None
    with session_factory() as session:
        with session.begin():
            job = session.get(JobQueue, job_id)
            assert job is not None
            job.attempts = WORKER_SETTINGS.max_attempts
            job.lease_expires_at = claimed_at - timedelta(seconds=1)

    assert queue.recover_expired_leases(now=claimed_at) == 1
    assert load_job(session_factory, job_id).status == "FAILED"


def test_transient_failure_uses_bounded_exponential_backoff(session_factory, monkeypatch):
    job_id, _ = create_webhook_job(session_factory)
    queue = JobQueueService(session_factory, WORKER_SETTINGS)
    worker = WebhookJobWorker(queue, "worker-a")
    current_time = utc_now()

    def fail_once(**_kwargs):
        raise RuntimeError("temporary database dependency failure")

    monkeypatch.setattr(queue, "complete_webhook_dispatch", fail_once)
    result = worker.process_once(now=current_time)

    assert result.outcome == "RETRY_SCHEDULED"
    job = load_job(session_factory, job_id)
    assert job.status == "RETRY_SCHEDULED"
    assert job.attempts == 1
    assert_same_timestamp(job.available_at, current_time + timedelta(seconds=5))
    assert retry_delay_seconds(attempt=1, base_seconds=5, max_seconds=20) == 5
    assert retry_delay_seconds(attempt=2, base_seconds=5, max_seconds=20) == 10
    assert retry_delay_seconds(attempt=4, base_seconds=5, max_seconds=20) == 20


def test_missing_webhook_event_fails_permanently(session_factory):
    job_id, _ = create_webhook_job(session_factory, include_event=False)
    worker = WebhookJobWorker(JobQueueService(session_factory, WORKER_SETTINGS), "worker-a")

    assert worker.process_once().outcome == "FAILED"
    job = load_job(session_factory, job_id)
    assert job.status == "FAILED"
    assert job.attempts == 1


def test_projected_event_completion_is_idempotent(session_factory):
    job_id, _ = create_webhook_job(session_factory, event_status="PROJECTED")
    worker = WebhookJobWorker(JobQueueService(session_factory, WORKER_SETTINGS), "worker-a")

    assert worker.process_once().outcome == "SUCCEEDED"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "WEBHOOK_PROJECTED")) == 0
    assert load_job(session_factory, job_id).status == "SUCCEEDED"


def test_worker_persists_advisory_when_advisor_is_supplied(session_factory):
    job_id, _ = create_webhook_job(session_factory)
    advice = AIAdvice(
        "UNKNOWN_FAILURE", "PREPARE_PAYMENT_LINK", 0.82,
        ["POSITIVE_OUTSTANDING_BALANCE"], "Advisory only.", ["CUSTOMER_INTENT_UNKNOWN"],
    )
    worker = WebhookJobWorker(
        JobQueueService(session_factory, WORKER_SETTINGS),
        "worker-ai",
        advisor=FakeAIAdvisor(advice),
        advisor_confidence_threshold=0.70,
    )

    assert worker.process_once().job_id == job_id
    with session_factory() as session:
        advisory = session.scalar(select(AIAdvisory))
        assert advisory is not None
        assert advisory.status == "COMPLETED"
        assert advisory.recommended_action == "PREPARE_PAYMENT_LINK"
