from __future__ import annotations
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from app.db.models import AIAdvisory, AuditLog, PolicyDecision
from app.domain.ai_advisor import AIAdvice
from app.integrations.ai_provider import AdvisorUnavailable, FakeAIAdvisor
from app.services.ai_advisor import advise_assessment, build_case_packet
from tests.test_policy_gate import add_policy, build_case, decide

VALID = AIAdvice("TRANSIENT_FAILURE", "PREPARE_PAYMENT_LINK", .82, ["TRANSIENT_PROVIDER_FAILURE", "POSITIVE_OUTSTANDING_BALANCE"], "Signals are consistent with a transient failure.", ["CUSTOMER_INTENT_UNKNOWN"])

def test_advice_is_minimized_hashed_and_idempotent(session_factory):
    case_id, assessment_id = build_case(session_factory); add_policy(session_factory)
    with session_factory() as s:
        with s.begin():
            result=advise_assessment(session=s,recovery_case_id=case_id,assessment_id=assessment_id,advisor=FakeAIAdvisor(VALID),confidence_threshold=.7,now=datetime.now(timezone.utc)); replay=advise_assessment(session=s,recovery_case_id=case_id,assessment_id=assessment_id,advisor=FakeAIAdvisor(VALID),confidence_threshold=.7,now=datetime.now(timezone.utc))
            assert result.id == replay.id and result.status == "COMPLETED" and len(result.input_hash)==64 and len(result.output_hash)==64
    with session_factory() as s:
        record=s.scalar(select(AIAdvisory)); assert "@" not in str(record.__dict__) and "test-webhook-secret" not in str(record.__dict__)

def test_invalid_contract_is_rejected():
    with pytest.raises(ValueError): AIAdvice.from_dict({})
    invalid=VALID.__dict__ | {"recommended_action":"APPROVE"}
    with pytest.raises(ValueError): AIAdvice.from_dict(invalid)
    with pytest.raises(ValueError): AIAdvice.from_dict(VALID.__dict__ | {"confidence":float("inf")})

def test_unavailable_ai_persists_fallback_and_policy_still_decides(session_factory):
    class Down:
        provider="FAKE"; model="down"
        def advise(self, packet): raise AdvisorUnavailable("timeout")
    case_id, assessment_id=build_case(session_factory); add_policy(session_factory, enabled=False)
    with session_factory() as s:
        with s.begin():
            advisory=advise_assessment(session=s,recovery_case_id=case_id,assessment_id=assessment_id,advisor=Down(),confidence_threshold=.7,now=datetime.now(timezone.utc))
            assert advisory.status == "UNAVAILABLE"
    decision=decide(session_factory,case_id,assessment_id)
    assert decision.decision == "DENY"

def test_conflict_is_observable_and_never_changes_policy_action(session_factory):
    case_id, assessment_id=build_case(session_factory, candidate_action="DO_NOTHING"); add_policy(session_factory)
    with session_factory() as s:
        with s.begin():
            result=advise_assessment(session=s,recovery_case_id=case_id,assessment_id=assessment_id,advisor=FakeAIAdvisor(VALID),confidence_threshold=.7,now=datetime.now(timezone.utc))
            assert "AI_DETERMINISTIC_RECOMMENDATION_CONFLICT" in result.uncertainty
    assert decide(session_factory,case_id,assessment_id).candidate_action == "DO_NOTHING"
