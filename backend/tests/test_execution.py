from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import ExecutionRequest
from app.integrations.razorpay_gateway import PaymentLinkRemote, RazorpayUnknownResult
from app.services.execution import execute, request_execution
from tests.test_policy_gate import add_policy, build_case, decide


class Gateway:
    def __init__(self, result=None, error=None): self.result, self.error, self.calls = result, error, 0
    def create_standard_payment_link(self, request):
        self.calls += 1
        if self.error: raise self.error
        return self.result


def approved_execution(session_factory):
    case_id, assessment_id = build_case(session_factory)
    add_policy(session_factory)
    decision = decide(session_factory, case_id, assessment_id)
    assert decision is not None and decision.decision == "APPROVE"
    with session_factory() as session:
        with session.begin():
            request = request_execution(session=session, policy_decision_id=decision.id, now=datetime.now(timezone.utc), expiry_seconds=3600)
    return request


def test_approved_execution_creates_one_request_and_payment_link(session_factory):
    request = approved_execution(session_factory)
    assert request is not None and request.status == "REQUESTED"
    remote = PaymentLinkRemote("plink_1", "https://rzp.io/i/x", "created", request.reference_id, request.amount_minor_units, request.currency)
    gateway = Gateway(result=remote)
    with session_factory() as session:
        with session.begin(): execute(session=session, execution_id=request.id, gateway=gateway, now=datetime.now(timezone.utc))
    assert gateway.calls == 1
    with session_factory() as session:
        stored = session.get(ExecutionRequest, request.id)
        assert stored.status == "AWAITING_PAYMENT" and stored.razorpay_payment_link_id == "plink_1"
    with session_factory() as session:
        with session.begin(): replay = request_execution(session=session, policy_decision_id=request.policy_decision_id, now=datetime.now(timezone.utc), expiry_seconds=3600)
    assert replay.id == request.id


def test_timeout_reconciles_without_second_create(session_factory):
    request = approved_execution(session_factory)
    gateway = Gateway(error=RazorpayUnknownResult("timeout"))
    with session_factory() as session:
        with session.begin(): execute(session=session, execution_id=request.id, gateway=gateway, now=datetime.now(timezone.utc))
    with session_factory() as session:
        assert session.get(ExecutionRequest, request.id).status == "RECONCILING"
    with session_factory() as session:
        with session.begin(): execute(session=session, execution_id=request.id, gateway=gateway, now=datetime.now(timezone.utc))
    assert gateway.calls == 1


def test_denied_decision_creates_blocked_request_without_gateway_call(session_factory):
    case_id, assessment_id = build_case(session_factory)
    add_policy(session_factory, enabled=False)
    decision = decide(session_factory, case_id, assessment_id)
    with session_factory() as session:
        with session.begin(): request = request_execution(session=session, policy_decision_id=decision.id, now=datetime.now(timezone.utc), expiry_seconds=3600)
    assert request.status == "BLOCKED"
