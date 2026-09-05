from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field
class SourceScope(str,Enum): TEST_MODE="TEST_MODE"; SYNTHETIC="SYNTHETIC"; MIXED="MIXED"
class RevenueStatus(str,Enum): SANDBOX_VERIFIED="SANDBOX_VERIFIED"; SIMULATED_NOT_REVENUE="SIMULATED_NOT_REVENUE"
class OverviewResponse(BaseModel):
    source_scope:SourceScope; revenue_status:RevenueStatus; total_recovery_cases:int=Field(ge=0); eligible_cases:int=Field(ge=0); assessed_cases:int=Field(ge=0); payment_links_created:int=Field(ge=0); verified_attributions:int=Field(ge=0); recovered_revenue_minor_units:int=Field(ge=0); active_cases:int=Field(ge=0); closed_cases:int=Field(ge=0)
class PaginationMetadata(BaseModel): limit:int=Field(ge=1,le=100); offset:int=Field(ge=0)
class RecoveryCaseSummary(BaseModel): case_id:str; status:str; amount_minor_units:int=Field(ge=0); currency:str; eligibility_result:str; source_scope:SourceScope
class RecoveryListResponse(BaseModel): items:list[RecoveryCaseSummary]; pagination:PaginationMetadata
class RecoveryCaseDetail(BaseModel): recovery_case:dict; assessments:list[dict]; ai_advisories:list[dict]; executions:list[dict]; attributions:list[dict]
class EvaluationRunSummary(BaseModel): run_id:str; run_name:str; dataset_id:str; dataset_version:str; run_type:str; status:str
class EvaluationRunListResponse(BaseModel): items:list[EvaluationRunSummary]
class EvaluationDetail(BaseModel): run_id:str; dataset_id:str; dataset_version:str; source_scope:str; configuration:dict; metrics:dict|None
class EvaluationMetricsResponse(BaseModel): model_config={"extra":"allow"}
class AIReportingResponse(BaseModel): total_advisories:int=Field(ge=0); completed:int=Field(ge=0); uncertain:int=Field(ge=0); unavailable:int=Field(ge=0)
class PolicyReportingResponse(BaseModel): approval_count:int=Field(ge=0); denial_count:int=Field(ge=0); escalation_count:int=Field(ge=0); denominator:int=Field(ge=0)
class ExecutionReportingResponse(BaseModel): execution_request_count:int=Field(ge=0); payment_link_created_count:int=Field(ge=0); awaiting_payment_count:int=Field(ge=0); reconciling_count:int=Field(ge=0); failed_count:int=Field(ge=0); blocked_count:int=Field(ge=0)
class AuditRecordResponse(BaseModel): action:str; entity_type:str; entity_id:str|None; created_at:object
class AuditListResponse(BaseModel): items:list[AuditRecordResponse]; pagination:PaginationMetadata
