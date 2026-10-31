#!/usr/bin/env bash
set -Eeuo pipefail

MODE="install"
case "${1:-}" in
  ""|--install) MODE="install" ;;
  --check) MODE="check" ;;
  --repair) MODE="repair" ;;
  # Internal: the system-package step, run once as root through one administrator prompt.
  --system-only) MODE="system" ;;
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
BROWSER_IMAGE_VERSION="3"
# A first install needs room for the Python and OCR runtimes, the browser image and downloads.
FRESH_INSTALL_DISK_GB=25
UPDATE_DISK_GB=5
MIN_RAM_GB=8
MIN_CPU_THREADS=4
LOG_FILE="${ROOT_DIR}/data/install.log"
IS_WSL=false
RELOGIN_REQUIRED=false

if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  IS_WSL=true
fi

if [ "${MODE}" != "check" ] && [ "${MODE}" != "system" ]; then
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
  # Ubuntu-based systems (Linux Mint, Pop!_OS, Zorin...) use the same packages.
  case " ${ID:-} ${ID_LIKE:-} " in
    *" ubuntu "*|*" debian "*) ;;
    *)
      echo "ERROR: Unsupported Linux distribution '${ID:-unknown}'. Use Ubuntu, Debian, or a system based on them."
      return 1
      ;;
  esac
}

