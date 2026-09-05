from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, EvaluationRun, EvaluationRunCase, ExecutionRequest, MetricSnapshot, PaymentLink, PolicyDecision, RecoveryAssessment, RecoveryAttribution, RecoveryCase

SCHEMA_VERSION = "metrics-v1"
RUN_TYPES = {"SYNTHETIC", "TEST_MODE", "MIXED"}


def create_run(*, session: Session, run_name: str, dataset_id: str, dataset_version: str, run_type: str, configuration: dict, now: datetime) -> EvaluationRun:
    if run_type not in RUN_TYPES or not run_name or not dataset_id or not dataset_version:
        raise ValueError("invalid evaluation run identity")
    existing = session.scalar(select(EvaluationRun).where(EvaluationRun.run_name == run_name, EvaluationRun.dataset_id == dataset_id, EvaluationRun.dataset_version == dataset_version).with_for_update())
    if existing: return existing
    run = EvaluationRun(run_name=run_name, dataset_id=dataset_id, dataset_version=dataset_version, run_type=run_type, configuration_snapshot={"schema_version": SCHEMA_VERSION, **configuration}, started_at=now)
    session.add(run); session.flush()
    _audit(session, run, "EVALUATION_STARTED")
    return run


def add_case(*, session: Session, run_id, recovery_case_id, source_scope: str) -> EvaluationRunCase:
    run = session.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update())
    if run is None or source_scope not in ("SYNTHETIC", "TEST_MODE") or (run.run_type != "MIXED" and run.run_type != source_scope):
        raise ValueError("evaluation source scope is invalid for run")
    existing = session.scalar(select(EvaluationRunCase).where(EvaluationRunCase.evaluation_run_id == run_id, EvaluationRunCase.recovery_case_id == recovery_case_id).with_for_update())
    if existing: return existing
    member = EvaluationRunCase(evaluation_run_id=run_id, recovery_case_id=recovery_case_id, source_scope=source_scope)
    session.add(member)
    return member


def evaluate_run(*, session: Session, run_id, now: datetime) -> MetricSnapshot | None:
    # The worker/test session deliberately disables autoflush; memberships must be
    # visible before aggregation and a new immutable snapshot before replay lookup.
    session.flush()
    run = session.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update())
    if run is None: return None
    existing = session.scalar(select(MetricSnapshot).where(MetricSnapshot.evaluation_run_id == run.id, MetricSnapshot.schema_version == SCHEMA_VERSION).with_for_update())
    if existing: return existing
    members = list(session.scalars(select(EvaluationRunCase).where(EvaluationRunCase.evaluation_run_id == run.id)))
    if run.run_type != "MIXED" and any(member.source_scope != run.run_type for member in members):
        raise ValueError("evaluation run contains mixed source facts")
    ids = [member.recovery_case_id for member in members]
    metrics = _aggregate(session, ids)
    snapshot = MetricSnapshot(evaluation_run_id=run.id, schema_version=SCHEMA_VERSION, source_scope=run.run_type, metrics_json={"schema_version": SCHEMA_VERSION, "run_id": str(run.id), "dataset_id": run.dataset_id, "dataset_version": run.dataset_version, "source_scope": run.run_type, "metrics": metrics})
    session.add(snapshot)
    session.flush()
    run.status, run.completed_at = "COMPLETED", now
    _audit(session, run, "METRICS_SNAPSHOT_CREATED")
    _audit(session, run, "EVALUATION_COMPLETED")
    return snapshot


