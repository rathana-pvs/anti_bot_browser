#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${project_root}/automation/venv/bin/python"
if [[ ! -x "${python_bin}" ]]; then
  python_bin="python3"
fi

cd "${project_root}"
"${python_bin}" -m pytest \
  backend/tests/test_reliability_drills.py \
  backend/tests/test_reliability_service.py \
  backend/tests/test_worker_agent.py \
  backend/tests/test_worker_integration.py \
  backend/tests/test_queue_persistence.py \
  backend/tests/test_container_cleanup.py \
  -q

cd "${project_root}/manager-app"
node --test --test-name-pattern="handshake|reconnect|security failure|late events" test/vncConnection.test.js

