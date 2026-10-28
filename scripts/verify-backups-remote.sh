#!/usr/bin/env bash
# Runs as root on the homeserver, fed by scripts/verify-backups.sh: see home-infra-docs' runbooks/verify-backups.md.
# stdin: line 1 the Borg passphrase, line 2 the Authelia storage encryption key (empty skips that check).

set -uo pipefail

red_hdd=/mnt/red-hdd
borg_image=ghcr.io/nextcloud-releases/aio-borgbackup:latest
authelia_image=authelia/authelia:4.39.22

IFS= read -r BORG_PASSPHRASE || BORG_PASSPHRASE=""
IFS= read -r AUTHELIA_STORAGE_ENCRYPTION_KEY || AUTHELIA_STORAGE_ENCRYPTION_KEY=""
export BORG_PASSPHRASE AUTHELIA_STORAGE_ENCRYPTION_KEY

scratch="$(mktemp -d "${red_hdd}/restore-drill.XXXXXX")"
chmod 0700 "${scratch}"
trap 'rm -rf "${scratch}"' EXIT

failures=0
pass() { printf 'PASS  %-12s %s\n' "$1" "$2"; }
fail() { printf 'FAIL  %-12s %s\n' "$1" "$2"; failures=$((failures + 1)); }
skip() { printf 'SKIP  %-12s %s\n' "$1" "$2"; }
age_hours() { echo $(( ($(date +%s) - $(stat -c %Y "$1")) / 3600 )); }

sqlite_ok() {
  python3 - "$1" <<'EOF'
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
result = conn.execute("PRAGMA integrity_check").fetchone()[0]
tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
print(f"{result}, {tables} tables")
sys.exit(0 if result == "ok" and tables > 0 else 1)
EOF
}

# Deluge config and Open WebUI data: newest tarball of each, extracted.
for name in deluge-config open-webui; do
  tarball="$(ls -1t "${red_hdd}/aux-backups/${name}"-*.tar.gz 2>/dev/null | head -1)"
  if [ -z "${tarball}" ]; then
    fail "${name}" "no tarball in ${red_hdd}/aux-backups"
    continue
  fi
  dest="${scratch}/aux/${name}"
  mkdir -p "${dest}"
  if ! tar -xzf "${tarball}" -C "${dest}"; then
    fail "${name}" "$(basename "${tarball}") doesn't extract"
    continue
  fi
  files="$(find "${dest}" -type f | wc -l)"
  detail="$(basename "${tarball}"), $(age_hours "${tarball}") h old, ${files} files"
  if [ "${name}" = open-webui ]; then
    db="$(find "${dest}" -name webui.db | head -1)"
    if [ -n "${db}" ] && sqlite_out="$(sqlite_ok "${db}")"; then
      pass "${name}" "${detail}, webui.db ${sqlite_out}"
    else
      fail "${name}" "${detail}, webui.db missing or not ok: ${sqlite_out:-}"
    fi
  elif [ "${files}" -gt 0 ]; then
    pass "${name}" "${detail}"
  else
    fail "${name}" "${detail}"
  fi
done

# Authelia: newest gzipped SQLite copy; then the key check, which fails for backups made before a key rotation.
backup="$(ls -1t "${red_hdd}"/authelia-backup/authelia-db-*.sqlite3.gz 2>/dev/null | head -1)"
if [ -z "${backup}" ]; then
  fail authelia "no backup in ${red_hdd}/authelia-backup"
else
  dest="${scratch}/authelia"
  mkdir -p "${dest}"
  # The image may not run as root; the parent scratch dir stays 0700 root on the host.
  chmod 0777 "${dest}"
  gunzip -c "${backup}" > "${dest}/db.sqlite3"
  chmod 0666 "${dest}/db.sqlite3"
  # Without --config the CLI fails on the missing /config/configuration.yml before it checks any key.
  printf 'storage:\n  local:\n    path: /data/db.sqlite3\n' > "${dest}/config.yml"
  chmod 0644 "${dest}/config.yml"
  detail="$(basename "${backup}"), $(age_hours "${backup}") h old"
  if sqlite_out="$(sqlite_ok "${dest}/db.sqlite3")"; then
    pass authelia "${detail}, ${sqlite_out}"
  else
    fail authelia "${detail}, not ok: ${sqlite_out}"
  fi
  if [ -z "${AUTHELIA_STORAGE_ENCRYPTION_KEY}" ]; then
    skip authelia-key "no key given"
  elif docker run --rm --network none -e AUTHELIA_STORAGE_ENCRYPTION_KEY \
    -v "${dest}:/data" "${authelia_image}" \
    authelia storage encryption check --config /data/config.yml 2>&1 | grep SUCCESS >/dev/null; then
    pass authelia-key "current storage key decrypts the backup"
  else
    fail authelia-key "current storage key doesn't decrypt the backup (made before a key rotation?)"
  fi
