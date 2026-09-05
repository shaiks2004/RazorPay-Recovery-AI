# Synthetic evaluation

Run `python -m app.synthetic --count 1000 --seed 20260905 --version synthetic-v1` from `backend`. Output is machine-readable JSON and is explicitly `SYNTHETIC_SIMULATED_NOT_REVENUE`.

The generator uses seeded latent outcome probabilities independent of the disclosed deterministic evaluation score. Customer IDs are synthetic and split by customer: 80% TRAIN / 20% EVAL. No payment links, Razorpay calls, PII, or production revenue are created.
