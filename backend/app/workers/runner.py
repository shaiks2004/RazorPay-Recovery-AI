from __future__ import annotations

import logging
import os
import socket
import time
import uuid

from app.config import Settings
from app.db.database import create_database_engine, create_session_factory
from app.integrations.ai_provider import AIAdvisor, OpenAICompatibleAdvisor
from app.services.job_worker import JobQueueService, WebhookJobWorker, WorkerSettings


def build_advisor(settings: Settings) -> AIAdvisor | None:
    """Build the advisory-only provider, or fail startup on unsafe enabled configuration."""
    if not settings.ai_advisor_enabled:
        return None
    if settings.ai_advisor_provider != "OPENAI_COMPATIBLE":
        raise ValueError("AI advisor is enabled but RECOVER_AI_ADVISOR_PROVIDER must be OPENAI_COMPATIBLE")
    if not settings.ai_advisor_model or not settings.ai_advisor_base_url or not settings.ai_advisor_api_key:
        raise ValueError("AI advisor is enabled but model, base URL, and API key are required")
    if settings.ai_advisor_timeout_seconds <= 0 or settings.ai_advisor_max_output_tokens <= 0:
        raise ValueError("AI advisor timeout and max output tokens must be positive")
    if not 0 <= settings.ai_advisor_confidence_threshold <= 1:
        raise ValueError("AI advisor confidence threshold must be between 0 and 1")
    return OpenAICompatibleAdvisor(
        base_url=settings.ai_advisor_base_url,
        api_key=settings.ai_advisor_api_key,
        model=settings.ai_advisor_model,
        timeout_seconds=settings.ai_advisor_timeout_seconds,
        max_output_tokens=settings.ai_advisor_max_output_tokens,
    )


def build_worker(settings: Settings | None = None) -> tuple[WebhookJobWorker, float]:
    runtime_settings = settings or Settings()
    logging.basicConfig(level=runtime_settings.log_level)
    session_factory = create_session_factory(create_database_engine(runtime_settings.database_url))
    queue = JobQueueService(
        session_factory,
        WorkerSettings(
            lease_seconds=runtime_settings.worker_lease_seconds,
            max_attempts=runtime_settings.worker_max_attempts,
            retry_base_seconds=runtime_settings.worker_retry_base_seconds,
            retry_max_seconds=runtime_settings.worker_retry_max_seconds,
        ),
    )
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
    return WebhookJobWorker(
        queue,
        worker_id,
        advisor=build_advisor(runtime_settings),
        advisor_confidence_threshold=runtime_settings.ai_advisor_confidence_threshold,
    ), runtime_settings.worker_poll_seconds


def run_forever() -> None:
    worker, poll_seconds = build_worker()
    while True:
        worker.process_once()
        time.sleep(poll_seconds)


if __name__ == "__main__":
    run_forever()
