from __future__ import annotations

import hashlib
import hmac
import inspect
import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.webhooks import verify_razorpay_signature
from app.db.models import AuditLog, JobQueue, WebhookEvent
from app.services.webhook_ingress import WebhookIngressService, utc_now


def signed_headers(body: bytes, event_id: str = "evt_001") -> dict[str, str]:
    signature = hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    return {"X-Razorpay-Signature": signature, "x-razorpay-event-id": event_id}


def counts(session_factory) -> tuple[int, int, int]:
    with session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(WebhookEvent)) or 0,
            session.scalar(select(func.count()).select_from(JobQueue)) or 0,
            session.scalar(select(func.count()).select_from(AuditLog)) or 0,
        )


def test_valid_signature_persists_event_job_and_audit(client, session_factory):
    body = b'{"event":"payment.failed","payload":{"payment":{"id":"pay_1"}}}'
    response = client.post("/webhooks/razorpay", content=body, headers=signed_headers(body))

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert counts(session_factory) == (1, 1, 1)
    with session_factory() as session:
        stored = session.scalar(select(WebhookEvent))
        assert stored is not None
        assert stored.raw_body == body
        assert stored.event_name == "payment.failed"
        assert stored.signature_valid is True


def test_invalid_or_missing_signature_creates_no_event_or_job(client, session_factory):
    body = b'{"event":"payment.failed"}'
    invalid = client.post("/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": "bad", "x-razorpay-event-id": "evt_bad"})
    missing = client.post("/webhooks/razorpay", content=body, headers={"x-razorpay-event-id": "evt_missing"})

    assert invalid.status_code == 401
    assert missing.status_code == 401
    assert counts(session_factory)[:2] == (0, 0)


def test_signature_uses_original_raw_bytes(client, session_factory):
    raw_body = b'{ "event" : "payment.failed", "payload" : { } }'
    response = client.post("/webhooks/razorpay", content=raw_body, headers=signed_headers(raw_body, "evt_raw"))

    assert response.status_code == 202
    reserialized = json.dumps(json.loads(raw_body)).encode()
    assert not verify_razorpay_signature(raw_body=reserialized, signature=signed_headers(raw_body, "evt_raw")["X-Razorpay-Signature"], secret="test-webhook-secret")
    assert counts(session_factory)[:2] == (1, 1)


def test_signature_uses_constant_time_comparison():
    source = inspect.getsource(verify_razorpay_signature)
    assert "hmac.compare_digest" in source


def test_missing_event_id_is_rejected(client, session_factory):
    body = b'{"event":"payment.failed"}'
    response = client.post("/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": signed_headers(body)["X-Razorpay-Signature"]})

    assert response.status_code == 400
    assert counts(session_factory)[:2] == (0, 0)


def test_duplicate_event_creates_no_second_event_or_job(client, session_factory):
    body = b'{"event":"payment.failed"}'
    headers = signed_headers(body, "evt_duplicate")

    assert client.post("/webhooks/razorpay", content=body, headers=headers).status_code == 202
    duplicate = client.post("/webhooks/razorpay", content=body, headers=headers)

    assert duplicate.status_code == 202
    assert duplicate.json()["status"] == "duplicate"
    assert counts(session_factory)[:2] == (1, 1)


def test_malformed_json_with_valid_signature_creates_no_job(client, session_factory):
    body = b'{"event":'
    response = client.post("/webhooks/razorpay", content=body, headers=signed_headers(body, "evt_malformed"))

    assert response.status_code == 400
    assert counts(session_factory)[:2] == (0, 0)
    with session_factory() as session:
        assert session.scalar(select(AuditLog.action)) == "WEBHOOK_REJECTED_MALFORMED"


def test_unknown_valid_event_is_persisted_without_extra_business_side_effect(client, session_factory):
    body = b'{"event":"unrecognised.event","payload":{}}'
    response = client.post("/webhooks/razorpay", content=body, headers=signed_headers(body, "evt_unknown"))

    assert response.status_code == 202
    assert counts(session_factory) == (1, 1, 1)
    with session_factory() as session:
        assert session.scalar(select(WebhookEvent.event_name)) == "unrecognised.event"


def test_job_insert_failure_rolls_back_event(client, session_factory):
    def fail_job_insert(*_args, **_kwargs):
        raise SQLAlchemyError("simulated queue write failure")

    event.listen(JobQueue, "before_insert", fail_job_insert)
    body = b'{"event":"payment.failed"}'
    try:
        response = client.post("/webhooks/razorpay", content=body, headers=signed_headers(body, "evt_rollback"))
    finally:
        event.remove(JobQueue, "before_insert", fail_job_insert)

    assert response.status_code == 500
    assert counts(session_factory) == (0, 0, 0)


def test_concurrent_duplicate_delivery_is_database_safe(client, session_factory):
    body = b'{"event":"payment.failed"}'
    payload = json.loads(body)
    service = WebhookIngressService(session_factory, "merchant-test")

    def accept():
        return service.accept(
            razorpay_event_id="evt_concurrent",
            raw_body=body,
            payload=payload,
            received_at=utc_now(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: accept(), range(2)))

    assert sum(result.duplicate for result in results) == 1
    assert counts(session_factory)[:2] == (1, 1)
