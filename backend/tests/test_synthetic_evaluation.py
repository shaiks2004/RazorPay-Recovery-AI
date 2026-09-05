from app.synthetic.generator import CATEGORIES, dataset_payload, generate
from app.synthetic.evaluate import evaluate

def test_generator_is_seeded_diverse_and_customer_split_safe():
    one=generate(count=1000,seed=20260905); two=generate(count=1000,seed=20260905); other=generate(count=1000,seed=7)
    assert one==two and one!=other and {x.category for x in one}==set(CATEGORIES)
    assert len({x.case_id for x in one})==1000 and all(x.currency=="INR" and x.amount_minor_units>0 for x in one)
    splits={}; [splits.setdefault(x.customer_id,x.split) for x in one]
    assert len(set(splits.values()))==2

def test_evaluation_is_reproducible_and_synthetic_only():
    rows=generate(count=1000,seed=20260905); result=evaluate(rows)
    assert result==evaluate(rows) and result["source_scope"]=="SYNTHETIC_SIMULATED_NOT_REVENUE"
    assert result["eval_case_count"]==200 and result["economic_metrics"]["simulated_recovered_revenue_minor_units"] >= 0
    assert result["classification"]["precision"] is not None
