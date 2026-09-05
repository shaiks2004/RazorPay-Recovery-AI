from __future__ import annotations
import argparse, json
from app.synthetic.generator import dataset_payload, generate
from app.synthetic.evaluate import evaluate
p=argparse.ArgumentParser(); p.add_argument("--count",type=int,default=1000); p.add_argument("--seed",type=int,default=20260905); p.add_argument("--version",default="synthetic-v1"); args=p.parse_args()
rows=generate(count=args.count,seed=args.seed); print(json.dumps({"dataset":dataset_payload(rows,seed=args.seed,dataset_version=args.version),"evaluation":evaluate(rows)},sort_keys=True))
