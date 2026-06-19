#!/usr/bin/env bash
#
# Control the Jastip STAGING web app (gunicorn under systemd).
#
# Usage:
#   ./stg.sh start      # start the service
#   ./stg.sh stop       # stop the service
#   ./stg.sh restart    # restart the service
#   ./stg.sh status      # show service status
#   ./stg.sh logs        # follow the live journal (Ctrl-C to exit)
#
# Runs `sudo systemctl …`, so it will prompt for your password the first time.
set -euo pipefail

SERVICE="jastip-stg"
URL="https://stg.jastip.me"

usage() {
  echo "Usage: $(basename "$0") {start|stop|restart|status|logs}" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage

case "$1" in
  start)
    echo "→ Starting ${SERVICE}…"
    sudo systemctl start "${SERVICE}"
    sleep 1
    sudo systemctl --no-pager --lines=0 status "${SERVICE}" || true
    echo "✓ ${SERVICE} started — ${URL}"
    ;;
  stop)
    echo "→ Stopping ${SERVICE}…"
    sudo systemctl stop "${SERVICE}"
    echo "✓ ${SERVICE} stopped"
    ;;
  restart)
    echo "→ Restarting ${SERVICE}…"
    sudo systemctl restart "${SERVICE}"
    sleep 1
    sudo systemctl --no-pager --lines=0 status "${SERVICE}" || true
    echo "✓ ${SERVICE} restarted — ${URL}"
    ;;
  status)
    systemctl --no-pager status "${SERVICE}"
    ;;
  logs)
    echo "→ Following ${SERVICE} journal (Ctrl-C to exit)…"
    journalctl -u "${SERVICE}" -f -q
    ;;
  *)
    usage
    ;;
esac
