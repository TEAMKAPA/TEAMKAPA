from datetime import UTC, datetime, timedelta

import jwt
import pytest

from backend.api.errors import ApiError
from backend.api.security import issue_token, verify_token
from backend.api.token_cli import main as token_cli
from backend.shared.config import ApiSettings
from tests.conftest import TEST_JWT_SECRET


@pytest.fixture
def settings() -> ApiSettings:
    return ApiSettings()


def _code(exc: pytest.ExceptionInfo[ApiError]) -> str:
    assert exc.value.status_code == 401
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}
    return exc.value.code


def test_round_trip(settings: ApiSettings) -> None:
    assert verify_token(issue_token(settings, "urzednik"), settings) == "urzednik"


def test_expired_token(settings: ApiSettings) -> None:
    token = issue_token(settings, "u", minutes=1, now=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(ApiError) as exc:
        verify_token(token, settings)
    assert _code(exc) == "TOKEN_EXPIRED"


def test_leeway_tolerates_small_clock_skew(settings: ApiSettings) -> None:
    token = issue_token(settings, "u", minutes=1, now=datetime.now(UTC) - timedelta(seconds=70))
    assert verify_token(token, settings) == "u"


def test_wrong_secret(settings: ApiSettings) -> None:
    token = jwt.encode(
        {"sub": "u", "exp": datetime.now(UTC) + timedelta(minutes=5)}, "x" * 40, algorithm="HS256"
    )
    with pytest.raises(ApiError) as exc:
        verify_token(token, settings)
    assert _code(exc) == "TOKEN_INVALID"


def test_algorithm_none_rejected(settings: ApiSettings) -> None:
    token = jwt.encode(
        {"sub": "u", "exp": datetime.now(UTC) + timedelta(minutes=5)}, None, algorithm="none"
    )
    with pytest.raises(ApiError) as exc:
        verify_token(token, settings)
    assert _code(exc) == "TOKEN_INVALID"


@pytest.mark.filterwarnings("ignore:The HMAC key")
def test_other_algorithm_rejected(settings: ApiSettings) -> None:
    token = jwt.encode(
        {"sub": "u", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        TEST_JWT_SECRET,
        algorithm="HS512",
    )
    with pytest.raises(ApiError) as exc:
        verify_token(token, settings)
    assert _code(exc) == "TOKEN_INVALID"


@pytest.mark.parametrize(
    "claims", [{"sub": "u"}, {"exp": 4_000_000_000}, {"sub": "", "exp": 4_000_000_000}]
)
def test_required_claims(settings: ApiSettings, claims: dict[str, object]) -> None:
    token = jwt.encode(claims, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(ApiError) as exc:
        verify_token(token, settings)
    assert _code(exc) == "TOKEN_INVALID"


def test_issue_rejects_bad_subject(settings: ApiSettings) -> None:
    with pytest.raises(ValueError, match="sub"):
        issue_token(settings, "")
    with pytest.raises(ValueError, match="sub"):
        issue_token(settings, "x" * 129)


def test_token_cli_prints_valid_token(
    settings: ApiSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert token_cli(["--sub", "urzednik", "--minutes", "5"]) == 0
    token = capsys.readouterr().out.strip()
    assert verify_token(token, settings) == "urzednik"


def test_token_cli_refuses_when_jwt_disabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JWT_ENABLED", "false")
    assert token_cli(["--sub", "u"]) == 2
    assert "JWT_ENABLED=false" in capsys.readouterr().err


def test_token_cli_validates_minutes(capsys: pytest.CaptureFixture[str]) -> None:
    assert token_cli(["--sub", "u", "--minutes", "0"]) == 2
    assert "--minutes" in capsys.readouterr().err
