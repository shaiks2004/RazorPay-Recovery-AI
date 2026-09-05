from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Order, Payment


@dataclass(frozen=True)
class EligibilityDecision:
    result: str
    reason_codes: tuple[str, ...]


def evaluate_recovery_eligibility(*, payment: Payment | None, order: Order | None, has_active_case: bool) -> EligibilityDecision:
    """A conservative, explainable gate for opening real recovery work."""
    if payment is None:
        return EligibilityDecision("INSUFFICIENT_FACTS", ("PAYMENT_PROJECTION_MISSING",))
    if payment.status == "CAPTURED":
        return EligibilityDecision("ALREADY_PAID", ("PAYMENT_CAPTURED",))
    if order is not None and order.status == "PAID":
        return EligibilityDecision("ALREADY_PAID", ("ORDER_PAID",))
    if has_active_case:
        return EligibilityDecision("ALREADY_RECOVERING", ("ACTIVE_RECOVERY_CASE_EXISTS",))
    if payment.status != "FAILED":
        return EligibilityDecision("INELIGIBLE", ("PAYMENT_NOT_FAILED",))
    if payment.amount <= 0:
        return EligibilityDecision("INELIGIBLE", ("NON_POSITIVE_AMOUNT",))
    if not payment.currency:
        return EligibilityDecision("INSUFFICIENT_FACTS", ("PAYMENT_CURRENCY_MISSING",))
    return EligibilityDecision("ELIGIBLE", ("FAILED_PAYMENT_UNPAID", "POSITIVE_OUTSTANDING_AMOUNT"))
