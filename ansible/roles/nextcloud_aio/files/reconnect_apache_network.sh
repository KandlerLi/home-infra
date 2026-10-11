#!/usr/bin/env bash
# Reconnects nextcloud-aio-apache to its Docker network after a reboot: see the nextcloud-aio runbook ("Known issues").

set -Eeuo pipefail

readonly NETWORK_NAME="nextcloud-aio"
readonly TARGET_CONTAINER="nextcloud-aio-apache"
readonly WAIT_TIMEOUT_SECONDS=600
readonly POLL_INTERVAL_SECONDS=5

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

command -v docker >/dev/null || die "docker is not on PATH."

# AIO starts its children on its own schedule, so poll.
deadline=$((SECONDS + WAIT_TIMEOUT_SECONDS))
while true; do
  if [[ "$(docker inspect --format '{{.State.Running}}' "$TARGET_CONTAINER" 2>/dev/null || true)" == "true" ]]; then
    break
  fi

  if ((SECONDS >= deadline)); then
    printf '%s did not become running within %ds; nothing to reconnect.\n' \
      "$TARGET_CONTAINER" "$WAIT_TIMEOUT_SECONDS"
    exit 0
  fi

  sleep "$POLL_INTERVAL_SECONDS"
done

connected_containers=$(
  docker network inspect "$NETWORK_NAME" \
    --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null
) || die "Could not inspect the $NETWORK_NAME network."

if [[ " $connected_containers " == *" $TARGET_CONTAINER "* ]]; then
  printf '%s is already connected to %s; nothing to do.\n' \
    "$TARGET_CONTAINER" "$NETWORK_NAME"
  exit 0
fi

printf '%s is missing from %s -- reconnecting (known AIO startup race).\n' \
  "$TARGET_CONTAINER" "$NETWORK_NAME"
docker network connect "$NETWORK_NAME" "$TARGET_CONTAINER"
printf 'Reconnected %s to %s.\n' "$TARGET_CONTAINER" "$NETWORK_NAME"
