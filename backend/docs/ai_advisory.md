# AI advisory boundary

`ai-advisor-v1` receives only the minimized `RecoveryCasePacket`: case/payment/order states, deterministic assessment, and policy context. It never receives raw webhooks, customer PII, credentials, secrets, or database records.

```text
Deterministic facts + assessment → AI advisor (recommendation only) → deterministic policy → bounded executor
```

The advisor cannot call Razorpay or tools. `AI_ADVISORY_UNAVAILABLE`, invalid responses, timeouts, and low confidence leave the deterministic assessment and policy path intact. `AIAdvisory` is immutable and hashes canonical input/output. Configuration: `RECOVER_AI_ADVISOR_ENABLED`, `RECOVER_AI_ADVISOR_PROVIDER`, `RECOVER_AI_ADVISOR_MODEL`, `RECOVER_AI_ADVISOR_TIMEOUT_SECONDS`, `RECOVER_AI_ADVISOR_MAX_OUTPUT_TOKENS`, `RECOVER_AI_ADVISOR_CONFIDENCE_THRESHOLD`, `RECOVER_AI_ADVISOR_BASE_URL`, `RECOVER_AI_ADVISOR_API_KEY`.

The normal worker constructs an advisor only when `RECOVER_AI_ADVISOR_ENABLED=true`. The supported runtime provider is `OPENAI_COMPATIBLE`; it requires a model, base URL, and API key. Invalid enabled configuration fails worker startup clearly. `FakeAIAdvisor` is test-only and cannot be selected through environment configuration.
