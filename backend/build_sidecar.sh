#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${ROOT_DIR}/manager-app/src-tauri/binaries"

mkdir -p "${DIST_DIR}"

echo "==> Building Python FastAPI Sidecar Binary..."
cd "${ROOT_DIR}"

"${ROOT_DIR}/automation/venv/bin/pyinstaller" \
  --name backend_server \
  --onefile \
  --clean \
  --noconfirm \
  --distpath "${DIST_DIR}" \
  --workpath "${ROOT_DIR}/build/pyinstaller_work" \
  --specpath "${ROOT_DIR}/build" \
  --paths "${ROOT_DIR}" \
  --exclude-module torch \
  --exclude-module torchvision \
  --exclude-module scipy \
  --exclude-module pytest \
  --add-data "${ROOT_DIR}/automation/brains:automation/brains" \
  --add-data "${ROOT_DIR}/automation/templates:automation/templates" \
  backend/main.py

echo "==> Sidecar binary built at: ${DIST_DIR}/backend_server"
