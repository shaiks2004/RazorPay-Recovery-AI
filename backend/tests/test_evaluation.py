from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import EvaluationRun, MetricSnapshot
from app.services.evaluation import add_case, create_run, evaluate_run
from tests.test_policy_gate import build_case


def test_evaluation_snapshot_uses_expected_value_not_recovered_revenue(session_factory):
    case_id, _ = build_case(session_factory, expected_value=42500)
    with session_factory() as session:
        with session.begin():
            run = create_run(session=session, run_name="synthetic-1", dataset_id="cases", dataset_version="v1", run_type="SYNTHETIC", configuration={"seed": 1}, now=datetime.now(timezone.utc))
            add_case(session=session, run_id=run.id, recovery_case_id=case_id, source_scope="SYNTHETIC")
            snapshot = evaluate_run(session=session, run_id=run.id, now=datetime.now(timezone.utc))
    metrics = snapshot.metrics_json["metrics"]
    assert metrics["expected_recovery_value_minor_units"]["value"] == 42500
    assert metrics["verified_recovered_revenue_minor_units"]["value"] == 0
    assert metrics["recovery_rate_bps"]["value"] == 0


def test_evaluation_is_immutable_and_zero_denominators_are_zero(session_factory):
    with session_factory() as session:
        with session.begin():
            run = create_run(session=session, run_name="empty", dataset_id="empty", dataset_version="v1", run_type="TEST_MODE", configuration={}, now=datetime.now(timezone.utc))
            first = evaluate_run(session=session, run_id=run.id, now=datetime.now(timezone.utc))
            replay = evaluate_run(session=session, run_id=run.id, now=datetime.now(timezone.utc))
            assert replay.id == first.id
    with session_factory() as session:
        snapshot = session.scalar(select(MetricSnapshot))
        assert snapshot.metrics_json["metrics"]["attribution_rate_bps"]["value"] == 0


def test_run_rejects_cross_source_membership(session_factory):
    case_id, _ = build_case(session_factory)
    with session_factory() as session:
        with session.begin():
            run = create_run(session=session, run_name="test-only", dataset_id="d", dataset_version="v1", run_type="TEST_MODE", configuration={}, now=datetime.now(timezone.utc))
            try:
                add_case(session=session, run_id=run.id, recovery_case_id=case_id, source_scope="SYNTHETIC")
            except ValueError as error:
                assert "source scope" in str(error)
            else:
                raise AssertionError("cross-source membership accepted")
