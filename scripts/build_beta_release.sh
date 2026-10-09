#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VERSION="${1:-$(tr -d '[:space:]' < "${ROOT_DIR}/VERSION")}"
SAFE_VERSION="${VERSION//[^a-zA-Z0-9._-]/_}"
RELEASES_DIR="${ROOT_DIR}/releases"
PACKAGE_NAME="automat_fb-${SAFE_VERSION}-linux"
STAGING_PARENT=""

mkdir -p "${RELEASES_DIR}"
STAGING_PARENT="$(mktemp -d "${RELEASES_DIR}/.staging.XXXXXX")"
PACKAGE_DIR="${STAGING_PARENT}/${PACKAGE_NAME}"

cleanup() {
  if [ -n "${STAGING_PARENT}" ] && [[ "${STAGING_PARENT}" == "${RELEASES_DIR}/.staging."* ]]; then
    rm -rf -- "${STAGING_PARENT}"
  fi
}
trap cleanup EXIT

mkdir -p "${PACKAGE_DIR}"

rsync -a \
  --exclude 'venv/' \
  --exclude '.venv' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'reports/' \
  --exclude 'test_fb_typing.py' \
  --exclude 'test_human_input.py' \
  --exclude 'test_human_typing.py' \
  --exclude 'test_vision.py' \
  --exclude 'brains/staging/' \
  --exclude 'node_modules/' \
  --exclude 'dist/' \
  --exclude 'target/' \
  --exclude 'tsconfig.tsbuildinfo' \
  --exclude '.env' \
  --exclude '.env.*' \
  "${ROOT_DIR}/automation" \
  "${ROOT_DIR}/backend" \
  "${ROOT_DIR}/container" \
  "${ROOT_DIR}/manager-app" \
  "${ROOT_DIR}/scripts" \
  "${ROOT_DIR}/README.md" \
  "${ROOT_DIR}/BETA_README.md" \
  "${ROOT_DIR}/VERSION" \
  "${ROOT_DIR}/install.sh" \
  "${ROOT_DIR}/install-windows.ps1" \
  "${ROOT_DIR}/.gitignore" \
  "${PACKAGE_DIR}/"

mkdir -p "${PACKAGE_DIR}/profiles/shared_media" "${PACKAGE_DIR}/proxies" "${PACKAGE_DIR}/data"
cp "${ROOT_DIR}/profiles/config.example.json" "${PACKAGE_DIR}/profiles/config.example.json"
cp "${ROOT_DIR}/proxies/proxy_pool.example.json" "${PACKAGE_DIR}/proxies/proxy_pool.example.json"
sed -i \
  -e 's/"username": "[^"]*"/"username": ""/' \
  -e 's/"password": "[^"]*"/"password": ""/' \
  "${PACKAGE_DIR}/proxies/proxy_pool.example.json"

printf '{\n  "queue_version": "2.0",\n  "daily_batches": []\n}\n' > "${PACKAGE_DIR}/data/posting_queue.json"
printf '{\n  "resource_mode": "auto"\n}\n' > "${PACKAGE_DIR}/data/manager_settings.json"
printf '[]\n' > "${PACKAGE_DIR}/proxies/proxy_pool.json"
printf 'Beta runtime media directory. Files added here remain local.\n' > "${PACKAGE_DIR}/profiles/shared_media/README.txt"

node "${ROOT_DIR}/scripts/scan_release_for_secrets.mjs" "${ROOT_DIR}" "${PACKAGE_DIR}"

ARCHIVE_PATH="${RELEASES_DIR}/${PACKAGE_NAME}.zip"
CHECKSUM_PATH="${ARCHIVE_PATH}.sha256"
rm -f -- "${ARCHIVE_PATH}" "${CHECKSUM_PATH}"
(
  cd "${STAGING_PARENT}"
  zip -q -r "${ARCHIVE_PATH}" "${PACKAGE_NAME}"
)
(
  cd "${RELEASES_DIR}"
  sha256sum "$(basename "${ARCHIVE_PATH}")" > "$(basename "${CHECKSUM_PATH}")"
)

echo "Beta release created: ${ARCHIVE_PATH}"
echo "Checksum: ${CHECKSUM_PATH}"
