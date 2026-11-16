#!/usr/bin/env bash
set -euo pipefail

SERVICE="neuroionm.service"
HEALTH_URL="http://127.0.0.1:8200/api/ready"
STATE_DIR="/run/neuroionm-healthcheck"
FAIL_FILE="$STATE_DIR/failures"
RESTART_FILE="$STATE_DIR/last_restart_epoch"
MIN_FAILURES_BEFORE_RESTART=2
MIN_RESTART_INTERVAL_SECONDS=600

install -d -m 755 "$STATE_DIR"

is_healthy() {
  systemctl is-active --quiet "$SERVICE" && curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null
}

if is_healthy; then
  echo 0 > "$FAIL_FILE"
  exit 0
fi

failures=0
if [[ -f "$FAIL_FILE" ]]; then
  failures="$(cat "$FAIL_FILE" 2>/dev/null || echo 0)"
fi
if ! [[ "$failures" =~ ^[0-9]+$ ]]; then
  failures=0
fi
failures=$((failures + 1))
echo "$failures" > "$FAIL_FILE"

logger -t neuroionm-healthcheck "health check failed count=$failures"

if (( failures < MIN_FAILURES_BEFORE_RESTART )); then
  exit 0
fi

now="$(date +%s)"
last_restart=0
if [[ -f "$RESTART_FILE" ]]; then
  last_restart="$(cat "$RESTART_FILE" 2>/dev/null || echo 0)"
fi
if ! [[ "$last_restart" =~ ^[0-9]+$ ]]; then
  last_restart=0
fi

if (( now - last_restart < MIN_RESTART_INTERVAL_SECONDS )); then
  logger -t neuroionm-healthcheck "restart skipped due to rate limit"
  exit 0
fi

logger -t neuroionm-healthcheck "restarting $SERVICE after repeated health failures"
systemctl restart "$SERVICE"
echo "$now" > "$RESTART_FILE"
echo 0 > "$FAIL_FILE"