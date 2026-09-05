# RECOVER demo

## Live Test Mode path

1. Start PostgreSQL with `docker compose -f infra/docker-compose.yml up -d`.
2. Copy `infra/env.example` to `backend/.env`; use only `rzp_test_` credentials.
3. From `backend`, run `python -m alembic upgrade head` and `uvicorn app.main:create_app --factory`.
4. Send a signed `payment.failed` webhook, process its job, and inspect the recovery case/assessment/policy evidence through `/api/v1/reporting/recoveries`.
5. Create and execute the already policy-approved bounded execution request. The gateway rejects non-test credentials.
6. Complete the link in Razorpay Test Mode and deliver the signed `payment_link.paid` webhook.
7. Inspect the immutable attribution and reporting evidence. Only `VERIFIED` attribution represents sandbox recovered value.

Do not claim this path succeeded unless actual Test Mode credentials, PostgreSQL, and webhooks were used.

## Reproducible fallback

Run `python -m app.synthetic --count 1000 --seed 20260905 --version synthetic-v1`. This yields 800 TRAIN / 200 EVAL cases and is `SYNTHETIC_SIMULATED_NOT_REVENUE`, not Test Mode or production revenue.
