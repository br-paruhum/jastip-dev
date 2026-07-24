#!/usr/bin/env bash
#
# Deploy the pushed `main` branch to a remote Jastip environment.
# Runs ON THE REMOTE: git pull → (migrate) → (collectstatic) → restart.
# NEVER seeds — seeding clobbers admin-edited content on stg/prd.
#
# Run this from your local dev repo AFTER you have committed and pushed to
# origin/main (e.g. an edit to apps/pages/how_to_body.html for the How-To page).
#
# Usage:
#   ./scripts/deploy.sh                 # staging, full deploy (default)
#   ./scripts/deploy.sh stg             # staging, full deploy
#   ./scripts/deploy.sh prd             # PRODUCTION (asks you to confirm)
#   ./scripts/deploy.sh stg --content   # code-only: pull → restart
#                                       #   (skips migrate + collectstatic — for
#                                       #    template/text edits with no new static)
#
# Auth: reads the password from $JASTIP_SSH_PASS if set, otherwise prompts once.
#       The same password is used for the SSH login and for `sudo systemctl restart`.
#
set -euo pipefail

HOST="claude@43.133.154.27"

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

# CloudPanel VPS: app dirs are 0770 owned by the per-site user, so git/migrate/
# collectstatic run AS that user (SITEUSER); only the systemctl restart is root.
case "$env" in
  stg) DIR="/home/goproxybuy-staging/htdocs/staging.goproxybuy.com"; SITEUSER="goproxybuy-staging"; SERVICE="jastip-stg"; URL="https://staging.goproxybuy.com" ;;
  prd) DIR="/home/goproxybuy/htdocs/www.goproxybuy.com"; SITEUSER="goproxybuy"; SERVICE="jastip-prd"; URL="https://www.goproxybuy.com" ;;
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
  "DIR='$DIR' SITEUSER='$SITEUSER' SERVICE='$SERVICE' MODE='$mode' SUDO_PW='$PW' bash -s" <<'REMOTE'
set -e

# Cache claude's sudo credentials once so the inner `sudo -u` and the restart
# don't need the password piped in (piping conflicts with the heredoc on stdin).
echo "$SUDO_PW" | sudo -S -v 2>/dev/null

# git pull / migrate / collectstatic run AS the site user (owns the 0770 app dir).
# NEVER seed on stg or prd — `seed` overwrites admin-edited page bodies
# (FAQ, Blogs, Privacy, Terms) and has clobbered production content repeatedly.
sudo -u "$SITEUSER" -H env DIR="$DIR" MODE="$MODE" bash -s <<'SITE'
set -e
PY="$DIR/.venv/bin/python"
cd "$DIR"
echo "  • git pull"
git pull --ff-only | sed 's/^/    /'
if [[ "$MODE" != "content" ]]; then
  echo "  • migrate"
  "$PY" manage.py migrate --noinput 2>&1 | grep -iE "applying|no migrations" | sed 's/^/    /' || true
  echo "  • collectstatic"
  "$PY" manage.py collectstatic --noinput 2>&1 | tail -1 | sed 's/^/    /'
fi
SITE

echo "  • restart $SERVICE"
sudo systemctl restart "$SERVICE"
sleep 2
echo "    service: $(systemctl is-active "$SERVICE")"
REMOTE

echo "✓ Done — ${URL}"
