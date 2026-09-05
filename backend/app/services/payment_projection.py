from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Order, Payment, PaymentAttempt, PaymentLink, WebhookEvent
from app.domain.payment_states import advance_order_status, advance_payment_status

SUPPORTED_EVENTS = {
    "payment.failed": "FAILED",
    "payment.authorized": "AUTHORIZED",
    "payment.captured": "CAPTURED",
    "order.paid": "CAPTURED",
    "payment_link.paid": "CAPTURED",
}


class ProjectionPayloadError(Exception):
    """A signed payload is syntactically valid but cannot support a safe projection."""


@dataclass(frozen=True)
class PaymentSnapshot:
    razorpay_payment_id: str
    razorpay_order_id: str | None
    amount: int
    currency: str
    method: str | None
    status: str
    error_code: str | None
    error_reason: str | None


@dataclass(frozen=True)
class OrderSnapshot:
    razorpay_order_id: str
    amount: int
    amount_paid: int
    amount_due: int
    currency: str
    receipt: str | None
    status: str


def project_event(*, session: Session, event: WebhookEvent, now: datetime) -> str:
    """Apply one immutable webhook fact to derived projections in the caller's transaction."""
    if event.event_name not in SUPPORTED_EVENTS:
        event.processing_status = "IGNORED_UNKNOWN"
        event.processed_at = now
        session.add(_event_audit(event, "WEBHOOK_PROJECTION_IGNORED_UNKNOWN", {}))
        return "IGNORED_UNKNOWN"

    expected_payment_status = SUPPORTED_EVENTS[event.event_name]
    payment = _parse_payment(event.payload_json, expected_payment_status)
    order_snapshot = _parse_order(event.payload_json) if event.event_name in ("order.paid", "payment_link.paid") else None
    if event.event_name in ("order.paid", "payment_link.paid") and payment.razorpay_order_id != order_snapshot.razorpay_order_id:
        raise ProjectionPayloadError("payment order_id does not match paid order id")

    order = _upsert_order(session=session, merchant_id=event.merchant_id, payment=payment, snapshot=order_snapshot, event=event)
    payment_projection = _upsert_payment(session=session, merchant_id=event.merchant_id, snapshot=payment, order=order, event=event)
    _upsert_attempt(session=session, payment=payment_projection, event=event, incoming_status=expected_payment_status)
    if event.event_name == "payment_link.paid":
        _upsert_paid_link(session=session, merchant_id=event.merchant_id, payment=payment_projection, event=event)

    event.processing_status = "PROJECTED"
    event.processed_at = now
    session.add(
        _event_audit(
            event,
            "WEBHOOK_PROJECTED",
            {"payment_id": str(payment_projection.id), "order_id": str(order.id) if order else None, "event_name": event.event_name},
        )
    )
    return "PROJECTED"


def _upsert_paid_link(*, session: Session, merchant_id: str, payment: Payment, event: WebhookEvent) -> PaymentLink:
    entity = _entity(event.payload_json, "payment_link")
    if entity.get("entity") != "payment_link":
        raise ProjectionPayloadError("payload payment_link entity type is invalid")
    link_id, reference_id = _required_str(entity, "id"), _required_str(entity, "reference_id")
    amount, currency, status = _required_non_negative_int(entity, "amount"), _required_str(entity, "currency"), _required_str(entity, "status").upper()
    if status != "PAID":
        raise ProjectionPayloadError("payload payment_link status does not match webhook event")
    link = session.scalar(select(PaymentLink).where(PaymentLink.merchant_id == merchant_id, PaymentLink.razorpay_payment_link_id == link_id).with_for_update())
    if link is None:
        link = PaymentLink(merchant_id=merchant_id, razorpay_payment_link_id=link_id, reference_id=reference_id, razorpay_order_id=payment.razorpay_order_id, captured_payment_id=payment.id, amount_minor_units=amount, currency=currency, status="PAID", source_webhook_event_id=event.id)
        session.add(link)
    else:
        if link.captured_payment_id is not None and link.captured_payment_id != payment.id:
            raise ProjectionPayloadError("payment link captured payment conflicts with existing projection")
        # The signed paid webhook is the provider fact. Keep the execution mapping but let
        # attribution compare its expected amount/currency/reference against this fact.
        link.reference_id, link.amount_minor_units, link.currency = reference_id, amount, currency
        link.razorpay_order_id, link.captured_payment_id, link.status, link.source_webhook_event_id = payment.razorpay_order_id, payment.id, "PAID", event.id
    return link


def _parse_payment(payload: Any, expected_status: str) -> PaymentSnapshot:
    entity = _entity(payload, "payment")
    if entity.get("entity") != "payment":
        raise ProjectionPayloadError("payload payment entity type is invalid")
    payment_id = _required_str(entity, "id")
    amount = _required_non_negative_int(entity, "amount")
    currency = _required_str(entity, "currency")
    status = _required_str(entity, "status").upper()
    if status != expected_status:
        raise ProjectionPayloadError("payload payment status does not match webhook event")
    order_id = entity.get("order_id")
    if order_id is not None and not isinstance(order_id, str):
        raise ProjectionPayloadError("payload order_id is invalid")
    return PaymentSnapshot(
        razorpay_payment_id=payment_id,
        razorpay_order_id=order_id,
        amount=amount,
        currency=currency,
        method=_optional_str(entity, "method"),
        status=status,
        error_code=_optional_str(entity, "error_code"),
        error_reason=_optional_str(entity, "error_reason"),
    )


