"""Uwierzytelnianie JWT — API wyłącznie weryfikuje tokeny (wystawia je ``token_cli``).

Weryfikacja: podpis kluczem ``JWT_SECRET_KEY``, wyłącznie algorytm ``JWT_ALGORITHM``
(brak podatności „alg confusion” / ``none``), wymagane claimy ``exp`` i ``sub``.
Podmiot (``sub``) jest właścicielem zadań — widzi tylko swoje zadania.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.api.errors import ApiError
from backend.shared.config import ApiSettings, get_api_settings

logger = logging.getLogger(__name__)

ANONYMOUS_SUBJECT = "anonymous"
MAX_SUBJECT_LENGTH = 128

_bearer = HTTPBearer(auto_error=False, description="Token JWT: Authorization: Bearer <token>")
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def _unauthorized(code: str, message: str) -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, code, message, headers=_CHALLENGE)


def _secret(settings: ApiSettings) -> str:
    if settings.jwt_secret_key is None:  # wykluczone przez walidację ApiSettings
        raise RuntimeError("JWT_SECRET_KEY nie jest ustawiony")
    return settings.jwt_secret_key.get_secret_value()


def issue_token(
    settings: ApiSettings,
    subject: str,
    *,
    minutes: int | None = None,
    now: datetime | None = None,
) -> str:
    if not subject or len(subject) > MAX_SUBJECT_LENGTH:
        raise ValueError(f"sub musi mieć od 1 do {MAX_SUBJECT_LENGTH} znaków")
    issued = now or datetime.now(UTC)
    lifetime = timedelta(minutes=minutes or settings.jwt_expire_minutes)
    claims = {"sub": subject, "iat": issued, "exp": issued + lifetime}
    return jwt.encode(claims, _secret(settings), algorithm=settings.jwt_algorithm)


def verify_token(token: str, settings: ApiSettings) -> str:
    """Zwraca podmiot (``sub``) z poprawnego tokenu albo rzuca ``ApiError`` 401."""
    try:
        claims = jwt.decode(
            token,
            _secret(settings),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
            leeway=settings.jwt_leeway_seconds,
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("TOKEN_EXPIRED", "Token wygasł") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("TOKEN_INVALID", "Nieprawidłowy token") from exc

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > MAX_SUBJECT_LENGTH:
        raise _unauthorized("TOKEN_INVALID", "Nieprawidłowy podmiot tokenu")
    return subject


async def current_subject(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> str:
    if not settings.jwt_enabled:
        return ANONYMOUS_SUBJECT
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("AUTH_REQUIRED", "Wymagany token JWT (nagłówek Authorization: Bearer)")
    return verify_token(credentials.credentials, settings)


Subject = Annotated[str, Depends(current_subject)]
