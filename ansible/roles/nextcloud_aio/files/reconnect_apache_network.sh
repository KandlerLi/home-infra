#!/usr/bin/env bash
#
# Reconnects nextcloud-aio-apache to the nextcloud-aio Docker network if
# it's missing -- confirmed live on two independent host reboots
# (2026-09-01, 2026-09-04, see PARKED.md's "Homeserver: make a reboot a
# complete non-event") that Apache silently drops off that network,
# deadlocking with nextcloud-aio-nextcloud (each waiting on the other's
# own name resolution). Root cause still unknown (most likely a
# startup-ordering race when every AIO container restarts simultaneously)
# -- this only automates the manual recovery
# (`docker network connect nextcloud-aio nextcloud-aio-apache`) already
# proven to work both times, it doesn't fix the underlying race.
#
# Run at boot (via the accompanying systemd unit) and safe to run any
# other time too -- idempotent, exits cleanly if Apache is already
# connected or hasn't appeared yet.

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

# AIO's own mastercontainer creates and starts its child containers on
# its own schedule after a host reboot -- there's no systemd-visible
# signal for "AIO has finished starting everything," so this polls
# rather than assuming Apache exists by the time this unit runs.
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

printf '%s is missing from %s -- reconnecting (known AIO startup race, see PARKED.md).\n' \
  "$TARGET_CONTAINER" "$NETWORK_NAME"
docker network connect "$NETWORK_NAME" "$TARGET_CONTAINER"
printf 'Reconnected %s to %s.\n' "$TARGET_CONTAINER" "$NETWORK_NAME"
