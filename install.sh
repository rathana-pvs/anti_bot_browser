#!/usr/bin/env bash
set -Eeuo pipefail

MODE="install"
case "${1:-}" in
  ""|--install) MODE="install" ;;
  --check) MODE="check" ;;
  --repair) MODE="repair" ;;
  -h|--help)
    echo "Usage: bash install.sh [--check|--install|--repair] [--non-interactive] [--desktop]"
    exit 0
    ;;
  *)
    echo "Unknown option: $1"
    echo "Usage: bash install.sh [--check|--install|--repair] [--non-interactive] [--desktop]"
    exit 2
    ;;
esac
if [ "$#" -gt 0 ]; then shift; fi
NON_INTERACTIVE=false
DESKTOP_SETUP=false
SKIP_SYSTEM_SETUP=false
for option in "$@"; do
  case "${option}" in
    --non-interactive) NON_INTERACTIVE=true ;;
    --desktop) DESKTOP_SETUP=true ;;
    --skip-system) SKIP_SYSTEM_SETUP=true ;;
    *) echo "Unknown option: ${option}"; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}"
LOG_FILE="${ROOT_DIR}/data/install.log"
IS_WSL=false
RELOGIN_REQUIRED=false

if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  IS_WSL=true
fi

if [ "${MODE}" != "check" ]; then
  mkdir -p "${ROOT_DIR}/data"
  touch "${LOG_FILE}"
  exec > >(tee -a "${LOG_FILE}") 2>&1
fi

on_error() {
  local line="$1"
  local code="$2"
  echo
  echo "Installation stopped at line ${line} (exit ${code})."
  if [ "${MODE}" != "check" ]; then
    echo "Review ${LOG_FILE}, fix the reported issue, then run: bash install.sh --repair"
  fi
}
trap 'on_error ${LINENO} $?' ERR

version_at_least() {
  local current="$1"
  local required="$2"
  [ "$(printf '%s\n%s\n' "${required}" "${current}" | sort -V | head -n1)" = "${required}" ]
}

command_version() {
  case "$1" in
    node) node --version 2>/dev/null | sed 's/^v//' ;;
    python3) python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null ;;
    *) "$1" --version 2>/dev/null | head -n1 ;;
  esac
}

print_header() {
  echo "=================================================="
  echo " Automat FB Beta Environment ${1}"
  echo " Mode: ${MODE}"
  echo " Root: ${ROOT_DIR}"
  echo "=================================================="
}

load_os_release() {
  if [ ! -f /etc/os-release ]; then
    echo "ERROR: /etc/os-release was not found. Ubuntu or Debian is required."
    return 1
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ;;
    *)
      echo "ERROR: Unsupported Linux distribution '${ID:-unknown}'. Use Ubuntu or Debian."
      return 1
      ;;
  esac
}

docker_accessible() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

