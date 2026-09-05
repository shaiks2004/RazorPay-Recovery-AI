from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from collections import Counter
from sqlalchemy import func, select
from app.db.models import AIAdvisory, AuditLog, ExecutionRequest, EvaluationRun, MetricSnapshot, PaymentLink, PolicyDecision, RecoveryAssessment, RecoveryAttribution, RecoveryCase
from app.api.reporting_auth import ReportingPrincipal, principal
from app.schemas.reporting import OverviewResponse, RecoveryListResponse, RecoveryCaseDetail, EvaluationRunListResponse, EvaluationDetail, EvaluationMetricsResponse, AIReportingResponse, PolicyReportingResponse, ExecutionReportingResponse, AuditListResponse

router=APIRouter(prefix="/api/v1/reporting",tags=["reporting"])
def db(r:Request): return r.app.state.session_factory()
def page(limit:int,offset:int): return min(max(limit,1),100),max(offset,0)
@router.get("/overview",response_model=OverviewResponse)
def overview(request:Request,p:ReportingPrincipal=Depends(principal)):
    with db(request) as s:
        cases=s.scalar(select(func.count()).select_from(RecoveryCase).where(RecoveryCase.merchant_id==p.merchant_id)) or 0; eligible=s.scalar(select(func.count()).select_from(RecoveryCase).where(RecoveryCase.merchant_id==p.merchant_id,RecoveryCase.eligibility_result=="ELIGIBLE")) or 0; assessments=s.scalar(select(func.count()).select_from(RecoveryAssessment).join(RecoveryCase).where(RecoveryCase.merchant_id==p.merchant_id)) or 0; links=s.scalar(select(func.count()).select_from(PaymentLink).where(PaymentLink.merchant_id==p.merchant_id)) or 0; attrs=list(s.scalars(select(RecoveryAttribution).where(RecoveryAttribution.merchant_id==p.merchant_id,RecoveryAttribution.attribution_status=="VERIFIED")))
        return {"source_scope":"TEST_MODE","revenue_status":"SANDBOX_VERIFIED","total_recovery_cases":cases,"eligible_cases":eligible,"assessed_cases":assessments,"payment_links_created":links,"verified_attributions":len(attrs),"recovered_revenue_minor_units":sum(x.attributed_amount_minor_units or 0 for x in attrs),"active_cases":s.scalar(select(func.count()).select_from(RecoveryCase).where(RecoveryCase.merchant_id==p.merchant_id,RecoveryCase.status!="CLOSED")) or 0,"closed_cases":s.scalar(select(func.count()).select_from(RecoveryCase).where(RecoveryCase.merchant_id==p.merchant_id,RecoveryCase.status=="CLOSED")) or 0}
@router.get("/recoveries",response_model=RecoveryListResponse)
def recoveries(request:Request,status:str|None=None,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),p:ReportingPrincipal=Depends(principal)):
    limit,offset=page(limit,offset)
    with db(request) as s:
        q=select(RecoveryCase).where(RecoveryCase.merchant_id==p.merchant_id).order_by(RecoveryCase.created_at.desc(),RecoveryCase.id).limit(limit).offset(offset)
        if status:q=q.where(RecoveryCase.status==status)
        cases=list(s.scalars(q)); ids=[x.id for x in cases]
        assessments={x.recovery_case_id:x for x in s.scalars(select(RecoveryAssessment).where(RecoveryAssessment.recovery_case_id.in_(ids)).order_by(RecoveryAssessment.created_at.desc()))} if ids else {}
        decisions={x.recovery_case_id:x for x in s.scalars(select(PolicyDecision).where(PolicyDecision.recovery_case_id.in_(ids)).order_by(PolicyDecision.created_at.desc()))} if ids else {}
        return {"items":[{"case_id":str(x.id),"status":x.status,"amount_minor_units":x.amount,"currency":x.currency,"eligibility_result":x.eligibility_result,"source_scope":"SYNTHETIC" if x.source=="SYNTHETIC_DEMO" else "TEST_MODE","recovery_probability":str(assessments[x.id].recovery_probability) if x.id in assessments else None,"diagnosis":assessments[x.id].diagnosis if x.id in assessments else None,"candidate_action":assessments[x.id].candidate_action if x.id in assessments else None,"policy_decision":decisions[x.id].decision if x.id in decisions else None,"created_at":x.created_at} for x in cases],"pagination":{"limit":limit,"offset":offset}}
