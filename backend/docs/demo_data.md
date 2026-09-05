# Synthetic demo data

RECOVER exposes a local Test Mode-only demo workflow at `/api/v1/demo`.

- `GET /api/v1/demo/status` reports whether the controls are available.
- `POST /api/v1/demo/data` replaces existing `SYNTHETIC_DEMO` rows with 300 deterministic cases using seed `20260906`.
- `DELETE /api/v1/demo/data` removes only rows created by that workflow.

The generator uses the existing payment, assessment, policy, advisory, execution, and audit schemas. Synthetic advisory records are marked `provider=SYNTHETIC_DEMO`; they are not external model output. Execution requests remain local `REQUESTED` records and never create Razorpay Payment Links. No `PaymentLink`, attribution, or verified revenue rows are created.

The frontend exposes the controls in Settings only when the backend reports local `merchant_id` (`local-test-merchant` or `merchant-test`) and `razorpay_mode=test`. The endpoint also requires the reporting admin role when reporting authentication is enabled.

All generated cases use `source=SYNTHETIC_DEMO`, webhook IDs beginning `demo-20260906-`, and synthetic identifiers. The existing merchant policy is read-only during generation.
