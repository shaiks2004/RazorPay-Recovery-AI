from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import MerchantPolicy, Order, Payment, PolicyDecision, RecoveryAssessment, RecoveryCase
from app.domain.policy import CandidateAction, PolicyDecisionKind, PolicyReasonCode


@dataclass(frozen=True)
class GateResult:
    decision: PolicyDecisionKind
    reason_codes: tuple[str, ...]
    facts: dict


def evaluate(*, session: Session, recovery_case: RecoveryCase, assessment: RecoveryAssessment, policy: MerchantPolicy | None, now: datetime) -> GateResult:
    """Fail-closed, ordered policy evaluation using current locked database facts."""
    payment = session.get(Payment, recovery_case.original_payment_id)
    order = session.get(Order, recovery_case.order_id) if recovery_case.order_id else None
    facts = {"payment_amount": recovery_case.amount, "currency": recovery_case.currency, "assessment_id": str(assessment.id)}
    if policy is None:
        return _deny(PolicyReasonCode.POLICY_MISSING, facts)
    facts["policy_version"] = policy.policy_version
    if not policy.enabled:
        return _deny(PolicyReasonCode.POLICY_DISABLED, facts)
    if recovery_case.status != "ASSESSING":
        return _deny(PolicyReasonCode.CASE_CLOSED, facts)
    if payment is None:
        return _escalate(PolicyReasonCode.INSUFFICIENT_FACTS, facts)
    if payment.status == "CAPTURED":
        return _deny(PolicyReasonCode.PAYMENT_ALREADY_CAPTURED, facts)
    if order is not None and order.status == "PAID":
        return _deny(PolicyReasonCode.ORDER_ALREADY_PAID, facts)
    if recovery_case.amount <= 0:
        return _deny(PolicyReasonCode.NON_POSITIVE_AMOUNT, facts)
    if not recovery_case.currency:
        return _deny(PolicyReasonCode.CURRENCY_MISSING, facts)
    try:
        action = CandidateAction(assessment.candidate_action)
    except ValueError:
        return _escalate(PolicyReasonCode.INVALID_CANDIDATE_ACTION, facts)
    if action is not CandidateAction.PREPARE_PAYMENT_LINK:
        return _deny(PolicyReasonCode.ACTION_NOT_ALLOWLISTED, facts)
    if action.value not in policy.allowed_actions:
        return _deny(PolicyReasonCode.ACTION_NOT_ALLOWLISTED, facts)
    if recovery_case.amount > policy.max_amount_minor_units:
        return _deny(PolicyReasonCode.AMOUNT_LIMIT_EXCEEDED, facts)
    probability = Decimal(assessment.recovery_probability)
    if probability < Decimal(policy.min_recovery_probability):
        return _deny(PolicyReasonCode.PROBABILITY_BELOW_THRESHOLD, facts)
    if assessment.expected_recovery_value < policy.min_expected_recovery_value_minor_units:
        return _deny(PolicyReasonCode.EXPECTED_VALUE_BELOW_THRESHOLD, facts)

    approvals = list(session.scalars(select(PolicyDecision).where(PolicyDecision.recovery_case_id == recovery_case.id, PolicyDecision.decision == PolicyDecisionKind.APPROVE.value).with_for_update()))
    facts["authorization_attempt_count"] = len(approvals)
    if len(approvals) >= policy.max_attempts:
        return _deny(PolicyReasonCode.ATTEMPT_LIMIT_EXCEEDED, facts)
    if policy.cooldown_seconds and approvals:
        last = max(_as_utc(item.created_at) for item in approvals)
        if _as_utc(now) - last < timedelta(seconds=policy.cooldown_seconds):
            return _deny(PolicyReasonCode.COOLDOWN_ACTIVE, facts)
    start = _as_utc(now).replace(hour=0, minute=0, second=0, microsecond=0)
    used = session.scalar(select(func.coalesce(func.sum(PolicyDecision.authorized_amount), 0)).where(PolicyDecision.merchant_id == recovery_case.merchant_id, PolicyDecision.decision == PolicyDecisionKind.APPROVE.value, PolicyDecision.created_at >= start)) or 0
    facts["daily_authorized_amount"] = int(used)
    if int(used) >= policy.daily_recovery_budget_minor_units:
        return _deny(PolicyReasonCode.BUDGET_EXHAUSTED, facts)
    if int(used) + recovery_case.amount > policy.daily_recovery_budget_minor_units:
        return _deny(PolicyReasonCode.BUDGET_LIMIT_EXCEEDED, facts)
    return GateResult(PolicyDecisionKind.APPROVE, (PolicyReasonCode.ALL_POLICY_CHECKS_PASSED.value,), facts)


def _deny(reason: PolicyReasonCode, facts: dict) -> GateResult:
    return GateResult(PolicyDecisionKind.DENY, (reason.value,), facts)


def _escalate(reason: PolicyReasonCode, facts: dict) -> GateResult:
    return GateResult(PolicyDecisionKind.ESCALATE, (reason.value,), facts)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