def _aggregate(session: Session, ids: list) -> dict:
    cases = list(session.scalars(select(RecoveryCase).where(RecoveryCase.id.in_(ids)))) if ids else []
    assessments = list(session.scalars(select(RecoveryAssessment).where(RecoveryAssessment.recovery_case_id.in_(ids)))) if ids else []
    decisions = list(session.scalars(select(PolicyDecision).where(PolicyDecision.recovery_case_id.in_(ids)))) if ids else []
    executions = list(session.scalars(select(ExecutionRequest).where(ExecutionRequest.recovery_case_id.in_(ids)))) if ids else []
    execution_ids = [x.id for x in executions]
    links = list(session.scalars(select(PaymentLink).where(PaymentLink.execution_request_id.in_(execution_ids)))) if execution_ids else []
    attributions = list(session.scalars(select(RecoveryAttribution).where(RecoveryAttribution.recovery_case_id.in_(ids)))) if ids else []
    eligible = [x for x in cases if x.eligibility_result == "ELIGIBLE"]
    approved = [x for x in decisions if x.decision == "APPROVE"]
    denied = [x for x in decisions if x.decision == "DENY"]
    escalated = [x for x in decisions if x.decision == "ESCALATE"]
    verified = [x for x in attributions if x.attribution_status == "VERIFIED"]
    rejected = [x for x in attributions if x.attribution_status == "REJECTED"]
    m = _definitions()
    def put(name, value, numerator=None, denominator=None):
        m[name]["value"] = value; m[name]["numerator"] = numerator if numerator is not None else value; m[name]["denominator"] = denominator
    put("total_cases", len(cases)); put("eligible_cases", len(eligible)); put("ineligible_cases", len(cases)-len(eligible)); put("assessment_count", len(assessments))
    put("policy_approval_count", len(approved)); put("policy_denial_count", len(denied)); put("policy_escalation_count", len(escalated)); put("execution_request_count", len(executions)); put("payment_link_created_count", len(links)); put("captured_payment_count", sum(x.captured_payment_id is not None for x in links)); put("verified_attribution_count", len(verified)); put("attribution_rejection_count", len(rejected))
    eligible_money = sum(x.amount for x in eligible); expected = sum(x.expected_recovery_value for x in assessments); authorized = sum(x.authorized_amount for x in approved); recovered = sum(x.attributed_amount_minor_units or 0 for x in verified)
    put("eligible_revenue_minor_units", eligible_money); put("approved_authorization_value_minor_units", authorized); put("expected_recovery_value_minor_units", expected); put("verified_recovered_revenue_minor_units", recovered); put("unrecovered_eligible_revenue_minor_units", max(0, eligible_money-recovered)); put("baseline_potential_revenue_minor_units", eligible_money)
    put("recovery_rate_bps", _bps(len(verified), len(eligible)), len(verified), len(eligible)); put("approval_rate_bps", _bps(len(approved), len(assessments)), len(approved), len(assessments)); put("denial_rate_bps", _bps(len(denied), len(assessments)), len(denied), len(assessments)); put("escalation_rate_bps", _bps(len(escalated), len(assessments)), len(escalated), len(assessments)); put("execution_request_rate_bps", _bps(len(executions), len(approved)), len(executions), len(approved)); put("payment_link_creation_rate_bps", _bps(len(links), len(executions)), len(links), len(executions)); put("execution_failure_rate_bps", _bps(sum(x.status == "FAILED" for x in executions), len(executions)), sum(x.status == "FAILED" for x in executions), len(executions)); put("reconciliation_rate_bps", _bps(sum(x.status == "RECONCILING" for x in executions), len(executions)), sum(x.status == "RECONCILING" for x in executions), len(executions)); put("attribution_rate_bps", _bps(len(verified), len(links)), len(verified), len(links))
    put("blocked_execution_count", sum(x.status == "BLOCKED" for x in executions)); put("already_paid_block_count", sum(x.failure_code in ("EXECUTION_BLOCKED_PAYMENT_CAPTURED", "EXECUTION_BLOCKED_ORDER_PAID") for x in executions)); put("amount_mismatch_rejection_count", sum("ATTRIBUTION_AMOUNT_MISMATCH" in x.evidence.get("reason_codes", []) for x in rejected)); put("currency_mismatch_rejection_count", sum("ATTRIBUTION_CURRENCY_MISMATCH" in x.evidence.get("reason_codes", []) for x in rejected)); put("attribution_conflict_count", 0); put("duplicate_execution_prevented_count", None); put("duplicate_attribution_prevented_count", None)
    return m


def _definitions() -> dict:
    return {name: {"unit": unit, "formula": formula, "source": "persisted_facts"} for name, unit, formula in [
        ("total_cases", "count", "run case memberships"), ("eligible_cases", "count", "eligibility_result=ELIGIBLE"), ("ineligible_cases", "count", "total_cases-eligible_cases"), ("assessment_count", "count", "persisted assessments"), ("policy_approval_count", "count", "decision=APPROVE"), ("policy_denial_count", "count", "decision=DENY"), ("policy_escalation_count", "count", "decision=ESCALATE"), ("execution_request_count", "count", "persisted execution requests"), ("payment_link_created_count", "count", "persisted execution-mapped links"), ("captured_payment_count", "count", "payment links with captured payment"), ("verified_attribution_count", "count", "attribution_status=VERIFIED"), ("attribution_rejection_count", "count", "attribution_status=REJECTED"), ("eligible_revenue_minor_units", "minor_units", "sum eligible case amount"), ("approved_authorization_value_minor_units", "minor_units", "sum approved authorized_amount"), ("expected_recovery_value_minor_units", "minor_units", "sum assessment expected_recovery_value"), ("verified_recovered_revenue_minor_units", "minor_units", "sum verified attribution amounts"), ("unrecovered_eligible_revenue_minor_units", "minor_units", "max(eligible revenue-verified recovered revenue,0)"), ("baseline_potential_revenue_minor_units", "minor_units", "all eligible case revenue; potential only"), ("recovery_rate_bps", "basis_points", "verified_attribution_count/eligible_cases"), ("approval_rate_bps", "basis_points", "approvals/assessments"), ("denial_rate_bps", "basis_points", "denials/assessments"), ("escalation_rate_bps", "basis_points", "escalations/assessments"), ("execution_request_rate_bps", "basis_points", "execution requests/approvals"), ("payment_link_creation_rate_bps", "basis_points", "links/execution requests"), ("execution_failure_rate_bps", "basis_points", "failed executions/execution requests"), ("reconciliation_rate_bps", "basis_points", "reconciling executions/execution requests"), ("attribution_rate_bps", "basis_points", "verified attributions/payment links"), ("blocked_execution_count", "count", "execution status BLOCKED"), ("already_paid_block_count", "count", "already-paid execution failure codes"), ("amount_mismatch_rejection_count", "count", "rejected attribution evidence"), ("currency_mismatch_rejection_count", "count", "rejected attribution evidence"), ("attribution_conflict_count", "count", "not yet reliably queryable"), ("duplicate_execution_prevented_count", "count_or_null", "not observable from current facts"), ("duplicate_attribution_prevented_count", "count_or_null", "not observable from current facts") ]}

def _bps(n: int, d: int) -> int: return 0 if not d else n * 10000 // d
def _audit(session, run, action): session.add(AuditLog(actor="evaluation_engine", action=action, entity_type="evaluation_run", entity_id=run.id, merchant_id=run.merchant_id, correlation_id=str(run.id), metadata_json={"evaluation_run_id": str(run.id), "dataset_id": run.dataset_id, "dataset_version": run.dataset_version, "schema_version": SCHEMA_VERSION}))
