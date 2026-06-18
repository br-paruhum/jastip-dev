#!/usr/bin/env bash
# Start the Jastip local dev server.
#   ./run.sh            -> http://localhost:8019/
#   ./run.sh 8025       -> use a different port
#   ./run.sh --migrate  -> run migrations first, then serve (on 8019)
#
# Stop it with Ctrl-C.
set -euo pipefail

# Always run from the project root (the dir this script lives in).
cd "$(dirname "$0")"

PY=".venv/bin/python"
PORT="8019"
DO_MIGRATE="0"

for arg in "$@"; do
  case "$arg" in
    --migrate) DO_MIGRATE="1" ;;
    [0-9]*)    PORT="$arg" ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

if [[ ! -x "$PY" ]]; then
  echo "✗ virtualenv not found at $PY — create it first:" >&2
  echo "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [[ "$DO_MIGRATE" == "1" ]]; then
  echo "→ Applying migrations…"
  "$PY" manage.py migrate
fi

echo "→ ProxyBuying dev server starting on http://localhost:${PORT}/"
echo "  Blog:  http://localhost:${PORT}/blog/"
echo "  Admin: http://localhost:${PORT}/admin/   (admin@jastip.me / ChangeMe!2026)"
echo "  Press Ctrl-C to stop."
exec "$PY" manage.py runserver "127.0.0.1:${PORT}"
