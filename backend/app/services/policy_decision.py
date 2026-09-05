from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, MerchantPolicy, PolicyDecision, RecoveryAssessment, RecoveryCase
from app.domain.policy import PolicyDecisionKind
from app.services.policy_gate import evaluate


def evaluate_and_persist(*, session: Session, recovery_case_id, assessment_id, now: datetime) -> PolicyDecision | None:
    recovery_case = session.scalar(select(RecoveryCase).where(RecoveryCase.id == recovery_case_id).with_for_update())
    assessment = session.scalar(select(RecoveryAssessment).where(RecoveryAssessment.id == assessment_id).with_for_update())
    if recovery_case is None or assessment is None:
        return None
    policy = session.scalar(select(MerchantPolicy).where(MerchantPolicy.merchant_id == recovery_case.merchant_id).order_by(MerchantPolicy.created_at.desc()).with_for_update())
    version = policy.policy_version if policy else "MISSING"
    existing = session.scalar(select(PolicyDecision).where(PolicyDecision.recovery_case_id == recovery_case.id, PolicyDecision.assessment_id == assessment.id, PolicyDecision.policy_version == version).with_for_update())
    if existing is not None:
        return existing
    result = evaluate(session=session, recovery_case=recovery_case, assessment=assessment, policy=policy, now=now)
    decision = PolicyDecision(
        recovery_case_id=recovery_case.id, assessment_id=assessment.id, merchant_id=recovery_case.merchant_id,
        policy_version=version, candidate_action=assessment.candidate_action, decision=result.decision.value,
        reason_codes=list(result.reason_codes), evaluated_facts=result.facts,
        authorized_amount=recovery_case.amount if result.decision is PolicyDecisionKind.APPROVE else 0,
    )
    session.add(decision)
    session.flush()
    action = {PolicyDecisionKind.APPROVE: "POLICY_APPROVED", PolicyDecisionKind.DENY: "POLICY_DENIED", PolicyDecisionKind.ESCALATE: "POLICY_ESCALATED"}[result.decision]
    session.add(AuditLog(actor="policy_gate", action="POLICY_EVALUATED", entity_type="policy_decision", entity_id=decision.id, merchant_id=recovery_case.merchant_id, correlation_id=recovery_case.correlation_id, metadata_json={"policy_version": version, "assessment_id": str(assessment.id), "candidate_action": assessment.candidate_action, "decision": result.decision.value, "reason_codes": list(result.reason_codes)}))
    session.add(AuditLog(actor="policy_gate", action=action, entity_type="policy_decision", entity_id=decision.id, merchant_id=recovery_case.merchant_id, correlation_id=recovery_case.correlation_id, metadata_json={"reason_codes": list(result.reason_codes)}))
    return decision
