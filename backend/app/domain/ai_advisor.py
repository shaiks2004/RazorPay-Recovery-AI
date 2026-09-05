from __future__ import annotations

import math
from dataclasses import dataclass

DIAGNOSES = {"TRANSIENT_FAILURE", "AUTHENTICATION_REQUIRED", "CUSTOMER_ABANDONMENT", "PAYMENT_INSTRUMENT_FAILURE", "INSUFFICIENT_FACTS", "UNKNOWN_FAILURE"}
ACTIONS = {"PREPARE_PAYMENT_LINK", "DO_NOTHING", "ESCALATE_FOR_REVIEW", "WAIT_FOR_MORE_FACTS"}
RATIONALE_CODES = {"TRANSIENT_PROVIDER_FAILURE", "AUTHENTICATION_REQUIRED", "INSTRUMENT_FAILURE", "CUSTOMER_ABANDONMENT_SIGNAL", "POSITIVE_OUTSTANDING_BALANCE", "HIGH_RECOVERY_PROBABILITY", "LOW_RECOVERY_PROBABILITY", "RECOVERY_VALUE_JUSTIFIED", "STALE_CASE", "INSUFFICIENT_FACTS", "REPEATED_FAILURE", "CUSTOMER_INTENT_UNKNOWN"}

@dataclass(frozen=True)
class RecoveryCasePacket:
    case_id: str; amount_minor_units: int; currency: str; payment_state: str; order_state: str; failure_code: str | None; failure_reason: str | None; failure_source: str; payment_method: str | None; failure_count: int; case_age_seconds: int; time_since_failure_seconds: int; deterministic_diagnosis: str; deterministic_diagnosis_confidence: str; deterministic_recovery_probability: str; deterministic_expected_recovery_value_minor_units: int; deterministic_candidate_action: str; policy_context: dict; previous_interventions_count: int; previous_policy_decisions_summary: dict; schema_version: str = "ai-case-packet-v1"

@dataclass(frozen=True)
class AIAdvice:
    diagnosis_label: str; recommended_action: str; confidence: float; rationale_codes: list[str]; explanation: str; uncertainty: list[str]
    @classmethod
    def from_dict(cls, data: object) -> "AIAdvice":
        if not isinstance(data, dict) or set(data) != {"diagnosis_label", "recommended_action", "confidence", "rationale_codes", "explanation", "uncertainty"}: raise ValueError("AI_RESPONSE_SCHEMA_INVALID")
        diagnosis, action, confidence = data["diagnosis_label"], data["recommended_action"], data["confidence"]
        if diagnosis not in DIAGNOSES: raise ValueError("AI_DIAGNOSIS_INVALID")
        if action not in ACTIONS: raise ValueError("AI_ACTION_INVALID")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1: raise ValueError("AI_CONFIDENCE_INVALID")
        codes, explanation, uncertainty = data["rationale_codes"], data["explanation"], data["uncertainty"]
        if not isinstance(codes, list) or not all(isinstance(x, str) and x in RATIONALE_CODES for x in codes): raise ValueError("AI_RATIONALE_CODES_INVALID")
        if not isinstance(explanation, str) or not explanation or len(explanation) > 1000: raise ValueError("AI_EXPLANATION_INVALID")
        if not isinstance(uncertainty, list) or not all(isinstance(x, str) and x in RATIONALE_CODES for x in uncertainty): raise ValueError("AI_UNCERTAINTY_INVALID")
        return cls(diagnosis, action, float(confidence), codes, explanation, uncertainty)
