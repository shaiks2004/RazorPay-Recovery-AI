from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.models import AIAdvisory, AuditLog, MerchantPolicy, Order, Payment, PolicyDecision, RecoveryAssessment, RecoveryCase
from app.domain.ai_advisor import RecoveryCasePacket
from app.integrations.ai_provider import AIAdvisor, AdvisorUnavailable, PROMPT_VERSION

def advise_assessment(*, session: Session, recovery_case_id, assessment_id, advisor: AIAdvisor, confidence_threshold: float, now: datetime) -> AIAdvisory | None:
    case = session.scalar(select(RecoveryCase).where(RecoveryCase.id == recovery_case_id).with_for_update()); assessment = session.get(RecoveryAssessment, assessment_id)
    if not case or not assessment: return None
    existing = session.scalar(select(AIAdvisory).where(AIAdvisory.recovery_case_id == case.id, AIAdvisory.assessment_id == assessment.id, AIAdvisory.prompt_version == PROMPT_VERSION, AIAdvisory.model == advisor.model).with_for_update())
    if existing: return existing
    packet = build_case_packet(session=session, recovery_case=case, assessment=assessment, now=now); input_hash = _hash(packet.__dict__)
    _audit(session, case, assessment, advisor, "AI_ADVISORY_REQUESTED", {"input_hash": input_hash})
    try: advice = advisor.advise(packet)
    except AdvisorUnavailable as error:
        record = AIAdvisory(recovery_case_id=case.id, assessment_id=assessment.id, schema_version=packet.schema_version, provider=advisor.provider, model=advisor.model, prompt_version=PROMPT_VERSION, input_hash=input_hash, status="UNAVAILABLE", failure_code="AI_ADVISORY_UNAVAILABLE")
        session.add(record); _audit(session, case, assessment, advisor, "AI_ADVISORY_FAILED", {"input_hash": input_hash, "status": record.status}); return record
    output = advice.__dict__; conflict = advice.recommended_action != assessment.candidate_action
    status = "COMPLETED" if advice.confidence >= confidence_threshold else "UNCERTAIN"
    record = AIAdvisory(recovery_case_id=case.id, assessment_id=assessment.id, schema_version=packet.schema_version, provider=advisor.provider, model=advisor.model, prompt_version=PROMPT_VERSION, input_hash=input_hash, output_hash=_hash(output), diagnosis_label=advice.diagnosis_label, recommended_action=advice.recommended_action, confidence=advice.confidence, rationale_codes=advice.rationale_codes, explanation=advice.explanation, uncertainty=advice.uncertainty + (["AI_DETERMINISTIC_RECOMMENDATION_CONFLICT"] if conflict else []), status=status)
    session.add(record); session.flush(); _audit(session, case, assessment, advisor, "AI_ADVISORY_COMPLETED", {"input_hash": input_hash, "output_hash": record.output_hash, "status": status, "conflict": conflict}); return record

def build_case_packet(*, session: Session, recovery_case: RecoveryCase, assessment: RecoveryAssessment, now: datetime) -> RecoveryCasePacket:
    payment = session.get(Payment, recovery_case.original_payment_id); order = session.get(Order, recovery_case.order_id) if recovery_case.order_id else None; policy = session.scalar(select(MerchantPolicy).where(MerchantPolicy.merchant_id == recovery_case.merchant_id).order_by(MerchantPolicy.created_at.desc())); decisions = list(session.scalars(select(PolicyDecision).where(PolicyDecision.recovery_case_id == recovery_case.id)))
    then = payment.updated_at if payment else recovery_case.created_at
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if then.tzinfo is None: then = then.replace(tzinfo=timezone.utc)
    seconds = max(0, int((current - then).total_seconds()))
    created = recovery_case.created_at if recovery_case.created_at.tzinfo else recovery_case.created_at.replace(tzinfo=timezone.utc)
    return RecoveryCasePacket(case_id=str(recovery_case.id), amount_minor_units=recovery_case.amount, currency=recovery_case.currency, payment_state=payment.status if payment else "UNKNOWN", order_state=order.status if order else "UNKNOWN", failure_code=recovery_case.initial_error_code, failure_reason=recovery_case.initial_error_reason, failure_source=recovery_case.source, payment_method=payment.method if payment else None, failure_count=1, case_age_seconds=max(0, int((current - created).total_seconds())), time_since_failure_seconds=seconds, deterministic_diagnosis=assessment.diagnosis, deterministic_diagnosis_confidence=assessment.diagnosis_confidence, deterministic_recovery_probability=str(assessment.recovery_probability), deterministic_expected_recovery_value_minor_units=assessment.expected_recovery_value, deterministic_candidate_action=assessment.candidate_action, policy_context={"policy_enabled": bool(policy and policy.enabled), "allowed_actions": policy.allowed_actions if policy else [], "min_recovery_probability": str(policy.min_recovery_probability) if policy else None, "max_amount_minor_units": policy.max_amount_minor_units if policy else None, "max_attempts": policy.max_attempts if policy else None, "cooldown_seconds": policy.cooldown_seconds if policy else None}, previous_interventions_count=0, previous_policy_decisions_summary={"APPROVE": sum(d.decision == "APPROVE" for d in decisions), "DENY": sum(d.decision == "DENY" for d in decisions), "ESCALATE": sum(d.decision == "ESCALATE" for d in decisions)})

def _hash(v): return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
def _audit(session, case, assessment, advisor, action, meta): session.add(AuditLog(actor="ai_advisor", action=action, entity_type="ai_advisory", entity_id=None, merchant_id=case.merchant_id, correlation_id=case.correlation_id, metadata_json={"recovery_case_id": str(case.id), "assessment_id": str(assessment.id), "provider": advisor.provider, "model": advisor.model, "prompt_version": PROMPT_VERSION, **meta}))
