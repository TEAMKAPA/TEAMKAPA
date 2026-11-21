"""Punkt wejścia API (FastAPI)."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.api.errors import error_response, error_responses, install_error_handlers
from backend.api.job_queue import ArqJobQueue, JobQueue
from backend.api.routes import router
from backend.api.schemas import HealthResponse
from backend.shared.config import ApiSettings, get_api_settings
from backend.shared.db.repository import JobStore, PgJobStore

logger = logging.getLogger(__name__)

API_VERSION = "0.4.0"

QueueFactory = Callable[[ApiSettings], Awaitable[JobQueue]]
StoreFactory = Callable[[ApiSettings], Awaitable[JobStore]]


class BodySizeLimitMiddleware:
    """Odrzuca za duże żądania (413), zanim multipart trafi do pliku tymczasowego.

    Limit: ``MAX_FILE_SIZE_MB × MAX_BATCH_FILES`` + zapas. Sprawdzany jest nagłówek
    Content-Length oraz faktycznie odebrane bajty (dla chunked transfer).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = get_api_settings().max_request_bytes
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            response = error_response(
                status.HTTP_413_CONTENT_TOO_LARGE, "REQUEST_TOO_LARGE", _too_large(limit)
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # fastapi.HTTPException przechodzi przez parser formularza bez zamiany na 400.
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=_too_large(limit))
            return message

        await self.app(scope, limited_receive, send)


def _too_large(limit: int) -> str:
    return f"Żądanie jest za duże (maksymalnie {limit // (1024 * 1024)} MB)"


def create_app(
    queue_factory: QueueFactory | None = None, store_factory: StoreFactory | None = None
) -> FastAPI:
    connect_queue: QueueFactory = queue_factory or ArqJobQueue.connect
    connect_store: StoreFactory = store_factory or PgJobStore.connect

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Walidacja konfiguracji przy starcie — błąd zatrzymuje proces od razu.
        settings = get_api_settings()
        if not settings.jwt_enabled:
            logger.warning("JWT_ENABLED=false — API nie wymaga uwierzytelnienia (tylko dev!)")
        app.state.job_store = await connect_store(settings)
        try:
            app.state.job_queue = await connect_queue(settings)
            try:
                yield
            finally:
                await app.state.job_queue.close()
        finally:
            await app.state.job_store.close()

    app = FastAPI(
        title="COI Cyfryzacja formularzy",
        version=API_VERSION,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware)
    app.include_router(router)

    @app.get("/health", tags=["system"])
    async def health() -> HealthResponse:
        """Liveness probe — nie wymaga uwierzytelnienia ani kolejki."""
        return HealthResponse(status="ok", version=API_VERSION)

    @app.get(
        "/ready",
        tags=["system"],
        response_model=HealthResponse,
        responses=error_responses(503),
    )
    async def ready() -> HealthResponse | JSONResponse:
        """Readiness probe — sprawdza połączenie z kolejką i bazą danych."""
        queue: JobQueue = app.state.job_queue
        store: JobStore = app.state.job_store
        if not await queue.ping():
            return error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE, "QUEUE_UNAVAILABLE", "Kolejka niedostępna"
            )
        if not await store.ping():
            return error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "DATABASE_UNAVAILABLE",
                "Baza danych niedostępna",
            )
        return HealthResponse(status="ready", version=API_VERSION)

    return app


app = create_app()
