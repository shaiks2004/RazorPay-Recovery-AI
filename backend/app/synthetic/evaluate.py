from __future__ import annotations
from app.synthetic.generator import SyntheticCase

def evaluate(rows:list[SyntheticCase])->dict:
    evals=[x for x in rows if x.split=="EVAL"]
    # Independent, disclosed baseline: pursue every synthetically eligible fact (non-insufficient data).
    baseline=[x for x in evals if x.category!="INSUFFICIENT_FACTS"]
    def probability(x): return {"TRANSIENT_FAILURE":.82,"AUTHENTICATION_REQUIRED":.68,"CUSTOMER_ABANDONMENT":.35,"PAYMENT_INSTRUMENT_FAILURE":.22,"INSUFFICIENT_FACTS":.05,"UNKNOWN_FAILURE":.30}[x.category] - .08*(x.failure_count-1) - (.15 if x.case_age_seconds>604800 else 0)
    recommended=[x for x in evals if probability(x)>=.40]
    approved=[x for x in recommended if x.amount_minor_units<=500000 and x.failure_count<=2]
    tp=sum(x.ground_truth_recoverable for x in recommended); fp=len(recommended)-tp; fn=sum(x.ground_truth_recoverable for x in evals if x not in recommended); tn=len(evals)-tp-fp-fn
    ratio=lambda n,d: None if not d else n/d
    buckets=[]
    for lo in (0,.2,.4,.6,.8):
        group=[x for x in evals if lo<=probability(x)<lo+.2 or (lo==.8 and probability(x)>=lo)]
        buckets.append({"range":f"{lo:.1f}-{min(1,lo+.2):.1f}","case_count":len(group),"mean_predicted_probability":ratio(sum(probability(x) for x in group),len(group)),"actual_recovery_rate":ratio(sum(x.ground_truth_recoverable for x in group),len(group))})
    simulated=sum(x.ground_truth_recovery_amount_minor_units for x in approved); eligible=sum(x.amount_minor_units for x in baseline)
    return {"schema_version":"synthetic-evaluation-v1","source_scope":"SYNTHETIC_SIMULATED_NOT_REVENUE","case_count":len(rows),"eval_case_count":len(evals),"baseline_metrics":{"eligible_case_count":len(baseline),"potential_revenue_minor_units":eligible},"deterministic_metrics":{"recommended_count":len(recommended),"mean_predicted_probability":ratio(sum(probability(x) for x in evals),len(evals))},"policy_metrics":{"approved_count":len(approved),"denied_or_blocked_count":len(recommended)-len(approved)},"classification":{"true_positive":tp,"true_negative":tn,"false_positive":fp,"false_negative":fn,"precision":ratio(tp,tp+fp),"recall":ratio(tp,tp+fn),"f1":ratio(2*tp,2*tp+fp+fn)},"economic_metrics":{"expected_recovery_value_minor_units":sum(int(x.amount_minor_units*probability(x)) for x in evals),"simulated_recovered_revenue_minor_units":simulated,"false_positive_value_minor_units":sum(x.amount_minor_units for x in recommended if not x.ground_truth_recoverable),"false_negative_value_minor_units":sum(x.amount_minor_units for x in evals if x.ground_truth_recoverable and x not in recommended)},"calibration_metrics":{"buckets":buckets},"safety_metrics":{"amount_limit_blocks":sum(x.amount_minor_units>500000 for x in recommended),"attempt_limit_blocks":sum(x.failure_count>2 for x in recommended),"insufficient_facts_cases":sum(x.category=="INSUFFICIENT_FACTS" for x in evals)},"error_analysis":{"false_positive_case_ids":[x.case_id for x in recommended if not x.ground_truth_recoverable][:20],"false_negative_case_ids":[x.case_id for x in evals if x.ground_truth_recoverable and x not in recommended][:20]}}
