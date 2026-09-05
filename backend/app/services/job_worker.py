from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AuditLog, JobQueue, WebhookEvent
from app.services.payment_projection import ProjectionPayloadError, project_event
from app.services.recovery_case import evaluate_and_create_case
from app.services.recovery_outcome import close_cases_for_outcome
from app.services.recovery_assessment import assess_case
from app.services.policy_decision import evaluate_and_persist
from app.services.recovery_attribution import evaluate_event as evaluate_attribution_event

logger = logging.getLogger(__name__)

PENDING_STATUSES = ("PENDING", "RETRY_SCHEDULED")
TERMINAL_JOB_STATUSES = ("SUCCEEDED", "FAILED")


class PermanentJobError(Exception):
    """A job cannot succeed by retrying because its persisted input is invalid."""


@dataclass(frozen=True)
class WorkerSettings:
    lease_seconds: int = 60
    max_attempts: int = 5
    retry_base_seconds: int = 5
    retry_max_seconds: int = 300


@dataclass(frozen=True)
class ProcessResult:
    job_id: uuid.UUID | None
    outcome: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def retry_delay_seconds(*, attempt: int, base_seconds: int, max_seconds: int) -> int:
    """Deterministic capped exponential backoff; attempt is one-based."""
    return min(base_seconds * (2 ** max(0, attempt - 1)), max_seconds)


