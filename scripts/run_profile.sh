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
SCREEN_RES=$(jq -r '.fingerprint.screen_resolution // "1920x1080"' "$CONFIG_FILE")
COLOR_DEPTH=$(jq -r '.fingerprint.color_depth // 24' "$CONFIG_FILE")
USER_AGENT=$(jq -r '.fingerprint.user_agent // ""' "$CONFIG_FILE")
TIMEZONE=$(jq -r '.fingerprint.timezone // "America/New_York"' "$CONFIG_FILE")
LANG_VAL=$(jq -r '.fingerprint.language // "en-US"' "$CONFIG_FILE")

PROXY_HOST=$(jq -r '.network.proxy_host // ""' "$CONFIG_FILE")
PROXY_PORT=$(jq -r '.network.proxy_port // ""' "$CONFIG_FILE")
PROXY_USER=$(jq -r '.network.proxy_user // ""' "$CONFIG_FILE")
PROXY_PASS=$(jq -r '.network.proxy_pass // ""' "$CONFIG_FILE")

case "$ACTION" in
    start)
        echo "=== Launching Profile: ${PROFILE_ID} ==="
        echo "Container:   ${CONTAINER_NAME}"
        echo "VNC Port:    ${VNC_PORT} -> 5900"
        echo "noVNC Port:  ${WS_PORT} -> 6080"
        echo "Resolution:  ${SCREEN_RES}x${COLOR_DEPTH}"
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
            -e "SCREEN_RESOLUTION=${SCREEN_RES}x${COLOR_DEPTH}"
            -e "WINDOW_SIZE=${SCREEN_RES/x/,}"
            -e "USER_AGENT=${USER_AGENT}"
            -e "TZ=${TIMEZONE}"
            -e "LANG=${LANG_VAL}"
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

        # Pass host GPU acceleration devices if available
        DEVICE_ARGS=()
        if [ -d /dev/dri ]; then
            DEVICE_ARGS+=(--device /dev/dri:/dev/dri)
        fi

        # Shared media directory for batch campaigns
        SHARED_MEDIA_DIR="${ROOT_DIR}/profiles/shared_media"
        mkdir -p "$SHARED_MEDIA_DIR"

        # Run container with resource limits, display ports, and network capabilities
        docker run -d \
            --name "$CONTAINER_NAME" \
            -p "${VNC_PORT}:5900" \
            -p "${WS_PORT}:6080" \
            -v "${DATA_DIR}:/data/profile" \
            -v "${SHARED_MEDIA_DIR}:/data/shared_media:ro" \
            -v "${ROOT_DIR}/container/entrypoint.sh:/entrypoint.sh:ro" \
            "${CAP_ARGS[@]}" \
            "${DEVICE_ARGS[@]}" \
            --memory="2048m" \
            --cpus="4.0" \
            --shm-size="1g" \
            "${ENV_ARGS[@]}" \
            "$IMAGE_NAME"

        # Update status in config.json
        jq '.status = "running"' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"

        echo "✅ Profile ${PROFILE_ID} launched successfully!"
        echo "👉 Connect via VNC: 127.0.0.1:${VNC_PORT} | noVNC WebSocket: ws://127.0.0.1:${WS_PORT}"
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