fi

# Paperless: every exported original exists and matches the checksum in the manifest (SHA-256 now, MD5 before).
if paperless_out="$(python3 - "${red_hdd}/paperless-export" <<'EOF'
import hashlib, json, os, sys
root = sys.argv[1]
manifest = json.load(open(os.path.join(root, "manifest.json")))
docs = [m for m in manifest if m.get("model") == "documents.document"]
missing = mismatched = 0
for doc in docs:
    path = os.path.join(root, doc["__exported_file_name__"])
    if not os.path.isfile(path):
        missing += 1
        continue
    expected = doc["fields"]["checksum"]
    digest = hashlib.sha256() if len(expected) == 64 else hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        mismatched += 1
print(f"{len(docs)} documents, {missing} missing, {mismatched} checksum mismatches")
sys.exit(0 if docs and not missing and not mismatched else 1)
EOF
)"; then
  pass paperless "manifest.json $(age_hours "${red_hdd}/paperless-export/manifest.json") h old, ${paperless_out}"
else
  fail paperless "${paperless_out:-manifest.json missing or unreadable}"
fi

# Stalwart: the newest tarball extracts and holds RocksDB's CURRENT file and the generated config.
tarball="$(ls -1t "${red_hdd}"/stalwart-backup/stalwart-*.tar.gz 2>/dev/null | head -1)"
if [ -z "${tarball}" ]; then
  fail stalwart "no backup in ${red_hdd}/stalwart-backup"
else
  dest="${scratch}/stalwart"
  mkdir -p "${dest}"
  detail="$(basename "${tarball}"), $(age_hours "${tarball}") h old"
  if ! tar -xzf "${tarball}" -C "${dest}"; then
    fail stalwart "${detail}, doesn't extract"
  elif [ -s "${dest}/stalwart/data/CURRENT" ] && [ -s "${dest}/stalwart/etc/config.json" ]; then
    pass stalwart "${detail}, $(find "${dest}" -type f | wc -l) files, RocksDB CURRENT and config present"
  else
    fail stalwart "${detail}, extracts but data/CURRENT or etc/config.json is missing"
  fi
fi

# Nextcloud AIO Borg: the passphrase opens the repo, and one small file comes back out of the newest archive.
if [ -z "${BORG_PASSPHRASE}" ]; then
  skip borg "no passphrase given"
else
  mkdir -p "${scratch}/borg"
  borg() {
    docker run --rm --network none -e BORG_PASSPHRASE -e BORG_BASE_DIR=/tmp/borg \
      -e BORG_RELOCATED_REPO_ACCESS_IS_OK=yes \
      -v "${red_hdd}/borg:/repo:ro" -v "${scratch}/borg:/out" -w /out \
      --entrypoint borg "${borg_image}" --bypass-lock "$@"
  }
  if ! archive="$(borg list --short --last 1 /repo 2>"${scratch}/borg.err")"; then
    fail borg "can't open the repository: $(tail -1 "${scratch}/borg.err")"
  else
    path="$(borg list --format '{path}{NL}' "/repo::${archive}" 2>/dev/null | grep -m1 '/version\.php$')"
    if [ -n "${path}" ] && borg extract "/repo::${archive}" "${path}" >/dev/null 2>&1 \
      && grep -q OC_Version "${scratch}/borg/${path}"; then
      pass borg "passphrase opens the repo; newest archive ${archive}; extracted ${path##*/} intact"
    else
      fail borg "opened the repo (newest archive ${archive}) but couldn't extract version.php"
    fi
  fi
fi

echo
if [ "${failures}" -eq 0 ]; then
  echo "All checks passed."
else
  echo "${failures} check(s) failed."
fi
exit "$((failures > 0))"