nvidia_card_present() {
  local device
  for device in /sys/bus/pci/devices/*; do
    # Vendor 0x10de is NVIDIA; class 0x03xxxx is a display controller.
    if [ "$(cat "${device}/vendor" 2>/dev/null)" = "0x10de" ] \
      && [[ "$(cat "${device}/class" 2>/dev/null)" == 0x03* ]]; then
      return 0
    fi
  done
  return 1
}

internet_reachable() {
  curl -fsS -o /dev/null --max-time 15 https://pypi.org/simple/pip/ \
    && curl -fsSI -o /dev/null --max-time 15 https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
}

docker_accessible() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

nvidia_smi_path() {
  if command -v nvidia-smi >/dev/null 2>&1; then
    command -v nvidia-smi
  elif [ -x /usr/lib/wsl/lib/nvidia-smi ]; then
    printf '%s\n' /usr/lib/wsl/lib/nvidia-smi
  else
    return 1
  fi
}

run_checks() {
  local failures=0
  local warnings=0
  local disk_gb
  local runtime_ready=false
  local image_ready=false
  print_header "Preflight"
  load_os_release || failures=$((failures + 1))
  if [ "${IS_WSL}" = true ]; then
    echo "Environment: ${PRETTY_NAME:-Linux} / WSL2"
  else
    echo "Environment: ${PRETTY_NAME:-Linux}"
  fi

  local arch
  arch="$(uname -m)"
  case "${arch}" in
    x86_64|amd64) echo "OK: Processor type ${arch}" ;;
    *)
      echo "ERROR: This app needs an Intel or AMD (x86_64) computer; this one is ${arch}. Chrome for Linux and the proxy tunnel are only made for x86_64."
      failures=$((failures + 1))
      ;;
  esac

  local ram_gb threads
  ram_gb="$(awk '/^MemTotal:/ {printf "%.0f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)"
  threads="$(nproc 2>/dev/null || echo 0)"
  if [ "${ram_gb}" -ge "${MIN_RAM_GB}" ] && [ "${threads}" -ge "${MIN_CPU_THREADS}" ]; then
    echo "OK: ${ram_gb} GB memory, ${threads} CPU threads"
  else
    echo "WARN: ${ram_gb} GB memory and ${threads} CPU threads. At least ${MIN_RAM_GB} GB and ${MIN_CPU_THREADS} threads are recommended (16 GB or more for several accounts at once); fewer accounts can run at the same time."
    warnings=$((warnings + 1))
  fi

  if [ -c /dev/net/tun ]; then
    echo "OK: Proxy tunnel device is available"
  else
    echo "ERROR: The proxy tunnel device /dev/net/tun is missing, so accounts with a proxy cannot start. Installation and repair load it (sudo modprobe tun)."
    failures=$((failures + 1))
  fi

  if command -v python3 >/dev/null 2>&1; then
    local py_version
    py_version="$(command_version python3)"
    if version_at_least "${py_version}" "3.10"; then
      echo "OK: System Python ${py_version}"
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

  local required_tools=(jq zip unzip rsync curl ffmpeg ffprobe)
  if [ "${DESKTOP_SETUP}" = false ]; then required_tools+=(npm); fi
  for tool in "${required_tools[@]}"; do
    if command -v "${tool}" >/dev/null 2>&1; then
      echo "OK: ${tool}"
    else
      echo "ERROR: ${tool} is missing"
      failures=$((failures + 1))
    fi
  done


  if [ -c /dev/dxg ]; then
    echo "OK: WSL DirectX GPU bridge is available"
  elif [ -d /dev/dri ]; then
    echo "OK: Linux DRM GPU device is available"
  else
    echo "WARN: No browser GPU device is visible; profiles will use software rendering"
    warnings=$((warnings + 1))
  fi

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

  if docker_accessible \
    && [ "$(docker image inspect --format '{{index .Config.Labels "org.automat-fb.browser-image-version"}}' isolated-chrome:latest 2>/dev/null || true)" = "$BROWSER_IMAGE_VERSION" ]; then
    image_ready=true
    echo "OK: GPU-capable browser image v${BROWSER_IMAGE_VERSION} is installed"
  else
    echo "INFO: GPU-capable browser image needs to be built or upgraded"
    warnings=$((warnings + 1))
  fi

  if [ -x "${ROOT_DIR}/automation/venv/bin/python" ] \
    && "${ROOT_DIR}/automation/venv/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 12); import fastapi, uvicorn' >/dev/null 2>&1; then
    runtime_ready=true
    echo "OK: Python 3.12 application runtime is ready"
  elif [ -x "${ROOT_DIR}/automation/venv/bin/python" ]; then
    echo "INFO: Python 3.12 application environment or its packages need installation"
    warnings=$((warnings + 1))
  else
    echo "INFO: Python virtual environment needs installation"
    warnings=$((warnings + 1))
  fi
  # The desktop app bundles its interface and picks its own local port.
  if [ "${DESKTOP_SETUP}" = false ]; then
    if [ -d "${ROOT_DIR}/manager-app/node_modules" ]; then
      echo "OK: Manager Node dependencies exist"
    else
      echo "INFO: Manager Node dependencies need installation"
      warnings=$((warnings + 1))
    fi
  fi

  local fresh=true
  if [ "${runtime_ready}" = true ] && [ "${image_ready}" = true ]; then fresh=false; fi
  local disk_needed="${UPDATE_DISK_GB}"
  if [ "${fresh}" = true ]; then disk_needed="${FRESH_INSTALL_DISK_GB}"; fi
  disk_gb="$(df -Pk "${ROOT_DIR}" | awk 'NR==2 {printf "%d", $4/1024/1024}')"
  if [ "${disk_gb}" -ge "${disk_needed}" ]; then
    echo "OK: ${disk_gb} GiB free disk space"
  else
    echo "ERROR: Only ${disk_gb} GiB is free; ${disk_needed} GiB is needed$([ "${fresh}" = true ] && echo " for a first install (runtimes, OCR models and the browser image)"). Each account then uses about 0.3-1.5 GiB more."
    failures=$((failures + 1))
  fi

  if internet_reachable; then
    echo "OK: Internet downloads are reachable"
  elif [ "${fresh}" = true ]; then
    echo "ERROR: Cannot reach pypi.org or dl.google.com. A first install downloads several GB; check the internet connection or firewall."
    failures=$((failures + 1))
  else
    echo "WARN: Cannot reach pypi.org or dl.google.com; repair and updates need the internet"
    warnings=$((warnings + 1))
  fi

  if nvidia_smi_path >/dev/null 2>&1; then
    echo "INFO: NVIDIA GPU detected; setup will install and verify the CUDA OCR runtime"
  elif nvidia_card_present; then
    echo "WARN: An NVIDIA graphics card was found, but its driver is not installed, so OCR will use the CPU. Install the NVIDIA driver (Ubuntu: Software & Updates > Additional Drivers), restart, then run repair."
    warnings=$((warnings + 1))
  else
    echo "INFO: No NVIDIA GPU detected; CPU OCR will be used"
  fi

  if [ "${DESKTOP_SETUP}" = false ] && command -v ss >/dev/null 2>&1; then
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

apt_locks_busy() {
  local lock
  local apt_locks=(
    /var/lib/apt/lists/lock
    /var/lib/dpkg/lock-frontend
    /var/lib/dpkg/lock
    /var/cache/apt/archives/lock
  )
  if command -v lslocks >/dev/null 2>&1; then
    while IFS= read -r lock; do
      for candidate in "${apt_locks[@]}"; do
        if [ "${lock}" = "${candidate}" ]; then return 0; fi
      done
    done < <(lslocks --noheadings --notruncate --output PATH 2>/dev/null || true)
    return 1
  fi
  if command -v fuser >/dev/null 2>&1; then
    for lock in "${apt_locks[@]}"; do
      if fuser "${lock}" >/dev/null 2>&1; then return 0; fi
    done
  fi
  return 1
}

wait_for_apt_locks() {
  local waited=0
  while apt_locks_busy; do
    if [ "${waited}" -eq 0 ]; then
      echo "Ubuntu's package manager is busy. Waiting for it to finish..."
    fi
    if [ "${waited}" -ge 300 ]; then
      echo "ERROR: Ubuntu's package manager remained busy for 5 minutes. Close Software Updater and try repair again."
      return 1
    fi
    sleep 5
    waited=$((waited + 5))
  done
  if [ "${waited}" -gt 0 ]; then
    echo "Ubuntu's package manager is available; continuing setup."
  fi
}

apt_get() {
  wait_for_apt_locks
  sudo_run apt-get -o DPkg::Lock::Timeout=300 "$@"
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
  apt_get update
  apt_get install -y nodejs
}

install_system_dependencies() {
  confirm_system_changes
  apt_get update
  apt_get install -y \
    ca-certificates curl gnupg jq zip unzip rsync python3 python3-venv python3-pip \
    build-essential iproute2 mesa-utils libgl1-mesa-dri mesa-vulkan-drivers vulkan-tools \
    tesseract-ocr tesseract-ocr-eng ffmpeg libraqm0

  if [ "${DESKTOP_SETUP}" = false ]; then
    local node_ok=false
    if command -v node >/dev/null 2>&1; then
      local installed_node
      installed_node="$(command_version node)"
      if version_at_least "${installed_node}" "18.0.0"; then node_ok=true; fi
    fi
    if [ "${node_ok}" = false ]; then install_node_20; fi
  fi

  if [ ! -c /dev/net/tun ]; then
    echo "Loading the proxy tunnel module..."
    sudo_run modprobe tun || true
    echo tun | sudo_run tee /etc/modules-load.d/automat-fb-tun.conf >/dev/null
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "Installing Docker Engine from the distribution repository..."
    apt_get install -y docker.io
    if command -v systemctl >/dev/null 2>&1; then
      sudo_run systemctl enable --now docker
    else
      sudo_run service docker start
    fi
  fi

  # Run as root through the single administrator prompt: give the real user Docker access.
  if [ "${MODE}" = "system" ]; then
    local target="${AUTOMAT_TARGET_USER:-}"
    if [ -n "${target}" ] && [ "${target}" != "root" ] && ! id -nG "${target}" | tr ' ' '\n' | grep -qx docker; then
      usermod -aG docker "${target}"
      echo "Docker permission was added for ${target}."
    fi
    return 0
  fi

  if [ "$(id -u)" -ne 0 ] && command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
    if sudo_run docker info >/dev/null 2>&1; then
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

# The desktop app runs setup without a terminal: one administrator prompt covers
# every system change instead of one prompt per command.
install_system_dependencies_elevated() {
  if ! command -v pkexec >/dev/null 2>&1; then
    echo "ERROR: Graphical administrator approval is unavailable (pkexec is missing)."
    exit 4
  fi
  echo "Administrator approval is needed once to install system packages..."
  local status=0
  pkexec /usr/bin/env AUTOMAT_TARGET_USER="$(id -un)" "$(command -v bash)" "${ROOT_DIR}/install.sh" --system-only || status=$?
  case "${status}" in
    0) ;;
    126|127)
      echo "ERROR: Administrator approval was cancelled or refused. Run setup again and approve the request."
      exit 4
      ;;
    *) echo "ERROR: System package setup failed (exit ${status})."; exit "${status}" ;;
  esac
  if ! docker_accessible; then
    if id -nG "$(id -un)" | tr ' ' '\n' | grep -qx docker || getent group docker | grep -qw "$(id -un)"; then
      echo "Docker permission was added for $(id -un). Sign out and back in, then run: bash install.sh --repair"
      exit 10
    fi
    echo "ERROR: Docker daemon is still unavailable. Start Docker, then run: bash install.sh --repair"
    exit 3
  fi
}

print_ocr_summary() {
  # Which device OCR really uses, read from what setup verified.
  python3 - "${ROOT_DIR}/data" "$(nvidia_smi_path 2>/dev/null || true)" "$(nvidia_card_present && echo yes || echo no)" <<'PY' || true
import json, subprocess, sys
from pathlib import Path
data, smi, card = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "yes"
def load(name):
    try:
        return json.loads((data / name).read_text())
    except (OSError, ValueError):
        return {}
paddle, torch = load("paddle_runtime.json"), load("torch_runtime.json")
gpu = (paddle.get("runtime") or {}).get("gpu_name") or torch.get("gpu_name")
driver = ""
if smi:
    try:
        driver = subprocess.run([smi, "--query-gpu=driver_version", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
    except Exception:
        pass
if paddle.get("device") == "cuda":
    print(f"OCR: GPU ({gpu or 'NVIDIA'})")
elif paddle.get("cuda_attempted"):
    print(f"OCR: CPU. The NVIDIA GPU{f' (driver {driver})' if driver else ''} could not run the GPU OCR build, "
          "usually because the driver is too old. Update the NVIDIA driver, then run repair.")
elif card:
    print("OCR: CPU. An NVIDIA card is installed but its driver is not; install the driver, then run repair to use the GPU.")
else:
    print("OCR: CPU (no NVIDIA GPU).")
if torch.get("device") and torch.get("device") != paddle.get("device"):
    print(f"Fallback OCR (EasyOCR): {torch['device'].upper()}")
PY
}

install_application() {
  echo
  echo "Setting up Python automation environment..."
  python3 "${ROOT_DIR}/automation/bootstrap_python_runtime.py" "${ROOT_DIR}"
  "${ROOT_DIR}/automation/venv/bin/python" -m pip install --upgrade pip 'setuptools<81' wheel
  "${ROOT_DIR}/automation/venv/bin/python" "${ROOT_DIR}/automation/bootstrap_torch_runtime.py"
  "${ROOT_DIR}/automation/venv/bin/python" -m pip uninstall -y opencv-python
  "${ROOT_DIR}/automation/venv/bin/python" -m pip install -r "${ROOT_DIR}/automation/requirements.txt"
  "${ROOT_DIR}/automation/venv/bin/python" -m pip install -r "${ROOT_DIR}/backend/requirements.txt"
  "${ROOT_DIR}/automation/venv/bin/python" "${ROOT_DIR}/automation/bootstrap_paddle_runtime.py"
  # EasyOCR/headless and PaddleX/contrib share cv2; restore the aligned contrib build last.
  "${ROOT_DIR}/automation/venv/bin/python" -m pip install --force-reinstall --no-deps opencv-contrib-python==4.10.0.84
  "${ROOT_DIR}/automation/venv/bin/python" "${ROOT_DIR}/automation/bootstrap_ocr_models.py"

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

  local installed_browser_image_version
  installed_browser_image_version="$(docker image inspect \
    --format '{{index .Config.Labels "org.automat-fb.browser-image-version"}}' \
    isolated-chrome:latest 2>/dev/null || true)"
  if [ "$installed_browser_image_version" != "$BROWSER_IMAGE_VERSION" ]; then
    echo
    echo "Building GPU-capable browser container image. This can take several minutes..."
    bash "${ROOT_DIR}/scripts/build_container.sh"
  else
    echo "Browser container image v${BROWSER_IMAGE_VERSION} is current; skipping rebuild."
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

if [ "${MODE}" = "system" ]; then
  if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: --system-only must run as root."
    exit 2
  fi
  load_os_release
  install_system_dependencies
  exit 0
fi

print_header "Installer"
load_os_release
# Stop in the first minute for problems that would otherwise fail an hour in.
case "$(uname -m)" in
  x86_64|amd64) ;;
  *) echo "ERROR: This app needs an Intel or AMD (x86_64) computer; this one is $(uname -m)."; exit 5 ;;
esac
if [ "${SKIP_SYSTEM_SETUP}" = true ]; then
  echo "System dependencies were prepared by the host setup assistant."
elif [ "${NON_INTERACTIVE}" = true ] && [ "$(id -u)" -ne 0 ]; then
  install_system_dependencies_elevated
else
  install_system_dependencies
fi
install_application

echo
if run_checks; then
  install -m 0644 "${ROOT_DIR}/VERSION" "${ROOT_DIR}/data/runtime-installed-version.new"
  mv -f "${ROOT_DIR}/data/runtime-installed-version.new" "${ROOT_DIR}/data/runtime-installed-version"
  echo
  print_ocr_summary
  echo "READY: Automat FB Beta installation completed successfully."
  if [ "${DESKTOP_SETUP}" = false ]; then
    echo "Start the application with: cd manager-app && npm start"
    echo "Then open: http://localhost:5173"
  fi
else
  echo "Installation completed, but preflight still reports a blocker. Review the messages above."
  exit 1
fi