run_checks() {
  local failures=0
  local warnings=0
  local disk_gb
  print_header "Preflight"
  load_os_release || failures=$((failures + 1))
  if [ "${IS_WSL}" = true ]; then
    echo "Environment: ${PRETTY_NAME:-Linux} / WSL2"
  else
    echo "Environment: ${PRETTY_NAME:-Linux}"
  fi

  if command -v python3 >/dev/null 2>&1; then
    local py_version
    py_version="$(command_version python3)"
    if version_at_least "${py_version}" "3.10"; then
      echo "OK: Python ${py_version}"
    else
      echo "ERROR: Python ${py_version} is older than 3.10"
      failures=$((failures + 1))
    fi
  else
    echo "ERROR: Python 3 is missing"
    failures=$((failures + 1))
  fi

  if [ "${DESKTOP_SETUP}" = true ]; then
    echo "OK: Desktop interface is bundled"
  elif command -v node >/dev/null 2>&1; then
    local node_version
    node_version="$(command_version node)"
    if version_at_least "${node_version}" "18.0.0"; then
      echo "OK: Node.js ${node_version}"
    else
      echo "ERROR: Node.js ${node_version} is older than 18"
      failures=$((failures + 1))
    fi
  else
    echo "ERROR: Node.js is missing"
    failures=$((failures + 1))
  fi

  local required_tools=(jq zip unzip rsync curl)
  if [ "${DESKTOP_SETUP}" = false ]; then required_tools+=(npm); fi
  for tool in "${required_tools[@]}"; do
    if command -v "${tool}" >/dev/null 2>&1; then
      echo "OK: ${tool}"
    else
      echo "ERROR: ${tool} is missing"
      failures=$((failures + 1))
    fi
  done

  if docker_accessible; then
    echo "OK: Docker daemon is available"
  elif [ "${IS_WSL}" = true ]; then
    echo "ERROR: Docker is unavailable inside WSL2. Start Docker Desktop and enable WSL integration."
    failures=$((failures + 1))
  elif command -v docker >/dev/null 2>&1; then
    echo "ERROR: Docker is installed, but the daemon or current-user permission is unavailable"
    failures=$((failures + 1))
  else
    echo "ERROR: Docker is missing"
    failures=$((failures + 1))
  fi

  if docker_accessible && docker image inspect isolated-chrome:latest >/dev/null 2>&1; then
    echo "OK: isolated-chrome:latest image exists"
  else
    echo "INFO: Browser image needs to be built"
    warnings=$((warnings + 1))
  fi

  if [ -x "${ROOT_DIR}/automation/venv/bin/python" ]; then
    echo "OK: Python virtual environment exists"
  else
    echo "INFO: Python virtual environment needs installation"
    warnings=$((warnings + 1))
  fi
  if [ -d "${ROOT_DIR}/manager-app/node_modules" ]; then
    echo "OK: Manager Node dependencies exist"
  else
    echo "INFO: Manager Node dependencies need installation"
    warnings=$((warnings + 1))
  fi

  disk_gb="$(df -Pk "${ROOT_DIR}" | awk 'NR==2 {printf "%d", $4/1024/1024}')"
  if [ "${disk_gb}" -ge 5 ]; then
    echo "OK: ${disk_gb} GiB free disk space"
  else
    echo "ERROR: Only ${disk_gb} GiB is free; at least 5 GiB is required"
    failures=$((failures + 1))
  fi

  if command -v nvidia-smi >/dev/null 2>&1; then
    echo "INFO: NVIDIA GPU detected; OCR will use it only when the installed runtime supports CUDA"
  else
    echo "INFO: No NVIDIA runtime detected; CPU OCR will be used"
  fi

  if command -v ss >/dev/null 2>&1; then
    for port in 3001 5173; do
      if ss -ltn "sport = :${port}" 2>/dev/null | tail -n +2 | grep -q .; then
        echo "WARN: Port ${port} is currently in use"
        warnings=$((warnings + 1))
      else
        echo "OK: Port ${port} is available"
      fi
    done
  fi

  echo "Preflight result: ${failures} blocker(s), ${warnings} warning(s)"
  [ "${failures}" -eq 0 ]
}

sudo_run() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif [ "${NON_INTERACTIVE}" = true ] && command -v pkexec >/dev/null 2>&1; then
    pkexec "$@"
  else
    sudo "$@"
  fi
}

confirm_system_changes() {
  if [ "$(id -u)" -eq 0 ]; then return 0; fi
  if [ "${NON_INTERACTIVE}" = true ]; then
    echo "Administrator approval is required to install system packages."
    if ! command -v pkexec >/dev/null 2>&1; then
      echo "ERROR: Graphical administrator approval is unavailable (pkexec is missing)."
      exit 4
    fi
    return 0
  fi
  echo
  echo "The installer may use sudo to install missing Ubuntu/Debian packages."
  read -r -p "Continue? [y/N] " answer
  case "${answer}" in
    y|Y|yes|YES) ;;
    *) echo "Installation cancelled."; exit 1 ;;
  esac
  sudo -v
}

install_node_20() {
  echo "Installing Node.js 20 from the signed NodeSource APT repository..."
  sudo_run install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    | sudo_run gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
    | sudo_run tee /etc/apt/sources.list.d/nodesource.list >/dev/null
  sudo_run apt-get update
  sudo_run apt-get install -y nodejs
}

