from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.services.webhook_ingress import WebhookIngressService, utc_now

router = APIRouter()
logger = logging.getLogger(__name__)


def verify_razorpay_signature(*, raw_body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def ingress_service(request: Request) -> WebhookIngressService:
    return request.app.state.webhook_ingress_service


@router.post("/webhooks/razorpay", status_code=202)
async def receive_razorpay_webhook(request: Request) -> JSONResponse:
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    razorpay_event_id = request.headers.get("x-razorpay-event-id")
    service = ingress_service(request)
    correlation_id = razorpay_event_id or "missing-event-id"

    if not signature or not verify_razorpay_signature(
        raw_body=raw_body,
        signature=signature or "",
        secret=request.app.state.settings.razorpay_webhook_secret,
    ):
        try:
            service.record_rejection(
                action="WEBHOOK_REJECTED_SIGNATURE",
                correlation_id=correlation_id,
                metadata={"event_id_present": razorpay_event_id is not None},
            )
        except SQLAlchemyError:
            logger.warning("Could not persist rejected webhook audit record")
        return JSONResponse(status_code=401, content={"detail": "invalid webhook signature"})

    if not razorpay_event_id:
        service.record_rejection(
            action="WEBHOOK_REJECTED_EVENT_ID",
            correlation_id=correlation_id,
            metadata={},
        )
        return JSONResponse(status_code=400, content={"detail": "missing x-razorpay-event-id"})

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        service.record_rejection(
            action="WEBHOOK_REJECTED_MALFORMED",
            correlation_id=razorpay_event_id,
            metadata={"raw_body_sha256": hashlib.sha256(raw_body).hexdigest()},
        )
        return JSONResponse(status_code=400, content={"detail": "malformed JSON payload"})

    if not isinstance(payload, dict):
        service.record_rejection(
            action="WEBHOOK_REJECTED_MALFORMED",
            correlation_id=razorpay_event_id,
            metadata={"reason": "JSON payload must be an object"},
        )
        return JSONResponse(status_code=400, content={"detail": "JSON payload must be an object"})

    try:
        result = service.accept(
            razorpay_event_id=razorpay_event_id,
            raw_body=raw_body,
            payload=payload,
            received_at=utc_now(),
        )
    except SQLAlchemyError:
        logger.exception("Webhook durable acceptance failed", extra={"event_id": razorpay_event_id})
        return JSONResponse(status_code=500, content={"detail": "webhook persistence failure"})

    if result.duplicate:
        return JSONResponse(status_code=202, content={"status": "duplicate"})
    return JSONResponse(status_code=202, content={"status": "accepted", "event_id": str(result.event_id)})

