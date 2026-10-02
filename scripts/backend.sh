#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT/backend"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
check_space() {
    available=$(df -Pk . | awk 'NR==2 {print $4}')
    [ "$available" -ge 2097152 ] || fail "Less than 2 GiB free; free space before continuing. No data was deleted."
}

for tool in uv docker; do
    command -v "$tool" >/dev/null 2>&1 || fail "Install $tool before starting the backend."
done
docker compose version >/dev/null || fail "Docker Compose is required."
docker info >/dev/null 2>&1 || fail "Docker is not reachable; start it or check permissions."
check_space

env_file=.env.example
if [ -f .env ]; then env_file=.env; fi
printf 'Using %s (local database persists until explicitly removed).\n' "$env_file"
uv sync --frozen
check_space

port=$(uv run --frozen --env-file "$env_file" python -c 'from services.settings import load_settings; print(load_settings().api_port)')
uv run --frozen --env-file "$env_file" python -c '
import socket, sys
from services.settings import load_settings
try:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", load_settings().api_port))
except OSError:
    sys.exit("ERROR: API port unavailable; set OVRLY_API_PORT to an unused local port.")
'
docker compose --env-file "$env_file" pull db
check_space
docker compose --env-file "$env_file" up -d --wait db
check_space
uv run --frozen --env-file "$env_file" alembic upgrade head

printf 'Starting API on 127.0.0.1:%s with the lifecycle-only embedded worker.\n' "$port"
printf 'Readiness: http://127.0.0.1:%s/healthz. Ctrl+C stops the API/worker, not PostgreSQL.\n' "$port"
export OVRLY_EMBED_WORKER=1
exec uv run --frozen --env-file "$env_file" uvicorn services.api.main:create_app --factory --host 127.0.0.1 --port "$port"
