#!/usr/bin/env bash
# Uruchamia system COI jednym poleceniem: buduje obrazy, startuje usługi z panelem urzędnika,
# czeka na gotowość i wystawia token JWT do panelu (Linux, macOS, Git Bash; Windows: start.cmd).
#
#   ./start.sh               start i otwarcie panelu w przeglądarce
#   ./start.sh --no-browser  start bez otwierania przeglądarki
#   ./start.sh --stop        zatrzymanie (dane w bazie zostają)
#   ./start.sh --token       nowy token JWT (np. po wygaśnięciu)
#   ./start.sh --logs        logi API i workera na żywo (Ctrl+C kończy)
#
# Pierwsze uruchomienie tworzy .env z .env.example z losowymi sekretami i pyta o klucz Eden AI
# (bez klucza: tryb demonstracyjny). Istniejącego .env skrypt nie zmienia.
# Pierwsze budowanie obrazów trwa kilkanaście minut (~5 GB).
set -euo pipefail
cd "$(dirname "$0")"

USER_SUB="${COI_USER:-urzednik}"
TOKEN_MINUTES="${COI_TOKEN_MINUTES:-480}"

compose() { docker compose --profile frontend "$@"; }
step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
fail() { printf '\033[31mBŁĄD: %s\033[0m\n' "$1" >&2; exit 1; }

env_value() {  # wartość z .env albo domyślna ($2)
  local value
  value=$(sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" .env 2>/dev/null | tail -n 1 | tr -d "\r\"'")
  printf '%s' "${value:-$2}"
}

# base64url bez dopełnienia: bezpieczne w .env i w adresie bazy (DATABASE_URL)
new_secret() { head -c 48 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }

create_env() {
  step 'Tworzę .env z .env.example (losowe sekrety)'
  local key="${EDENAI_API_KEY:-}" jwt pg line
  if [ -z "$key" ] && [ -t 0 ]; then
    read -rsp 'Klucz API Eden AI (Enter = tryb demonstracyjny bez modelu): ' key
    echo
  fi
  jwt=$(new_secret)
  pg=$(new_secret)
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      JWT_SECRET_KEY=*) printf 'JWT_SECRET_KEY=%s\n' "$jwt" ;;
      POSTGRES_PASSWORD=*) printf 'POSTGRES_PASSWORD=%s\n' "$pg" ;;
      EDENAI_API_KEY=*) printf 'EDENAI_API_KEY=%s\n' "$key" ;;
      LLM_DEMO_MODE=*) if [ -z "$key" ]; then echo 'LLM_DEMO_MODE=true'; else printf '%s\n' "$line"; fi ;;
      *) printf '%s\n' "$line" ;;
    esac
  done < .env.example > .env
  chmod 600 .env
  if [ -z "$key" ]; then
    echo 'Brak klucza Eden AI: LLM_DEMO_MODE=true — wyniki demonstracyjne, nie do importu.'
    echo 'Aby użyć modelu: wpisz EDENAI_API_KEY w .env, ustaw LLM_DEMO_MODE=false i uruchom ponownie.'
  fi
}

show_token() {
  local jwt clip copied=''
  jwt=$(compose exec -T api python -m backend.api.token_cli --sub "$USER_SUB" --minutes "$TOKEN_MINUTES" | tr -d '\r\n') || jwt=''
  if [ -z "$jwt" ]; then
    echo 'Nie wystawiono tokenu (JWT wyłączony albo API nie działa — sprawdź: ./start.sh --logs).'
    return
  fi
  for clip in pbcopy wl-copy 'xclip -selection clipboard' clip.exe; do
    if command -v "${clip%% *}" >/dev/null 2>&1 && printf '%s' "$jwt" | $clip 2>/dev/null; then
      copied=' jest w schowku —'
      break
    fi
  done
  echo "Token JWT dla '$USER_SUB' (ważny $((TOKEN_MINUTES / 60)) h)$copied w panelu kliknij „Token API” i wklej:"
  echo "$jwt"
}

open_browser() {
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "$1" || true
  elif command -v start >/dev/null 2>&1; then start "$1" || true  # Git Bash
  fi
}

case "${1:-}" in
  --stop) step 'Zatrzymuję usługi (dane w bazie zostają)'; compose down; exit 0 ;;
  --logs) compose logs -f --tail 100 api worker; exit 0 ;;
  --token) show_token; exit 0 ;;
  -h | --help) sed -n '2,13p' "$0"; exit 0 ;;
  '' | --no-browser) ;;
  *) fail "nieznana opcja: $1 (pomoc: ./start.sh --help)" ;;
esac

[ -f .env ] || create_env
if [ -z "$(env_value EDENAI_API_KEY '')" ] && [ "$(env_value LLM_DEMO_MODE false)" != 'true' ]; then
  echo 'Uwaga: .env nie ma EDENAI_API_KEY, a LLM_DEMO_MODE=false — analizy zakończą się błędem.'
fi
docker info >/dev/null 2>&1 || fail 'Docker nie działa — uruchom Dockera i spróbuj ponownie.'

step 'Buduję obrazy i uruchamiam usługi (pierwszy raz: kilkanaście minut)'
compose up -d --build --wait --wait-timeout 900 || fail 'usługi nie wystartowały — sprawdź: ./start.sh --logs'

front="http://localhost:$(env_value FRONTEND_PORT 8080)"
api="http://localhost:$(env_value API_PORT 8000)"
echo
echo "Panel urzędnika:  $front"
echo "API (OpenAPI):    $api/api/docs"
echo
show_token
echo
echo 'Zatrzymanie: ./start.sh --stop   Nowy token: ./start.sh --token   Logi: ./start.sh --logs'
if [ "${1:-}" != '--no-browser' ]; then open_browser "$front"; fi
