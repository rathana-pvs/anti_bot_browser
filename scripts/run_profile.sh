#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PROFILE_ID="${1:-profile_001}"
ACTION="${2:-start}"

CONFIG_FILE="${ROOT_DIR}/profiles/${PROFILE_ID}/config.json"
DATA_DIR="${ROOT_DIR}/profiles/${PROFILE_ID}/chrome_data"
CONTAINER_NAME="isolated_${PROFILE_ID}"
IMAGE_NAME="isolated-chrome:latest"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Configuration file not found: $CONFIG_FILE"
    exit 1
fi

# Parse config via jq
VNC_PORT=$(jq -r '.container.vnc_port // 5901' "$CONFIG_FILE")
WS_PORT=$(jq -r '.container.ws_port // empty' "$CONFIG_FILE")
if [ -z "$WS_PORT" ]; then
    WS_PORT=$(( VNC_PORT + 180 ))
fi
SCREEN_RES=$(jq -r '.requested_environment.screen_resolution // .fingerprint.screen_resolution // "1920x1080"' "$CONFIG_FILE")
COLOR_DEPTH=$(jq -r '.fingerprint.color_depth // 24' "$CONFIG_FILE")
TIMEZONE=$(jq -r '.requested_environment.timezone // .fingerprint.timezone // empty' "$CONFIG_FILE")
if [ -z "$TIMEZONE" ]; then
    TIMEZONE=$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo "UTC")
fi
LANG_VAL=$(jq -r '.requested_environment.language // .fingerprint.language // "en-US"' "$CONFIG_FILE")
RENDERING_MODE=$(jq -r '.requested_environment.rendering_mode // "host_gpu"' "$CONFIG_FILE")
CPU_LIMIT=$(jq -r '.resources.cpu_limit // 4' "$CONFIG_FILE")
MEMORY_LIMIT_MB=$(jq -r '.resources.memory_mb // 4096' "$CONFIG_FILE")

PROXY_HOST=$(jq -r '.network.proxy_host // ""' "$CONFIG_FILE")
PROXY_PORT=$(jq -r '.network.proxy_port // ""' "$CONFIG_FILE")
PROXY_USER=$(jq -r '.network.proxy_user // ""' "$CONFIG_FILE")
PROXY_PASS=$(jq -r '.network.proxy_pass // ""' "$CONFIG_FILE")