class JobQueueService:
    """Leases jobs atomically. PostgreSQL uses SKIP LOCKED; conditional updates add a second guard."""

    def __init__(self, session_factory: sessionmaker[Session], settings: WorkerSettings) -> None:
        self._session_factory = session_factory
        self._settings = settings

    def recover_expired_leases(self, *, now: datetime) -> int:
        recovered = 0
        with self._session_factory() as session:
            with session.begin():
                expired = session.scalars(
                    select(JobQueue)
                    .where(JobQueue.status == "RUNNING", JobQueue.lease_expires_at <= now)
                    .with_for_update(skip_locked=True)
                ).all()
                for job in expired:
                    recovered += 1
                    if job.attempts >= self._settings.max_attempts:
                        job.status = "FAILED"
                        action = "JOB_LEASE_EXPIRED_FAILED"
                    else:
                        job.status = "RETRY_SCHEDULED"
                        job.available_at = now + timedelta(
                            seconds=retry_delay_seconds(
                                attempt=job.attempts,
                                base_seconds=self._settings.retry_base_seconds,
                                max_seconds=self._settings.retry_max_seconds,
                            )
                        )
                        action = "JOB_LEASE_EXPIRED_REQUEUED"
                    job.locked_at = None
                    job.locked_by = None
                    job.lease_expires_at = None
                    job.last_error = "worker lease expired"
                    session.add(self._audit(job=job, action=action, metadata={"attempt": job.attempts}))
        return recovered

    def claim_next(self, *, worker_id: str, now: datetime) -> JobQueue | None:
        """Claim exactly one available job and increment attempts in the leasing transaction."""
        with self._session_factory() as session:
            with session.begin():
                candidate_id = session.scalar(self._claim_candidate_query(now))
                if candidate_id is None:
                    return None
                updated = session.execute(
                    update(JobQueue)
                    .where(
                        JobQueue.id == candidate_id,
                        JobQueue.status.in_(PENDING_STATUSES),
                        JobQueue.available_at <= now,
                    )
                    .values(
                        status="RUNNING",
                        attempts=JobQueue.attempts + 1,
                        locked_at=now,
                        locked_by=worker_id,
                        lease_expires_at=now + timedelta(seconds=self._settings.lease_seconds),
                        last_error=None,
                    )
                )
                if updated.rowcount != 1:
                    return None
                job = session.get(JobQueue, candidate_id)
                assert job is not None
                session.add(self._audit(job=job, action="JOB_LEASED", metadata={"attempt": job.attempts, "worker_id": worker_id}))
                return job

    def retry_or_fail(self, *, job_id: uuid.UUID, worker_id: str, error: Exception, now: datetime) -> str:
        with self._session_factory() as session:
            with session.begin():
                job = self._locked_job(session, job_id, worker_id)
                if job.attempts >= self._settings.max_attempts:
                    job.status = "FAILED"
                    job.available_at = now
                    action = "JOB_FAILED"
                else:
                    job.status = "RETRY_SCHEDULED"
                    job.available_at = now + timedelta(
                        seconds=retry_delay_seconds(
                            attempt=job.attempts,
                            base_seconds=self._settings.retry_base_seconds,
                            max_seconds=self._settings.retry_max_seconds,
                        )
                    )
                    action = "JOB_RETRY_SCHEDULED"
                job.locked_at = None
                job.locked_by = None
                job.lease_expires_at = None
                job.last_error = _safe_error(error)
                session.add(self._audit(job=job, action=action, metadata={"attempt": job.attempts, "error_type": type(error).__name__}))
                return job.status

    def fail_permanently(self, *, job_id: uuid.UUID, worker_id: str, error: Exception, now: datetime) -> None:
        with self._session_factory() as session:
            with session.begin():
                job = self._locked_job(session, job_id, worker_id)
                job.status = "FAILED"
                job.available_at = now
                job.locked_at = None
                job.locked_by = None
                job.lease_expires_at = None
                job.last_error = _safe_error(error)
                session.add(self._audit(job=job, action="JOB_FAILED_PERMANENT", metadata={"error_type": type(error).__name__}))
                if isinstance(error, ProjectionPayloadError):
                    self._mark_projection_rejected(session=session, job=job, now=now, error=error)

    def complete_webhook_dispatch(self, *, job_id: uuid.UUID, worker_id: str, now: datetime, advisor=None, advisor_confidence_threshold: float = 0.70) -> None:
        """Project one stored webhook fact and complete its job in one transaction."""
        with self._session_factory() as session:
            with session.begin():
                job = self._locked_job(session, job_id, worker_id)
                webhook_event_id = _webhook_event_id(job)
                event = session.scalar(select(WebhookEvent).where(WebhookEvent.id == webhook_event_id).with_for_update())
                if event is None:
                    raise PermanentJobError("referenced webhook event does not exist")

                if event.processing_status in ("PENDING", "READY_FOR_PROJECTION"):
                    project_event(session=session, event=event, now=now)
                elif event.processing_status not in ("PROJECTED", "IGNORED_UNKNOWN"):
                    raise PermanentJobError(f"webhook event has unsupported status: {event.processing_status}")

                if event.event_name == "payment.failed" and event.processing_status == "PROJECTED":
                    case_result = evaluate_and_create_case(session=session, event=event, now=now)
                    if case_result.case_id is not None:
                        assessment = assess_case(session=session, recovery_case_id=case_result.case_id, now=now)
                        if assessment is not None:
                            if advisor is not None:
                                from app.services.ai_advisor import advise_assessment
                                advise_assessment(session=session, recovery_case_id=case_result.case_id, assessment_id=assessment.id, advisor=advisor, confidence_threshold=advisor_confidence_threshold, now=now)
                            evaluate_and_persist(session=session, recovery_case_id=case_result.case_id, assessment_id=assessment.id, now=now)
                elif event.event_name in ("payment.captured", "order.paid") and event.processing_status == "PROJECTED":
                    close_cases_for_outcome(session=session, event=event, now=now)
                if event.processing_status == "PROJECTED":
                    session.flush()
                    evaluate_attribution_event(session=session, event=event, now=now)

                job.status = "SUCCEEDED"
                job.locked_at = None
                job.locked_by = None
                job.lease_expires_at = None
                job.last_error = None
                session.add(self._audit(job=job, action="JOB_SUCCEEDED", metadata={"job_type": job.job_type}))

    @staticmethod
    def _mark_projection_rejected(*, session: Session, job: JobQueue, now: datetime, error: ProjectionPayloadError) -> None:
        webhook_event_id = _webhook_event_id(job)
        event = session.scalar(select(WebhookEvent).where(WebhookEvent.id == webhook_event_id).with_for_update())
        if event is None:
            return
        event.processing_status = "PROJECTION_REJECTED"
        event.processed_at = now
        session.add(
            AuditLog(
                actor="background_worker",
                action="WEBHOOK_PROJECTION_REJECTED",
                entity_type="webhook_event",
                entity_id=event.id,
                correlation_id=event.razorpay_event_id,
                metadata_json={"error_type": type(error).__name__},
            )
        )

    def _claim_candidate_query(self, now: datetime) -> Select[tuple[uuid.UUID]]:
        return (
            select(JobQueue.id)
            .where(JobQueue.status.in_(PENDING_STATUSES), JobQueue.available_at <= now)
            .order_by(JobQueue.available_at, JobQueue.created_at, JobQueue.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    @staticmethod
    def _locked_job(session: Session, job_id: uuid.UUID, worker_id: str) -> JobQueue:
        job = session.scalar(
            select(JobQueue)
            .where(JobQueue.id == job_id, JobQueue.status == "RUNNING", JobQueue.locked_by == worker_id)
            .with_for_update()
        )
        if job is None:
            raise PermanentJobError("job is not leased by this worker")
        return job

    @staticmethod
    def _audit(*, job: JobQueue, action: str, metadata: dict) -> AuditLog:
        return AuditLog(
            actor="background_worker",
            action=action,
            entity_type="job_queue",
            entity_id=job.id,
            correlation_id=str(job.id),
            metadata_json=metadata,
        )


class WebhookJobWorker:
    """Single-job worker. It deliberately does not run recovery, AI, policy, or payment actions."""

    def __init__(self, queue: JobQueueService, worker_id: str, advisor=None, advisor_confidence_threshold: float = 0.70) -> None:
        self._queue, self._worker_id, self._advisor, self._advisor_confidence_threshold = queue, worker_id, advisor, advisor_confidence_threshold

    def process_once(self, *, now: datetime | None = None) -> ProcessResult:
        current_time = now or utc_now()
        self._queue.recover_expired_leases(now=current_time)
        job = self._queue.claim_next(worker_id=self._worker_id, now=current_time)
        if job is None:
            return ProcessResult(job_id=None, outcome="IDLE")
        try:
            if job.job_type != "PROCESS_RAZORPAY_WEBHOOK":
                raise PermanentJobError(f"unsupported job type: {job.job_type}")
            self._queue.complete_webhook_dispatch(job_id=job.id, worker_id=self._worker_id, now=current_time, advisor=self._advisor, advisor_confidence_threshold=self._advisor_confidence_threshold)
            return ProcessResult(job_id=job.id, outcome="SUCCEEDED")
        except (PermanentJobError, ProjectionPayloadError) as error:
            self._queue.fail_permanently(job_id=job.id, worker_id=self._worker_id, error=error, now=current_time)
            return ProcessResult(job_id=job.id, outcome="FAILED")
        except Exception as error:
            status = self._queue.retry_or_fail(job_id=job.id, worker_id=self._worker_id, error=error, now=current_time)
            logger.error("Background job failed", extra={"job_id": str(job.id), "error_type": type(error).__name__})
            return ProcessResult(job_id=job.id, outcome=status)


def _webhook_event_id(job: JobQueue) -> uuid.UUID:
    value = job.payload.get("webhook_event_id") if isinstance(job.payload, dict) else None
    if not isinstance(value, str):
        raise PermanentJobError("webhook job payload is missing webhook_event_id")
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise PermanentJobError("webhook job payload contains invalid webhook_event_id") from error


def _safe_error(error: Exception) -> str:
    """Avoid persisting arbitrary exception text that may contain external payload data."""
    return type(error).__name__
