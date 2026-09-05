from sqlalchemy import func, select

from app.db.models import AuditLog, MerchantPolicy, PaymentLink, RecoveryAttribution, RecoveryCase


def add_demo_policy(session_factory):
    with session_factory() as session:
        with session.begin():
            session.add(MerchantPolicy(
                merchant_id="merchant-test",
                policy_version="demo-test-v1",
                enabled=True,
                allowed_actions=["PREPARE_PAYMENT_LINK"],
                max_amount_minor_units=100000,
                min_recovery_probability="0.3000",
                min_expected_recovery_value_minor_units=100,
                max_attempts=3,
                cooldown_seconds=0,
                daily_recovery_budget_minor_units=100000,
            ))


def test_demo_data_generate_and_clear_is_synthetic_and_bounded(client, session_factory):
    add_demo_policy(session_factory)

    generated = client.post("/api/v1/demo/data")
    assert generated.status_code == 200
    body = generated.json()
    assert body["synthetic_demo"] is True
    assert body["generated"] == 300
    assert body["assessments"] == 300
    assert body["advisories"] == 300
    assert body["approved"] > 0
    assert body["denied"] > 0
    assert body["verified_revenue_minor_units"] == 0

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryCase).where(RecoveryCase.source == "SYNTHETIC_DEMO")) == 300
        assert session.scalar(select(func.count()).select_from(PaymentLink)) == 0
        assert session.scalar(select(func.count()).select_from(RecoveryAttribution)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.correlation_id.like("demo-20260906-%"))) > 300

    cleared = client.delete("/api/v1/demo/data")
    assert cleared.status_code == 200
    assert cleared.json()["deleted"] == 300
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecoveryCase).where(RecoveryCase.source == "SYNTHETIC_DEMO")) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.correlation_id.like("demo-20260906-%"))) == 0
        assert session.scalar(select(func.count()).select_from(PaymentLink)) == 0


def test_demo_controls_are_unavailable_outside_local_test_mode(client):
    client.app.state.settings.razorpay_mode = "live"
    assert client.get("/api/v1/demo/status").status_code == 404
    assert client.post("/api/v1/demo/data").status_code == 404
