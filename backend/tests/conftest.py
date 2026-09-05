from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.models import Base
from app.main import create_app


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    test_directory = Path(tempfile.mkdtemp(prefix="recover-webhook-test-"))
    database_url = f"sqlite:///{(test_directory / 'recover-test.db').as_posix()}"
    settings = Settings(
        database_url=database_url,
        razorpay_webhook_secret="test-webhook-secret",
        merchant_id="merchant-test",
    )
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)
    with TestClient(app) as test_client:
        yield test_client
    app.state.engine.dispose()


@pytest.fixture()
def session_factory(client):
    return client.app.state.session_factory
