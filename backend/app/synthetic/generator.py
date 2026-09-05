from __future__ import annotations
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

CATEGORIES=("TRANSIENT_FAILURE","AUTHENTICATION_REQUIRED","CUSTOMER_ABANDONMENT","PAYMENT_INSTRUMENT_FAILURE","INSUFFICIENT_FACTS","UNKNOWN_FAILURE")
GENERATOR_VERSION="synthetic-generator-v1"
@dataclass(frozen=True)
class SyntheticCase:
    case_id:str; customer_id:str; split:str; category:str; failure_code:str|None; failure_reason:str|None; amount_minor_units:int; currency:str; payment_method:str|None; failure_count:int; case_age_seconds:int; previous_success_count:int; ground_truth_recoverable:bool; ground_truth_recovery_amount_minor_units:int; ground_truth_reason:str

def generate(*, count:int=1000, seed:int=20260905) -> list[SyntheticCase]:
    if count < 500: raise ValueError("minimum synthetic dataset size is 500")
    rng=random.Random(seed); rows=[]; reasons={"TRANSIENT_FAILURE":("GATEWAY_ERROR","temporary provider failure",.78),"AUTHENTICATION_REQUIRED":("BAD_REQUEST_ERROR","authentication required",.62),"CUSTOMER_ABANDONMENT":("PAYMENT_CANCELLED","checkout abandoned",.32),"PAYMENT_INSTRUMENT_FAILURE":("BAD_REQUEST_ERROR","issuer declined",.20),"INSUFFICIENT_FACTS":(None,None,.12),"UNKNOWN_FAILURE":("SERVER_ERROR","unclassified provider error",.28)}
    amounts=(9900,19900,49900,99900,250000,500000,1000000)
    for i in range(count):
        category=CATEGORIES[i%len(CATEGORIES)]; code,reason,base=reasons[category]; customer=i//2; amount=rng.choice(amounts); attempts=rng.randrange(1,5); age=rng.choice((1800,7200,43200,172800,432000,864000)); success=rng.randrange(0,5)
        # Outcome sampling is seeded from latent simulation parameters, not RECOVER scores/features.
        chance=base - .10*(attempts-1) - (.18 if age>604800 else 0) + .03*success
        recovered=rng.random() < max(.02,min(.95,chance)); rows.append(SyntheticCase(f"syn_{i:06d}",f"customer_{customer:06d}","EVAL" if customer%5==0 else "TRAIN",category,code,reason,amount,"INR",None if category=="INSUFFICIENT_FACTS" else rng.choice(("card","upi","netbanking")),attempts,age,success,recovered,amount if recovered else 0,"seeded latent outcome model"))
    return rows

def dataset_payload(rows:list[SyntheticCase], *, seed:int, dataset_id:str="recover-synthetic", dataset_version:str="synthetic-v1")->dict:
    return {"dataset_id":dataset_id,"dataset_version":dataset_version,"generator_version":GENERATOR_VERSION,"seed":seed,"case_count":len(rows),"cases":[asdict(x) for x in rows]}
