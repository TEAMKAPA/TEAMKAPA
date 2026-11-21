"""Punkt wejścia API (FastAPI)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from backend.shared.config import get_api_settings

API_VERSION = "0.1.0"


class HealthResponse(BaseModel):
    status: str
    version: str


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Walidacja konfiguracji przy starcie — błędna konfiguracja zatrzymuje proces od razu,
    # zamiast wyjść na jaw przy pierwszym żądaniu.
    get_api_settings()
    yield


app = FastAPI(
    title="COI Cyfryzacja formularzy",
    version=API_VERSION,
    lifespan=lifespan,
)


@app.get("/health", tags=["system"])
async def health() -> HealthResponse:
    """Liveness probe — nie wymaga uwierzytelnienia."""
    return HealthResponse(status="ok", version=API_VERSION)
