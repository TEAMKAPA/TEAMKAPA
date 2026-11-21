"""Wystawia token JWT dla użytkownika API (sekret i algorytm z konfiguracji runtime).

Użycie w działającym kontenerze (sekret jest tylko tam):
    docker compose exec api python -m backend.api.token_cli --sub urzednik
"""

import argparse
import sys

from pydantic import ValidationError

from backend.api.security import MAX_SUBJECT_LENGTH, issue_token
from backend.shared.config import ApiSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wystaw token JWT dla API COI.")
    parser.add_argument("--sub", required=True, help="Identyfikator użytkownika (claim sub)")
    parser.add_argument(
        "--minutes",
        type=int,
        default=None,
        help="Ważność w minutach (domyślnie JWT_EXPIRE_MINUTES)",
    )
    args = parser.parse_args(argv)

    try:
        settings = ApiSettings()
    except ValidationError as exc:
        print(f"Błędna konfiguracja: {exc}", file=sys.stderr)
        return 2
    if not settings.jwt_enabled:
        print("JWT_ENABLED=false — API nie wymaga tokenów.", file=sys.stderr)
        return 2
    if not 0 < len(args.sub) <= MAX_SUBJECT_LENGTH:
        print(f"--sub musi mieć od 1 do {MAX_SUBJECT_LENGTH} znaków", file=sys.stderr)
        return 2
    if args.minutes is not None and not 1 <= args.minutes <= 24 * 60:
        print("--minutes musi być z zakresu 1–1440", file=sys.stderr)
        return 2

    print(issue_token(settings, args.sub, minutes=args.minutes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
