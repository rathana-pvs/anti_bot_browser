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

# Existing desktop builds prefer the sidecar beside their executable. Keep
# those copies current so restarting cannot reload the previous backend.
for desktop_target in debug release; do
  desktop_dir="${ROOT_DIR}/manager-app/src-tauri/target/${desktop_target}"
  if [ -f "${desktop_dir}/isolated-browser-manager" ]; then
    mkdir -p "${desktop_dir}/binaries"
    install -m 755 "${DIST_DIR}/backend_server" "${desktop_dir}/binaries/backend_server.new"
    mv -f "${desktop_dir}/binaries/backend_server.new" "${desktop_dir}/binaries/backend_server"
    echo "==> Updated ${desktop_target} desktop sidecar"
  fi
done
