"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analysis.types import ALGORITHM_VERSION
from app.api import auth, evaluation, incidents, ingestions, projects, telemetry
from app.config import DEV_SECRET, get_settings
from app.db import SessionLocal, get_db
from app.errors import install_error_handlers
from app.llm.providers import get_provider
from app.llm.writer import DETERMINISTIC_MODEL
from app.services import jobs

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                   "logger": record.name, "msg": record.getMessage()}
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("tracemind")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.seed_on_startup and settings.demo_mode:
        from app.seed import seed_demo

        try:
            with SessionLocal() as db:
                seed_demo(db)
        except Exception:  # never block startup on demo data
            logging.getLogger("tracemind").exception("demo seeding failed")
    yield
    jobs.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.env == "production" and settings.secret_key == DEV_SECRET:
        raise RuntimeError("TRACEMIND_SECRET_KEY must be set in production")

    app = FastAPI(title="TraceMind AI", version=ALGORITHM_VERSION, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    access = logging.getLogger("tracemind.access")
    long_running = ("/events", "/ingestions")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        request.state.request_id = request_id
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_upload_bytes + 1_000_000:
            return JSONResponse({"error": {"code": "payload_too_large", "message": "Request body too large.",
                                           "details": None, "request_id": request_id}}, status_code=413)
        started = time.perf_counter()
        try:
            if request.url.path.endswith(long_running):
                response = await call_next(request)
            else:
                response = await asyncio.wait_for(call_next(request), timeout=settings.request_timeout_seconds)
        except TimeoutError:
            return JSONResponse({"error": {"code": "timeout", "message": "The request took too long.", "details": None,
                                           "request_id": request_id}}, status_code=504)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        access.info("request", extra={"method": request.method, "path": request.url.path, "status": response.status_code,
                                      "duration_ms": round((time.perf_counter() - started) * 1000, 1), "request_id": request_id,
                                      "user_id": getattr(request.state, "user_id", None)})
        return response

    api = APIRouter(prefix="/api/v1")

    @api.get("/health", tags=["meta"])
    def health(db: Session = Depends(get_db)):
        try:
            db.execute(text("SELECT 1"))
            database = "ok"
        except Exception:
            database = "unavailable"
        status = "ok" if database == "ok" else "degraded"
        return JSONResponse({"status": status, "database": database, "version": ALGORITHM_VERSION},
                            status_code=200 if status == "ok" else 503)

    @api.get("/meta", tags=["meta"])
    def meta():
        provider = get_provider()
        return {"algorithm_version": ALGORITHM_VERSION, "demo_mode": settings.demo_mode,
                "llm_provider": provider.name if provider else "deterministic",
                "llm_model": provider.model_version if provider else DETERMINISTIC_MODEL,
                "max_upload_bytes": settings.max_upload_bytes}

    for module in (auth, projects, ingestions, telemetry, incidents, evaluation):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