@router.get("/recoveries/{case_id}",response_model=RecoveryCaseDetail)
def recovery(case_id:str,request:Request,p:ReportingPrincipal=Depends(principal)):
    with db(request) as s:
        x=s.scalar(select(RecoveryCase).where(RecoveryCase.id==case_id,RecoveryCase.merchant_id==p.merchant_id))
        if not x: raise HTTPException(404,"recovery case not found")
        return {"recovery_case":{"case_id":str(x.id),"status":x.status,"amount_minor_units":x.amount,"currency":x.currency,"source":x.source,"created_at":x.created_at},"assessments":[{"diagnosis":a.diagnosis,"diagnosis_confidence":a.diagnosis_confidence,"candidate_action":a.candidate_action,"recovery_probability":str(a.recovery_probability),"expected_recovery_value":a.expected_recovery_value,"reason_codes":a.reason_codes,"score_version":a.score_version,"created_at":a.created_at} for a in s.scalars(select(RecoveryAssessment).where(RecoveryAssessment.recovery_case_id==x.id))],"ai_advisories":[{"status":a.status,"provider":a.provider,"model":a.model,"prompt_version":a.prompt_version,"diagnosis_label":a.diagnosis_label,"recommended_action":a.recommended_action,"confidence":str(a.confidence) if a.confidence is not None else None,"rationale_codes":a.rationale_codes,"explanation":a.explanation,"uncertainty":a.uncertainty,"failure_code":a.failure_code,"created_at":a.created_at} for a in s.scalars(select(AIAdvisory).where(AIAdvisory.recovery_case_id==x.id))],"policy_decisions":[{"decision":d.decision,"candidate_action":d.candidate_action,"policy_version":d.policy_version,"reason_codes":d.reason_codes,"authorized_amount":d.authorized_amount,"evaluated_facts":d.evaluated_facts,"created_at":d.created_at} for d in s.scalars(select(PolicyDecision).where(PolicyDecision.recovery_case_id==x.id))],"executions":[{"execution_id":str(e.id),"action":e.action,"status":e.status,"payment_link_id":e.razorpay_payment_link_id,"amount_minor_units":e.amount_minor_units,"currency":e.currency,"reference_id":e.reference_id,"failure_code":e.failure_code,"failure_reason":e.failure_reason,"expires_at":e.expires_at,"created_at":e.created_at,"updated_at":e.updated_at} for e in s.scalars(select(ExecutionRequest).where(ExecutionRequest.recovery_case_id==x.id))],"attributions":[{"status":a.attribution_status,"amount_minor_units":a.attributed_amount_minor_units,"payment_link_id":a.razorpay_payment_link_id,"razorpay_payment_id":a.razorpay_payment_id,"currency":a.currency,"evidence":a.evidence,"created_at":a.created_at} for a in s.scalars(select(RecoveryAttribution).where(RecoveryAttribution.recovery_case_id==x.id))]}
@router.get("/evaluations",response_model=EvaluationRunListResponse)
def evaluations(request:Request,p:ReportingPrincipal=Depends(principal)):
    with db(request) as s:return {"items":[{"run_id":str(x.id),"run_name":x.run_name,"dataset_id":x.dataset_id,"dataset_version":x.dataset_version,"run_type":x.run_type,"status":x.status} for x in s.scalars(select(EvaluationRun).where(EvaluationRun.merchant_id==p.merchant_id).order_by(EvaluationRun.created_at.desc()))]}
