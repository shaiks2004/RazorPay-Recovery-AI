from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.webhooks import router as webhooks_router
from app.api.reporting import router as reporting_router
from app.config import Settings
from app.db.database import create_database_engine, create_session_factory, database_healthy
from app.services.webhook_ingress import WebhookIngressService


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings()
    logging.basicConfig(level=runtime_settings.log_level)
    engine = create_database_engine(runtime_settings.database_url)
    session_factory = create_session_factory(engine)

    app = FastAPI(title="RECOVER", version="0.1.0")
    app.state.settings = runtime_settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.webhook_ingress_service = WebhookIngressService(session_factory, runtime_settings.merchant_id)
    app.include_router(webhooks_router)
    app.include_router(reporting_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/database")
    def database_health() -> JSONResponse:
        try:
            database_healthy(session_factory)
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(status_code=200, content={"status": "ok"})

    return app
