#!/usr/bin/env bash
#
# Deploy the pushed `main` branch to a remote Jastip environment.
# Runs ON THE REMOTE: git pull → (migrate) → seed → (collectstatic) → restart.
#
# Run this from your local dev repo AFTER you have committed and pushed to
# origin/main (e.g. an edit to apps/pages/how_to_body.html for the How-To page).
#
# Usage:
#   ./scripts/deploy.sh                 # staging, full deploy (default)
#   ./scripts/deploy.sh stg             # staging, full deploy
#   ./scripts/deploy.sh prd             # PRODUCTION (asks you to confirm)
#   ./scripts/deploy.sh stg --content   # content-only: pull → seed → restart
#                                       #   (skips migrate + collectstatic — perfect
#                                       #    for How-To / static page text edits)
#
# Auth: reads the password from $JASTIP_SSH_PASS if set, otherwise prompts once.
#       The same password is used for the SSH login and for `sudo systemctl restart`.
#
set -euo pipefail

HOST="claude@43.129.52.60"

# ── Parse args (env + optional --content) ────────────────────────────────────
env="stg"
mode="full"
for arg in "$@"; do
  case "$arg" in
    stg|staging)     env="stg" ;;
    prd|prod|production) env="prd" ;;
    --content)       mode="content" ;;
    -h|--help)       sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; echo "Usage: $(basename "$0") [stg|prd] [--content]" >&2; exit 2 ;;
  esac
done

case "$env" in
  stg) DIR="/var/www/jastip-stg"; SERVICE="jastip-stg"; URL="https://stg.jastip.me" ;;
  prd) DIR="/var/www/jastip-prd"; SERVICE="jastip-prd"; URL="https://www.jastip.me" ;;
esac

# ── Safety: confirm production, and warn if local HEAD isn't pushed ───────────
if [[ "$env" == "prd" ]]; then
  echo "⚠️  You are deploying to PRODUCTION (${URL})."
  read -r -p "   Type 'yes' to continue: " confirm
  [[ "$confirm" == "yes" ]] || { echo "Aborted."; exit 1; }
fi

if git rev-parse --git-dir >/dev/null 2>&1; then
  git fetch -q origin main 2>/dev/null || true
  local_head="$(git rev-parse HEAD 2>/dev/null || echo local)"
  remote_head="$(git rev-parse origin/main 2>/dev/null || echo remote)"
  if [[ "$local_head" != "$remote_head" ]]; then
    echo "⚠️  Local HEAD differs from origin/main — did you commit & push your edit?"
    echo "    local : $local_head"
    echo "    origin: $remote_head"
    read -r -p "   Continue anyway? [y/N]: " go
    [[ "$go" =~ ^[Yy]$ ]] || { echo "Aborted — run 'git push origin main' first."; exit 1; }
  fi
fi

# ── Password (env var or single prompt, reused for ssh + sudo) ────────────────
PW="${JASTIP_SSH_PASS:-}"
if [[ -z "$PW" ]]; then
  read -rsp "SSH/sudo password for ${HOST}: " PW; echo
fi
export SSHPASS="$PW"

command -v sshpass >/dev/null 2>&1 || { echo "✗ sshpass not installed (sudo apt install sshpass)"; exit 1; }

echo "→ Deploying origin/main to ${env} (${DIR}) — mode: ${mode}"

# ── Run the deploy on the remote ─────────────────────────────────────────────
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$HOST" \
  "DIR='$DIR' SERVICE='$SERVICE' MODE='$mode' SUDO_PW='$PW' bash -s" <<'REMOTE'
set -e
PY="$DIR/.venv/bin/python"
cd "$DIR"

echo "  • git pull"
git pull --ff-only | sed 's/^/    /'

if [[ "$MODE" != "content" ]]; then
  echo "  • migrate"
  "$PY" manage.py migrate --noinput 2>&1 | grep -iE "applying|no migrations" | sed 's/^/    /' || true
fi

echo "  • seed (refresh page bodies, idempotent)"
"$PY" manage.py seed 2>&1 | grep -iE "site pages|seed complete" | sed 's/^/    /' || true

if [[ "$MODE" != "content" ]]; then
  echo "  • collectstatic"
  "$PY" manage.py collectstatic --noinput 2>&1 | tail -1 | sed 's/^/    /'
fi

echo "  • restart $SERVICE"
echo "$SUDO_PW" | sudo -S systemctl restart "$SERVICE" 2>&1 | grep -vi password || true
sleep 2
echo "    service: $(systemctl is-active "$SERVICE")"
REMOTE

echo "✓ Done — ${URL}"