@router.get("/evaluations/{run_id}/metrics",response_model=EvaluationMetricsResponse)
def metrics(run_id:str,request:Request,p:ReportingPrincipal=Depends(principal)):
    with db(request) as s:
        x=s.scalar(select(MetricSnapshot).join(EvaluationRun).where(MetricSnapshot.evaluation_run_id==run_id,EvaluationRun.merchant_id==p.merchant_id))
        if not x:raise HTTPException(404,"metric snapshot not found")
        return x.metrics_json
@router.get("/evaluations/{run_id}",response_model=EvaluationDetail)
def evaluation(run_id:str,request:Request,p:ReportingPrincipal=Depends(principal)):
    with db(request) as s:
        x=s.scalar(select(EvaluationRun).where(EvaluationRun.id==run_id,EvaluationRun.merchant_id==p.merchant_id))
        if not x: raise HTTPException(404,"evaluation run not found")
        snapshot=s.scalar(select(MetricSnapshot).where(MetricSnapshot.evaluation_run_id==x.id))
        return {"run_id":str(x.id),"dataset_id":x.dataset_id,"dataset_version":x.dataset_version,"source_scope":x.run_type,"configuration":x.configuration_snapshot,"metrics":snapshot.metrics_json if snapshot else None}
@router.get("/recovery-funnel",response_model=OverviewResponse)
def funnel(request:Request,p:ReportingPrincipal=Depends(principal)): return overview(request,p)
@router.get("/policy",response_model=PolicyReportingResponse)
def policy(request:Request,p:ReportingPrincipal=Depends(principal)):
    from app.db.models import PolicyDecision
    with db(request) as s:
        xs=list(s.scalars(select(PolicyDecision).where(PolicyDecision.merchant_id==p.merchant_id))); n=len(xs); return {"approval_count":sum(x.decision=="APPROVE" for x in xs),"denial_count":sum(x.decision=="DENY" for x in xs),"escalation_count":sum(x.decision=="ESCALATE" for x in xs),"denominator":n,"reason_code_counts":dict(Counter(code for x in xs for code in (x.reason_codes or [])))}
@router.get("/executions",response_model=ExecutionReportingResponse)
def executions(request:Request,p:ReportingPrincipal=Depends(principal)):
    with db(request) as s:
        xs=list(s.scalars(select(ExecutionRequest).where(ExecutionRequest.merchant_id==p.merchant_id))); return {"execution_request_count":len(xs),"payment_link_created_count":sum(x.razorpay_payment_link_id is not None for x in xs),"awaiting_payment_count":sum(x.status=="AWAITING_PAYMENT" for x in xs),"reconciling_count":sum(x.status=="RECONCILING" for x in xs),"failed_count":sum(x.status=="FAILED" for x in xs),"blocked_count":sum(x.status=="BLOCKED" for x in xs)}
@router.get("/ai",response_model=AIReportingResponse)
def ai(request:Request,p:ReportingPrincipal=Depends(principal)):
    with db(request) as s:
        xs=list(s.scalars(select(AIAdvisory).join(RecoveryCase).where(RecoveryCase.merchant_id==p.merchant_id))); return {"total_advisories":len(xs),"completed":sum(x.status=="COMPLETED" for x in xs),"uncertain":sum(x.status=="UNCERTAIN" for x in xs),"unavailable":sum(x.status=="UNAVAILABLE" for x in xs)}
@router.get("/audit",response_model=AuditListResponse)
def audit(request:Request,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),p:ReportingPrincipal=Depends(principal)):
    limit,offset=page(limit,offset)
    with db(request) as s:return {"items":[{"action":x.action,"entity_type":x.entity_type,"entity_id":str(x.entity_id) if x.entity_id else None,"created_at":x.created_at,"actor":x.actor,"metadata":x.metadata_json} for x in s.scalars(select(AuditLog).where(AuditLog.merchant_id==p.merchant_id).order_by(AuditLog.created_at.desc(),AuditLog.id).limit(limit).offset(offset))],"pagination":{"limit":limit,"offset":offset}}
