from __future__ import annotations

import pytest

from app.config import Settings
from app.integrations.ai_provider import OpenAICompatibleAdvisor
from app.workers.runner import build_advisor, build_worker


def settings(**overrides) -> Settings:
    return Settings(
        database_url="sqlite://",
        razorpay_webhook_secret="test-secret",
        **overrides,
    )


def test_disabled_ai_does_not_construct_advisor():
    assert build_advisor(settings()) is None
    worker, _ = build_worker(settings())
    assert worker._advisor is None


def test_enabled_openai_compatible_advisor_is_passed_to_worker():
    configured = settings(
        ai_advisor_enabled=True,
        ai_advisor_provider="OPENAI_COMPATIBLE",
        ai_advisor_model="test-model",
        ai_advisor_base_url="https://ai.invalid/v1",
        ai_advisor_api_key="test-only-key",
    )
    advisor = build_advisor(configured)
    assert isinstance(advisor, OpenAICompatibleAdvisor)
    worker, _ = build_worker(configured)
    assert isinstance(worker._advisor, OpenAICompatibleAdvisor)
    assert worker._advisor_confidence_threshold == configured.ai_advisor_confidence_threshold


@pytest.mark.parametrize("overrides", [
    {"ai_advisor_enabled": True, "ai_advisor_provider": "DISABLED"},
    {"ai_advisor_enabled": True, "ai_advisor_provider": "OPENAI_COMPATIBLE", "ai_advisor_model": "m"},
    {"ai_advisor_enabled": True, "ai_advisor_provider": "OPENAI_COMPATIBLE", "ai_advisor_model": "m", "ai_advisor_base_url": "https://ai.invalid", "ai_advisor_api_key": "x", "ai_advisor_confidence_threshold": 1.1},
])
def test_enabled_invalid_ai_configuration_fails_clearly(overrides):
    with pytest.raises(ValueError, match="AI advisor"):
        build_advisor(settings(**overrides))
