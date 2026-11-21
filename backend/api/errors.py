"""Jednolity format błędów API: ``{"error": {"code", "message", "details"}}``."""

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.shared.db.pool import DB_UNAVAILABLE_ERRORS

logger = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(body, exclude_none=True),
        headers=headers,
    )


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Opis odpowiedzi błędów do dokumentacji OpenAPI."""
    return {code: {"model": ErrorResponse} for code in codes}


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # noqa: S101 — rejestracja handlera gwarantuje typ
    return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    details = [{"loc": list(e.get("loc", ())), "message": e.get("msg", "")} for e in exc.errors()]
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "REQUEST_INVALID",
        "Nieprawidłowe żądanie",
        details,
    )


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "REQUEST_TOO_LARGE"}.get(
        exc.status_code, "HTTP_ERROR"
    )
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def _database_unavailable(_: Request, exc: Exception) -> JSONResponse:
    logger.warning("Baza danych niedostępna: %s", type(exc).__name__)
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "DATABASE_UNAVAILABLE",
        "Baza danych jest chwilowo niedostępna — spróbuj ponownie",
        headers={"Retry-After": "5"},
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    for error_type in DB_UNAVAILABLE_ERRORS:
        app.add_exception_handler(error_type, _database_unavailable)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