install_system_dependencies() {
  confirm_system_changes
  sudo_run apt-get update
  sudo_run apt-get install -y \
    ca-certificates curl gnupg jq zip unzip rsync python3 python3-venv python3-pip \
    build-essential iproute2

  if [ "${DESKTOP_SETUP}" = false ]; then
    local node_ok=false
    if command -v node >/dev/null 2>&1; then
      local installed_node
      installed_node="$(command_version node)"
      if version_at_least "${installed_node}" "18.0.0"; then node_ok=true; fi
    fi
    if [ "${node_ok}" = false ]; then install_node_20; fi
  fi

  if ! command -v docker >/dev/null 2>&1; then
    if [ "${IS_WSL}" = true ]; then
      echo "ERROR: Docker Desktop is required on Windows. Install/start it and enable WSL integration, then run --repair."
      exit 3
    fi
    echo "Installing Docker Engine from the distribution repository..."
    sudo_run apt-get install -y docker.io
    if command -v systemctl >/dev/null 2>&1; then
      sudo_run systemctl enable --now docker
    fi
  fi

  if [ "$(id -u)" -ne 0 ] && command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
    if sudo docker info >/dev/null 2>&1; then
      sudo_run usermod -aG docker "${USER}"
      RELOGIN_REQUIRED=true
    fi
  fi
  if [ "${RELOGIN_REQUIRED}" = true ]; then
    echo "Docker permission was added for ${USER}. Sign out and back in, then run: bash install.sh --repair"
    exit 10
  fi
  if ! docker_accessible; then
    echo "ERROR: Docker daemon is still unavailable. Start Docker, then run: bash install.sh --repair"
    exit 3
  fi
}

install_application() {
  echo
  echo "Setting up Python automation environment..."
  python3 -m venv "${ROOT_DIR}/automation/venv"
  "${ROOT_DIR}/automation/venv/bin/python" -m pip install --upgrade pip wheel
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "No NVIDIA runtime detected; installing the smaller CPU-only Torch runtime..."
    "${ROOT_DIR}/automation/venv/bin/python" -m pip install \
      "torch==2.14.0+cpu" "torchvision==0.29.0+cpu" \
      --index-url https://download.pytorch.org/whl/cpu
  fi
  "${ROOT_DIR}/automation/venv/bin/python" -m pip install -r "${ROOT_DIR}/automation/requirements.txt"

  if [ "${DESKTOP_SETUP}" = false ]; then
    echo
    echo "Installing and building manager application..."
    (
      cd "${ROOT_DIR}/manager-app"
      npm ci
      npm test
      npm run build
    )
  else
    echo "Desktop interface is bundled; skipping developer UI dependencies."
  fi

  echo
  echo "Running offline automation tests..."
  (
    cd "${ROOT_DIR}/automation"
    "${ROOT_DIR}/automation/venv/bin/python" -m unittest discover -s tests
  )

  if ! docker image inspect isolated-chrome:latest >/dev/null 2>&1; then
    echo
    echo "Building browser container image. This can take several minutes..."
    bash "${ROOT_DIR}/scripts/build_container.sh"
  else
    echo "Browser container image already exists; skipping rebuild."
  fi

  mkdir -p "${ROOT_DIR}/profiles/shared_media" "${ROOT_DIR}/data" "${ROOT_DIR}/proxies"
  if [ ! -f "${ROOT_DIR}/data/posting_queue.json" ]; then
    printf '{\n  "queue_version": "2.0",\n  "daily_batches": []\n}\n' > "${ROOT_DIR}/data/posting_queue.json"
  fi
  if [ ! -f "${ROOT_DIR}/data/manager_settings.json" ]; then
    printf '{\n  "resource_mode": "auto"\n}\n' > "${ROOT_DIR}/data/manager_settings.json"
  fi
  if [ ! -f "${ROOT_DIR}/proxies/proxy_pool.json" ]; then
    printf '[]\n' > "${ROOT_DIR}/proxies/proxy_pool.json"
  fi
}

if [ "${MODE}" = "check" ]; then
  if run_checks; then
    exit 0
  fi
  exit 1
fi

print_header "Installer"
load_os_release
if [ "${SKIP_SYSTEM_SETUP}" = true ]; then
  echo "System dependencies were prepared by the host setup assistant."
else
  install_system_dependencies
fi
install_application

echo
if run_checks; then
  echo
  echo "READY: Automat FB Beta installation completed successfully."
  if [ "${DESKTOP_SETUP}" = false ]; then
    echo "Start the application with: cd manager-app && npm start"
    echo "Then open: http://localhost:5173"
  fi
else
  echo "Installation completed, but preflight still reports a blocker. Review the messages above."
  exit 1
fi
