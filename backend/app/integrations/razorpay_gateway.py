from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class RazorpayGatewayError(Exception):
    pass


class RazorpayUnknownResult(RazorpayGatewayError):
    pass


class RazorpayValidationError(RazorpayGatewayError):
    pass


@dataclass(frozen=True)
class PaymentLinkRemote:
    link_id: str
    short_url: str | None
    status: str | None
    reference_id: str
    amount: int
    currency: str


class RazorpayGateway:
    """The only module that constructs Razorpay Test Mode HTTP requests."""

    base_url = "https://api.razorpay.com/v1"

    def __init__(self, *, mode: str, key_id: str | None, key_secret: str | None, client: httpx.Client | None = None) -> None:
        self._mode, self._key_id, self._key_secret = mode, key_id, key_secret
        self._client = client or httpx.Client(timeout=httpx.Timeout(10.0), base_url=self.base_url)

    def create_standard_payment_link(self, request: dict[str, Any]) -> PaymentLinkRemote:
        self._validate_test_mode()
        try:
            response = self._client.post("/payment_links", json=request, auth=(self._key_id, self._key_secret))
        except httpx.TimeoutException as exc:
            raise RazorpayUnknownResult("provider timeout") from exc
        except httpx.TransportError as exc:
            raise RazorpayUnknownResult("provider transport uncertainty") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise RazorpayUnknownResult("provider transient response")
        if response.status_code >= 400:
            raise RazorpayValidationError("provider rejected payment link")
        return self._parse(response.json())

    def fetch_payment_link(self, link_id: str) -> PaymentLinkRemote:
        self._validate_test_mode()
        try:
            response = self._client.get(f"/payment_links/{link_id}", auth=(self._key_id, self._key_secret))
        except httpx.TransportError as exc:
            raise RazorpayUnknownResult("provider reconciliation unavailable") from exc
        if response.status_code == 404:
            raise RazorpayValidationError("payment link not found")
        if response.status_code >= 400:
            raise RazorpayUnknownResult("provider reconciliation unavailable")
        return self._parse(response.json())

    def _validate_test_mode(self) -> None:
        if self._mode != "test" or not self._key_id or not self._key_id.startswith("rzp_test_") or not self._key_secret:
            raise RazorpayValidationError("EXECUTION_BLOCKED_LIVE_MODE")

    @staticmethod
    def _parse(body: Any) -> PaymentLinkRemote:
        if not isinstance(body, dict) or not all(isinstance(body.get(key), str) for key in ("id", "reference_id", "currency")) or not isinstance(body.get("amount"), int):
            raise RazorpayValidationError("malformed provider response")
        return PaymentLinkRemote(body["id"], body.get("short_url") if isinstance(body.get("short_url"), str) else None, body.get("status") if isinstance(body.get("status"), str) else None, body["reference_id"], body["amount"], body["currency"])
