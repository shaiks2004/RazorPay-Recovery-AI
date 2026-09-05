from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AuditLog, JobQueue, WebhookEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngressResult:
    event_id: uuid.UUID | None
    duplicate: bool


class WebhookIngressService:
    """Persists accepted webhook facts and work in one database transaction."""

    def __init__(self, session_factory: sessionmaker[Session], merchant_id: str) -> None:
        self._session_factory = session_factory
        self._merchant_id = merchant_id

    def accept(self, *, razorpay_event_id: str, raw_body: bytes, payload: dict, received_at: datetime) -> IngressResult:
        event_name = payload.get("event") if isinstance(payload.get("event"), str) else "unknown"
        event = WebhookEvent(
            merchant_id=self._merchant_id,
            razorpay_event_id=razorpay_event_id,
            event_name=event_name,
            raw_body=raw_body,
            payload_json=payload,
            signature_valid=True,
            validation_status="VALID",
            processing_status="PENDING",
            received_at=received_at,
        )
        try:
            with self._session_factory() as session:
                with session.begin():
                    session.add(event)
                    session.flush()  # Executes the database uniqueness constraint before enqueueing.
                    session.add(
                        JobQueue(
                            job_type="PROCESS_RAZORPAY_WEBHOOK",
                            dedupe_key=f"razorpay-webhook:{self._merchant_id}:{razorpay_event_id}",
                            payload={"webhook_event_id": str(event.id)},
                            status="PENDING",
                        )
                    )
                    session.add(
                        AuditLog(
                            actor="razorpay_webhook",
                            action="WEBHOOK_ACCEPTED",
                            entity_type="webhook_event",
                            entity_id=event.id,
                            correlation_id=razorpay_event_id,
                            metadata_json={"event_name": event_name},
                        )
                    )
            return IngressResult(event_id=event.id, duplicate=False)
        except IntegrityError:
            if self._event_exists(razorpay_event_id):
                self._record_duplicate(razorpay_event_id)
                return IngressResult(event_id=None, duplicate=True)
            raise

    def record_rejection(self, *, action: str, correlation_id: str, metadata: dict) -> None:
        """Audit rejected requests without creating a webhook event or job."""
        with self._session_factory() as session:
            with session.begin():
                session.add(
                    AuditLog(
                        actor="razorpay_webhook",
                        action=action,
                        entity_type="webhook_request",
                        entity_id=None,
                        correlation_id=correlation_id,
                        metadata_json=metadata,
                    )
                )

    def _event_exists(self, razorpay_event_id: str) -> bool:
        with self._session_factory() as session:
            return session.scalar(
                select(WebhookEvent.id).where(
                    WebhookEvent.merchant_id == self._merchant_id,
                    WebhookEvent.razorpay_event_id == razorpay_event_id,
                )
            ) is not None

    def _record_duplicate(self, razorpay_event_id: str) -> None:
        with self._session_factory() as session:
            with session.begin():
                session.add(
                    AuditLog(
                        actor="razorpay_webhook",
                        action="WEBHOOK_DUPLICATE",
                        entity_type="webhook_event",
                        entity_id=None,
                        correlation_id=razorpay_event_id,
                        metadata_json={},
                    )
                )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