case "$ACTION" in
    start)
        echo "=== Launching Profile: ${PROFILE_ID} ==="
        echo "Container:   ${CONTAINER_NAME}"
        echo "Legacy port: ${VNC_PORT} (raw VNC remains container-internal)"
        echo "noVNC:       ${WS_PORT} -> 6080"
        echo "Resolution:  ${SCREEN_RES}x${COLOR_DEPTH}"
        echo "CPU Limit:   ${CPU_LIMIT} vCPU"
        echo "Memory Limit:${MEMORY_LIMIT_MB} MiB"
        echo "Data Mount:  ${DATA_DIR}"

        # Ensure host profile storage directory exists
        mkdir -p "$DATA_DIR"

        # Check if already running or stopped
        if [ "$(docker ps -q -f name=^/${CONTAINER_NAME}$)" ]; then
            echo "Container ${CONTAINER_NAME} is already running."
            exit 0
        fi

        if [ "$(docker ps -aq -f name=^/${CONTAINER_NAME}$)" ]; then
            echo "Removing existing stopped container ${CONTAINER_NAME}..."
            docker rm "$CONTAINER_NAME" >/dev/null
        fi

        ENV_ARGS=(
            -e "PROFILE_ID=${PROFILE_ID}"
            -e "DISPLAY=:99"
            -e "SCREEN_RESOLUTION=${SCREEN_RES}x${COLOR_DEPTH}"
            -e "WINDOW_SIZE=${SCREEN_RES/x/,}"
            -e "RENDERING_MODE=${RENDERING_MODE}"
            -e "TZ=${TIMEZONE}"
            -e "BROWSER_LANG=${LANG_VAL}"
            -e "LANG=en_US.UTF-8"
            -e "LC_ALL=en_US.UTF-8"
        )
        CAP_ARGS=(--cap-add=SYS_ADMIN)
        if [ -n "$PROXY_HOST" ]; then
            ENV_ARGS+=(
                -e "PROXY_HOST=${PROXY_HOST}"
                -e "PROXY_PORT=${PROXY_PORT}"
                -e "PROXY_USER=${PROXY_USER}"
                -e "PROXY_PASS=${PROXY_PASS}"
            )
            if [ -c /dev/net/tun ]; then
                CAP_ARGS+=(--device /dev/net/tun --cap-add=NET_ADMIN)
            fi
        fi

        # Pass the native Linux DRM device, or WSL2's DirectX GPU bridge.
        # Keep startup portable: the entrypoint records a software fallback
        # when neither device is available.
        DEVICE_ARGS=()
        GPU_DEVICE_BACKEND="none"
        if [ "$RENDERING_MODE" = "host_gpu" ] && [ -d /dev/dri ]; then
            DEVICE_ARGS+=(--device /dev/dri:/dev/dri)
            GPU_DEVICE_BACKEND="drm"
        elif [ "$RENDERING_MODE" = "host_gpu" ] \
            && [ -c /dev/dxg ] \
            && [ -d /usr/lib/wsl/lib ]; then
            DEVICE_ARGS+=(--device /dev/dxg:/dev/dxg)
            DEVICE_ARGS+=(-v /usr/lib/wsl:/usr/lib/wsl:ro)
            GPU_DEVICE_BACKEND="wsl_dxg"
        fi
        ENV_ARGS+=(-e "GPU_DEVICE_BACKEND=${GPU_DEVICE_BACKEND}")
        if [ "$RENDERING_MODE" = "host_gpu" ] && [ "$GPU_DEVICE_BACKEND" = "none" ]; then
            echo "WARNING: Host GPU rendering was requested, but no supported GPU device is available; Chrome will use software rendering." >&2
        fi

        # Shared media directory for batch campaigns
        SHARED_MEDIA_DIR="${ROOT_DIR}/profiles/shared_media"
        mkdir -p "$SHARED_MEDIA_DIR"

        # Run container with resource limits, display ports, and network capabilities
        CONTAINER_ID=$(docker run -d \
            --name "$CONTAINER_NAME" \
            -p "127.0.0.1:${WS_PORT}:6080" \
            -v "${DATA_DIR}:/data/profile" \
            -v "${SHARED_MEDIA_DIR}:/data/shared_media:ro" \
            -v "${ROOT_DIR}/container/entrypoint.sh:/entrypoint.sh:ro" \
            "${CAP_ARGS[@]}" \
            "${DEVICE_ARGS[@]}" \
            --memory="${MEMORY_LIMIT_MB}m" \
            --memory-swap="${MEMORY_LIMIT_MB}m" \
            --cpus="${CPU_LIMIT}" \
            --shm-size="1g" \
            "${ENV_ARGS[@]}" \
            "$IMAGE_NAME")

        # Do not report a profile as running until its in-container network
        # preflight has completed. A proxy-mode failure exits before Chrome.
        PREFLIGHT_READY=false
        for _ in $(seq 1 80); do
            if ! docker inspect -f '{{.State.Running}}' "$CONTAINER_ID" 2>/dev/null | grep -q '^true$'; then
                echo "Error: Container stopped before network preflight completed." >&2
                docker logs "$CONTAINER_ID" >&2 || true
                jq '.status = "stopped"' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
                exit 1
            fi
            if docker exec "$CONTAINER_ID" test -f /run/network-preflight.ok 2>/dev/null; then
                PREFLIGHT_READY=true
                break
            fi
            sleep 0.25
        done
        if [ "$PREFLIGHT_READY" != "true" ]; then
            echo "Error: Network preflight did not complete within 20 seconds." >&2
            docker logs "$CONTAINER_ID" >&2 || true
            docker stop "$CONTAINER_ID" >/dev/null 2>&1 || true
            jq '.status = "stopped"' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
            exit 1
        fi

        # Update status in config.json
        jq '.status = "running"' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"

        echo "✅ Profile ${PROFILE_ID} launched successfully!"
        echo "👉 Open noVNC: http://127.0.0.1:${WS_PORT}/vnc.html"
        ;;

    stop)
        echo "Stopping profile container ${CONTAINER_NAME}..."
        docker stop "$CONTAINER_NAME" || true
        jq '.status = "stopped"' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
        echo "✅ Profile stopped."
        ;;

    pause)
        echo "Pausing profile container ${CONTAINER_NAME}..."
        docker pause "$CONTAINER_NAME"
        jq '.status = "paused"' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
        echo "✅ Profile paused (0% CPU, RAM retained)."
        ;;

    unpause|resume)
        echo "Resuming profile container ${CONTAINER_NAME}..."
        docker unpause "$CONTAINER_NAME"
        jq '.status = "running"' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
        echo "✅ Profile resumed."
        ;;

    status)
        docker ps -a -f name="^/${CONTAINER_NAME}$"
        ;;

    logs)
        docker logs -f "$CONTAINER_NAME"
        ;;

    *)
        echo "Usage: $0 <profile_id> {start|stop|pause|unpause|status|logs}"
        exit 1
        ;;
esac
