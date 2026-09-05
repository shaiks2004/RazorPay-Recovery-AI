from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.db.models import AuditLog, JobQueue, Payment, RecoveryAssessment, RecoveryCase, WebhookEvent
from app.services.job_worker import JobQueueService, WebhookJobWorker, WorkerSettings
from app.services.recovery_assessment import SCORE_VERSION, assess_case, diagnose, expected_recovery_value, extract_features, recommend_action, score


def make_case(session_factory, *, error_code: str | None = "BAD_REQUEST_ERROR", error_reason: str | None = "bank_technical_error", amount: int = 50000, status: str = "ASSESSING", age_hours: int = 0):
    now = datetime.now(timezone.utc)
    payment_id, case_id = uuid.uuid4(), uuid.uuid4()
    with session_factory() as session:
        with session.begin():
            payment = Payment(
                id=payment_id, merchant_id="merchant-test", razorpay_payment_id=f"pay_{payment_id.hex}",
                amount=amount, currency="INR", method="card", status="FAILED",
                error_code=error_code, error_reason=error_reason,
            )
            session.add(payment)
            recovery_case = RecoveryCase(
                id=case_id, merchant_id="merchant-test", original_payment_id=payment_id, amount=amount,
                currency="INR", source="RAZORPAY_TEST", initial_error_code=error_code,
                initial_error_reason=error_reason, eligibility_result="ELIGIBLE",
                eligibility_reason_codes=["FAILED_PAYMENT_UNPAID"], status=status,
                correlation_id=f"evt_{case_id.hex}", created_at=now - timedelta(hours=age_hours),
            )
            session.add(recovery_case)
    return case_id


def assessment(session_factory, case_id) -> RecoveryAssessment | None:
    with session_factory() as session:
        return session.scalar(select(RecoveryAssessment).where(RecoveryAssessment.recovery_case_id == case_id))


def test_documented_transient_error_diagnoses_and_scores_high(session_factory):
    case_id = make_case(session_factory, error_reason="bank_technical_error")
    with session_factory() as session:
        with session.begin():
            result = assess_case(session=session, recovery_case_id=case_id, now=datetime.now(timezone.utc))
    assert result is not None
    assert result.diagnosis == "TRANSIENT_FAILURE"
    assert result.candidate_action == "PREPARE_PAYMENT_LINK"
    assert result.recovery_probability == Decimal("0.8500")


def test_insufficient_error_facts_waits_for_more_facts(session_factory):
    case_id = make_case(session_factory, error_code=None, error_reason=None)
    with session_factory() as session:
        with session.begin():
            result = assess_case(session=session, recovery_case_id=case_id, now=datetime.now(timezone.utc))
    assert result is not None
    assert result.diagnosis == "INSUFFICIENT_FACTS"
    assert result.candidate_action == "WAIT_FOR_MORE_FACTS"


def test_unknown_documented_taxonomy_remains_unknown_and_escalates(session_factory):
    case_id = make_case(session_factory, error_reason="provider_specific_future_reason")
    with session_factory() as session:
        with session.begin():
            result = assess_case(session=session, recovery_case_id=case_id, now=datetime.now(timezone.utc))
    assert result is not None
    assert result.diagnosis == "UNKNOWN_FAILURE"
    assert result.candidate_action == "ESCALATE_FOR_REVIEW"


def test_instrument_failure_scores_low_and_recommends_no_action(session_factory):
    case_id = make_case(session_factory, error_reason="bank_account_invalid")
    with session_factory() as session:
        with session.begin():
            result = assess_case(session=session, recovery_case_id=case_id, now=datetime.now(timezone.utc))
    assert result is not None
    assert result.recovery_probability == Decimal("0.3000")
    # Instrument errors remain reviewable, but are not an automatic preparation recommendation.
    assert result.candidate_action == "ESCALATE_FOR_REVIEW"


def test_assessment_is_reproducible_and_idempotent(session_factory):
    case_id = make_case(session_factory)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        with session.begin():
            first = assess_case(session=session, recovery_case_id=case_id, now=now)
    with session_factory() as session:
        with session.begin():
            replay = assess_case(session=session, recovery_case_id=case_id, now=now + timedelta(hours=1))
    assert first is not None and replay is not None
    assert first.id == replay.id
    assert assessment(session_factory, case_id).score_version == SCORE_VERSION
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryAssessment)) == 1


def test_expected_recovery_value_uses_integer_minor_unit_rounding():
    assert expected_recovery_value(amount=101, probability=Decimal("0.5000")) == 51
    assert expected_recovery_value(amount=0, probability=Decimal("0.9000")) == 0


def test_terminal_or_non_assessing_case_does_not_receive_assessment(session_factory):
    case_id = make_case(session_factory, status="CLOSED")
    with session_factory() as session:
        with session.begin():
            assert assess_case(session=session, recovery_case_id=case_id, now=datetime.now(timezone.utc)) is None
    assert assessment(session_factory, case_id) is None


def test_stale_case_is_deterministically_escalated(session_factory):
    case_id = make_case(session_factory, age_hours=73)
    with session_factory() as session:
        with session.begin():
            result = assess_case(session=session, recovery_case_id=case_id, now=datetime.now(timezone.utc))
    assert result is not None
    assert "STALE_CASE" in result.reason_codes
    assert result.candidate_action == "ESCALATE_FOR_REVIEW"


def test_assessment_audit_events_are_persisted(session_factory):
    case_id = make_case(session_factory)
    with session_factory() as session:
        with session.begin():
            assess_case(session=session, recovery_case_id=case_id, now=datetime.now(timezone.utc))
    with session_factory() as session:
        actions = set(session.scalars(select(AuditLog.action).where(AuditLog.entity_id == case_id)))
    assert {"RECOVERY_DIAGNOSED", "RECOVERY_FEATURES_EXTRACTED", "RECOVERY_SCORED", "RECOVERY_ACTION_RECOMMENDED"} <= actions


def test_failed_webhook_worker_creates_one_assessment_and_replay_is_safe(session_factory):
    event_id, job_id = uuid.uuid4(), uuid.uuid4()
    payload = {
        "event": "payment.failed",
        "payload": {"payment": {"entity": {
            "id": "pay_worker_assessment", "entity": "payment", "amount": 50000, "currency": "INR",
            "status": "failed", "order_id": None, "method": "card",
            "error_code": "BAD_REQUEST_ERROR", "error_reason": "bank_technical_error",
        }}},
    }
    with session_factory() as session:
        with session.begin():
            session.add(WebhookEvent(id=event_id, merchant_id="merchant-test", razorpay_event_id="evt_worker_assessment", event_name="payment.failed", raw_body=b"verified", payload_json=payload, signature_valid=True, validation_status="VALID", processing_status="PENDING"))
            session.add(JobQueue(id=job_id, job_type="PROCESS_RAZORPAY_WEBHOOK", dedupe_key="assessment-worker-job", payload={"webhook_event_id": str(event_id)}, status="PENDING"))
    worker = WebhookJobWorker(JobQueueService(session_factory, WorkerSettings(lease_seconds=10, max_attempts=3, retry_base_seconds=1, retry_max_seconds=5)), "assessment-worker")
    assert worker.process_once().outcome == "SUCCEEDED"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryAssessment)) == 1
    with session_factory() as session:
        with session.begin():
            job = session.get(JobQueue, job_id)
            assert job is not None
            job.status, job.locked_at, job.locked_by, job.lease_expires_at = "PENDING", None, None, None
    assert worker.process_once().outcome == "SUCCEEDED"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryAssessment)) == 1
