#!/usr/bin/env bash

set -Eeuo pipefail

readonly AIO_MASTER_CONTAINER="nextcloud-aio-mastercontainer"
readonly DATA_MOUNT="/mnt/black-hdd"
readonly STOP_TIMEOUT_SECONDS=600

services_may_be_stopped=false

usage() {
  cat <<'EOF'
Usage: sudo bash shutdown-homeserver-for-maintenance.sh [--check]

Safely prepare the homeserver for hardware maintenance by stopping the
Nextcloud AIO application containers and then powering off the host.

Options:
  --check  Run read-only preflight checks without stopping or powering off.
  -h, --help
           Show this help text.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

on_exit() {
  local status=$?

  if ((status != 0)) && [[ "$services_may_be_stopped" == true ]]; then
    printf '%s\n' \
      'Poweroff was not completed. Some Nextcloud containers may be stopped.' \
      'Resolve the reported error, then either rerun this script or start the' \
      'containers through the Nextcloud AIO interface.' >&2
  fi
}

trap on_exit EXIT

container_is_running() {
  local wanted_name=$1
  local name
  local docker_output

  docker_output=$(docker ps --format '{{.Names}}') \
    || die "Could not read Docker container state."

  while IFS= read -r name; do
    if [[ "$name" == "$wanted_name" ]]; then
      return 0
    fi
  done <<<"$docker_output"

  return 1
}

read_running_aio_children() {
  local name
  local docker_output
  running_aio_children=()

  docker_output=$(docker ps --format '{{.Names}}') \
    || die "Could not read Docker container state."

  while IFS= read -r name; do
    if [[ "$name" == nextcloud-aio-* \
      && "$name" != "$AIO_MASTER_CONTAINER" ]]; then
      running_aio_children+=("$name")
    fi
  done <<<"$docker_output"
}

check_prerequisites() {
  local command_name
  local failed_units
  local virsh_output
  local vm_name
  local -a running_vms=()

  ((EUID == 0)) || die "Run this script with sudo."

  for command_name in docker mountpoint systemctl timeout; do
    command -v "$command_name" >/dev/null \
      || die "Required command is unavailable: $command_name"
  done

  systemctl is-active --quiet docker \
    || die "Docker is not running; inspect the host before powering it off."

  docker ps --format '{{.Names}}' >/dev/null \
    || die "Docker is running, but its container state could not be read."

  mountpoint --quiet "$DATA_MOUNT" \
    || die "$DATA_MOUNT is not mounted; inspect storage before continuing."

  if command -v virsh >/dev/null; then
    virsh_output=$(virsh list --name) \
      || die "Could not read libvirt VM state."
    while IFS= read -r vm_name; do
      [[ -n "$vm_name" ]] && running_vms+=("$vm_name")
    done <<<"$virsh_output"
    if ((${#running_vms[@]} > 0)); then
      printf 'Running virtual machines:\n' >&2
      printf '  %s\n' "${running_vms[@]}" >&2
      die "Stop or verify active VM workloads before shutting down the host."
    fi
  fi

  failed_units=$(systemctl --failed --no-legend --plain || true)
  if [[ -n "$failed_units" ]]; then
    printf 'WARNING: failed systemd units were found:\n%s\n' "$failed_units" >&2
  fi

  read_running_aio_children

  if ! container_is_running "$AIO_MASTER_CONTAINER" \
    && ((${#running_aio_children[@]} > 0)); then
    printf 'Running AIO containers without the master container:\n' >&2
    printf '  %s\n' "${running_aio_children[@]}" >&2
    die "Inspect the inconsistent AIO state before powering off."
  fi
}

mode="shutdown"

case "${1:-}" in
  "") ;;
  --check) mode="check" ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

check_prerequisites

printf 'Preflight passed:\n'
printf '  Docker is running.\n'
printf '  %s is mounted.\n' "$DATA_MOUNT"
printf '  No running virtual machines were found.\n'

if container_is_running "$AIO_MASTER_CONTAINER"; then
  printf '  The Nextcloud AIO master container is running.\n'
else
  printf '  The Nextcloud AIO master container is already stopped.\n'
fi

if [[ "$mode" == "check" ]]; then
  printf 'Check completed; no services were stopped.\n'
  exit 0
fi

printf '\nThis will stop Nextcloud and power off the homeserver.\n'
printf 'Confirm that users have finished uploads and a suitable backup exists.\n'
printf 'Type POWER OFF to continue: '
read -r confirmation </dev/tty

[[ "$confirmation" == "POWER OFF" ]] || die "Confirmation did not match; no changes made."

if container_is_running "$AIO_MASTER_CONTAINER"; then
  printf 'Stopping Nextcloud AIO application containers...\n'
  services_may_be_stopped=true
  timeout --foreground "${STOP_TIMEOUT_SECONDS}s" docker exec \
    --env STOP_CONTAINERS=1 \
    "$AIO_MASTER_CONTAINER" \
    /daily-backup.sh

  deadline=$((SECONDS + STOP_TIMEOUT_SECONDS))
  while true; do
    read_running_aio_children
    if ((${#running_aio_children[@]} == 0)); then
      break
    fi

    if ((SECONDS >= deadline)); then
      printf 'AIO containers still running after %d seconds:\n' \
        "$STOP_TIMEOUT_SECONDS" >&2
      printf '  %s\n' "${running_aio_children[@]}" >&2
      die "Refusing to power off because the AIO stop could not be verified."
    fi

    sleep 5
  done
else
  printf 'The AIO master container is already stopped; skipping the stop request.\n'
fi

printf 'Nextcloud AIO application containers are stopped.\n'
printf '%s\n' \
  'After the next boot, start them through the AIO interface if needed:' \
  '  https://192.168.178.100:8080'
printf 'Flushing filesystem buffers and powering off now...\n'
sync
systemctl poweroff