def _parse_order(payload: Any) -> OrderSnapshot:
    entity = _entity(payload, "order")
    if entity.get("entity") != "order":
        raise ProjectionPayloadError("payload order entity type is invalid")
    status = _required_str(entity, "status").upper()
    if status != "PAID":
        raise ProjectionPayloadError("payload order status does not match order.paid event")
    return OrderSnapshot(
        razorpay_order_id=_required_str(entity, "id"),
        amount=_required_non_negative_int(entity, "amount"),
        amount_paid=_required_non_negative_int(entity, "amount_paid"),
        amount_due=_required_non_negative_int(entity, "amount_due"),
        currency=_required_str(entity, "currency"),
        receipt=_optional_str(entity, "receipt"),
        status=status,
    )


def _upsert_order(*, session: Session, merchant_id: str, payment: PaymentSnapshot, snapshot: OrderSnapshot | None, event: WebhookEvent) -> Order | None:
    external_order_id = snapshot.razorpay_order_id if snapshot else payment.razorpay_order_id
    if external_order_id is None or (snapshot is None and payment.status != "CAPTURED"):
        return None
    order = session.scalar(
        select(Order).where(Order.merchant_id == merchant_id, Order.razorpay_order_id == external_order_id).with_for_update()
    )
    if order is None:
        order = Order(merchant_id=merchant_id, razorpay_order_id=external_order_id)
        session.add(order)
        session.flush()
    if snapshot is not None:
        order.amount = snapshot.amount
        order.amount_paid = snapshot.amount_paid
        order.amount_due = snapshot.amount_due
        order.currency = snapshot.currency
        order.receipt = snapshot.receipt
        order.status = advance_order_status(order.status, snapshot.status)
    elif payment.status == "CAPTURED":
        order.status = advance_order_status(order.status, "PAID")
    order.source_webhook_event_id = event.id
    return order


def _upsert_payment(*, session: Session, merchant_id: str, snapshot: PaymentSnapshot, order: Order | None, event: WebhookEvent) -> Payment:
    payment = session.scalar(
        select(Payment)
        .where(Payment.merchant_id == merchant_id, Payment.razorpay_payment_id == snapshot.razorpay_payment_id)
        .with_for_update()
    )
    if payment is None:
        payment = Payment(
            merchant_id=merchant_id,
            razorpay_payment_id=snapshot.razorpay_payment_id,
            amount=snapshot.amount,
            currency=snapshot.currency,
            status="UNKNOWN",
        )
        session.add(payment)
        session.flush()
    if payment.amount != snapshot.amount or payment.currency != snapshot.currency:
        raise ProjectionPayloadError("payment amount or currency conflicts with existing projection")
    payment.order_id = order.id if order else payment.order_id
    payment.razorpay_order_id = snapshot.razorpay_order_id
    payment.method = snapshot.method or payment.method
    payment.status = advance_payment_status(payment.status, snapshot.status)
    if snapshot.status == "FAILED":
        payment.error_code = snapshot.error_code
        payment.error_reason = snapshot.error_reason
    payment.source_webhook_event_id = event.id
    return payment


def _upsert_attempt(*, session: Session, payment: Payment, event: WebhookEvent, incoming_status: str) -> PaymentAttempt:
    attempt = session.scalar(select(PaymentAttempt).where(PaymentAttempt.payment_id == payment.id).with_for_update())
    if attempt is None:
        attempt = PaymentAttempt(
            payment_id=payment.id,
            first_webhook_event_id=event.id,
            last_webhook_event_id=event.id,
            status=incoming_status,
        )
        session.add(attempt)
        return attempt
    attempt.status = advance_payment_status(attempt.status, incoming_status)
    attempt.last_webhook_event_id = event.id
    return attempt


def _entity(payload: Any, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProjectionPayloadError("webhook payload is not an object")
    candidate = payload.get("payload")
    if not isinstance(candidate, dict):
        raise ProjectionPayloadError("webhook payload container is missing")
    resource = candidate.get(name)
    if not isinstance(resource, dict) or not isinstance(resource.get("entity"), dict):
        raise ProjectionPayloadError(f"webhook {name} entity is missing")
    return resource["entity"]


def _required_str(entity: dict[str, Any], name: str) -> str:
    value = entity.get(name)
    if not isinstance(value, str) or not value:
        raise ProjectionPayloadError(f"required field {name} is missing or invalid")
    return value


def _optional_str(entity: dict[str, Any], name: str) -> str | None:
    value = entity.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProjectionPayloadError(f"optional field {name} is invalid")
    return value


def _required_non_negative_int(entity: dict[str, Any], name: str) -> int:
    value = entity.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProjectionPayloadError(f"required field {name} is missing or invalid")
    return value


def _event_audit(event: WebhookEvent, action: str, metadata: dict[str, Any]) -> AuditLog:
    return AuditLog(
        actor="background_worker",
        action=action,
        entity_type="webhook_event",
        entity_id=event.id,
        merchant_id=event.merchant_id,
        correlation_id=event.razorpay_event_id,
        metadata_json=metadata,
    )
