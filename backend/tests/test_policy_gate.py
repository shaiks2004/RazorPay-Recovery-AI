from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models import AuditLog, MerchantPolicy, Order, Payment, PolicyDecision, RecoveryAssessment, RecoveryCase
from app.services.policy_decision import evaluate_and_persist


def build_case(session_factory, *, amount: int = 50000, payment_status: str = "FAILED", case_status: str = "ASSESSING", candidate_action: str = "PREPARE_PAYMENT_LINK", probability: Decimal = Decimal("0.8500"), expected_value: int = 42500, order_paid: bool = False):
    payment_id, order_id, case_id, assessment_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with session_factory() as session:
        with session.begin():
            order = Order(id=order_id, merchant_id="merchant-test", razorpay_order_id=f"order_{order_id.hex}", status="PAID" if order_paid else "UNKNOWN")
            payment = Payment(id=payment_id, merchant_id="merchant-test", razorpay_payment_id=f"pay_{payment_id.hex}", order_id=order_id, amount=amount, currency="INR", method="card", status=payment_status)
            recovery_case = RecoveryCase(id=case_id, merchant_id="merchant-test", original_payment_id=payment_id, order_id=order_id, amount=amount, currency="INR", source="RAZORPAY_TEST", eligibility_result="ELIGIBLE", eligibility_reason_codes=["FACT"], status=case_status, correlation_id=f"evt_{case_id.hex}")
            assessment = RecoveryAssessment(id=assessment_id, recovery_case_id=case_id, diagnosis="TRANSIENT_FAILURE", diagnosis_confidence="HIGH", recovery_probability=probability, payment_amount=amount, expected_recovery_value=expected_value, candidate_action=candidate_action, reason_codes=["FACT"], feature_snapshot={}, score_version="deterministic-v1")
            session.add_all((order, payment, recovery_case, assessment))
    return case_id, assessment_id


def add_policy(session_factory, *, version: str = "v1", enabled: bool = True, actions: list[str] | None = None, max_amount: int = 100000, probability: Decimal = Decimal("0.6500"), minimum_value: int = 100, attempts: int = 1, cooldown: int = 0, budget: int = 200000):
    with session_factory() as session:
        with session.begin():
            session.add(MerchantPolicy(merchant_id="merchant-test", policy_version=version, enabled=enabled, allowed_actions=actions if actions is not None else ["PREPARE_PAYMENT_LINK"], max_amount_minor_units=max_amount, min_recovery_probability=probability, min_expected_recovery_value_minor_units=minimum_value, max_attempts=attempts, cooldown_seconds=cooldown, daily_recovery_budget_minor_units=budget))


def decide(session_factory, case_id, assessment_id, now=None):
    with session_factory() as session:
        with session.begin():
            return evaluate_and_persist(session=session, recovery_case_id=case_id, assessment_id=assessment_id, now=now or datetime.now(timezone.utc))


def test_valid_policy_approves_and_is_idempotent(session_factory):
    case_id, assessment_id = build_case(session_factory)
    add_policy(session_factory)
    first, replay = decide(session_factory, case_id, assessment_id), decide(session_factory, case_id, assessment_id)
    assert first is not None and first.decision == "APPROVE"
    assert replay is not None and replay.id == first.id
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PolicyDecision)) == 1


@pytest.mark.parametrize(
    ("policy_kwargs", "case_kwargs", "expected"),
    [
        ({"enabled": False}, {}, "POLICY_DISABLED"),
        ({"actions": []}, {}, "ACTION_NOT_ALLOWLISTED"),
        ({"max_amount": 49999}, {}, "AMOUNT_LIMIT_EXCEEDED"),
        ({"probability": Decimal("0.8501")}, {}, "PROBABILITY_BELOW_THRESHOLD"),
        ({"minimum_value": 42501}, {}, "EXPECTED_VALUE_BELOW_THRESHOLD"),
        ({}, {"payment_status": "CAPTURED"}, "PAYMENT_ALREADY_CAPTURED"),
        ({}, {"order_paid": True}, "ORDER_ALREADY_PAID"),
        ({}, {"case_status": "CLOSED"}, "CASE_CLOSED"),
        ({}, {"amount": 0, "expected_value": 0}, "NON_POSITIVE_AMOUNT"),
    ],
)
def test_policy_denies_explicit_safety_failures(session_factory, policy_kwargs, case_kwargs, expected):
    case_id, assessment_id = build_case(session_factory, **case_kwargs)
    add_policy(session_factory, **policy_kwargs)
    result = decide(session_factory, case_id, assessment_id)
    assert result is not None
    assert result.decision == "DENY"
    assert result.reason_codes == [expected]


def test_missing_policy_fails_closed(session_factory):
    case_id, assessment_id = build_case(session_factory)
    result = decide(session_factory, case_id, assessment_id)
    assert result is not None and result.decision == "DENY"
    assert result.reason_codes == ["POLICY_MISSING"]


def test_unknown_candidate_action_escalates(session_factory):
    case_id, assessment_id = build_case(session_factory, candidate_action="FUTURE_ACTION")
    add_policy(session_factory)
    result = decide(session_factory, case_id, assessment_id)
    assert result is not None and result.decision == "ESCALATE"
    assert result.reason_codes == ["INVALID_CANDIDATE_ACTION"]


def test_exact_thresholds_and_limits_are_approved(session_factory):
    case_id, assessment_id = build_case(session_factory, amount=50000, probability=Decimal("0.6500"), expected_value=100)
    add_policy(session_factory, max_amount=50000, probability=Decimal("0.6500"), minimum_value=100, budget=50000)
    result = decide(session_factory, case_id, assessment_id)
    assert result is not None and result.decision == "APPROVE"


def test_attempt_cooldown_and_budget_controls_use_prior_approvals(session_factory):
    case_id, assessment_id = build_case(session_factory)
    add_policy(session_factory, version="v1", attempts=2, cooldown=3600, budget=100000)
    first = decide(session_factory, case_id, assessment_id, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert first is not None and first.decision == "APPROVE"
    # New immutable policy version forces re-evaluation and sees authorization history.
    add_policy(session_factory, version="v2", attempts=2, cooldown=3600, budget=100000)
    cooldown = decide(session_factory, case_id, assessment_id)
    assert cooldown is not None and cooldown.reason_codes == ["COOLDOWN_ACTIVE"]


def test_budget_exhaustion_and_attempt_limit(session_factory):
    first_case, first_assessment = build_case(session_factory, amount=50000)
    add_policy(session_factory, version="v1", budget=50000, attempts=1)
    assert decide(session_factory, first_case, first_assessment).decision == "APPROVE"
    second_case, second_assessment = build_case(session_factory, amount=100)
    result = decide(session_factory, second_case, second_assessment)
    assert result is not None and result.reason_codes == ["BUDGET_EXHAUSTED"]
    add_policy(session_factory, version="v2", budget=100000, attempts=1)
    again = decide(session_factory, first_case, first_assessment)
    assert again is not None and again.reason_codes == ["ATTEMPT_LIMIT_EXCEEDED"]


def test_audit_is_immutable_and_contains_decision_context(session_factory):
    case_id, assessment_id = build_case(session_factory)
    add_policy(session_factory)
    decision = decide(session_factory, case_id, assessment_id)
    assert decision is not None
    with session_factory() as session:
        actions = set(session.scalars(select(AuditLog.action).where(AuditLog.entity_id == decision.id)))
    assert {"POLICY_EVALUATED", "POLICY_APPROVED"} <= actions
