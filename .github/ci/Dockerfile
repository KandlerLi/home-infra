FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copied from the repo root (the build context is "." here, not ".github/ci"
# like the other repos' CI images) so pip/collection versions have exactly
# one source of truth: this repo's own requirements.txt/requirements.yml,
# never duplicated into this Dockerfile.
COPY requirements.txt requirements.yml ./

# --break-system-packages: this is an ephemeral CI image, not a system
# Python anyone else depends on -- Debian trixie's PEP 668 protection has
# nothing to protect here.
# --resume-retries: the self-hosted runner's uplink has been observed to
# stall mid-download of the ansible wheel (~59MB) under transient
# congestion, confirmed live 2026-09-16 (4 consecutive CI failures, all
# "Connection timed out while downloading" at varying byte offsets, with
# a direct curl/docker-container download succeeding minutes later at
# ~5MB/s) -- this resumes the partial download instead of restarting the
# whole file from byte zero on every retry.
RUN pip install --break-system-packages --no-cache-dir --resume-retries 5 -r requirements.txt

# /usr/share/ansible/collections is one of Ansible's default collection
# search paths regardless of which user runs ansible-playbook later --
# unlike the plain default (~/.ansible/collections), which would only be
# found under whichever UID's home directory did the install. The
# validate job runs as the runner's own service account (--user uid:gid),
# not root, so installing here at build time (as root) still has to land
# somewhere every later UID can actually find.
RUN ansible-galaxy collection install -r requirements.yml -p /usr/share/ansible/collections

WORKDIR /workspace
