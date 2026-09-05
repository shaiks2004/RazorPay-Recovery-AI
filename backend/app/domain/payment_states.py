from __future__ import annotations

PAYMENT_STATE_RANK = {
    "UNKNOWN": 0,
    "FAILED": 1,
    "AUTHORIZED": 2,
    "CAPTURED": 3,
}


def advance_payment_status(current: str, incoming: str) -> str:
    """Return a monotonic state independent of webhook delivery order."""
    if current not in PAYMENT_STATE_RANK or incoming not in PAYMENT_STATE_RANK:
        raise ValueError("unsupported payment status")
    return incoming if PAYMENT_STATE_RANK[incoming] > PAYMENT_STATE_RANK[current] else current


def advance_order_status(current: str, incoming: str) -> str:
    """Paid is terminal for the limited V1 order projection."""
    if current == "PAID" or incoming == "PAID":
        return "PAID"
    return "UNKNOWN"
