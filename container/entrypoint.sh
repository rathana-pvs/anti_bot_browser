#!/bin/bash
set -Eeuo pipefail

echo "=== Initializing Isolated Browser Container ==="

# 1. System Timezone Configuration (Match Proxy Location)
if [ -n "$TZ" ]; then
    echo "Configuring container timezone to match proxy: ${TZ}..."
    if [ -f "/usr/share/zoneinfo/${TZ}" ]; then
        ln -fs "/usr/share/zoneinfo/${TZ}" /etc/localtime
        echo "${TZ}" > /etc/timezone
    fi
    export TZ
fi

# Fix permissions for the persistent data volume
mkdir -p /data/profile
chown -R chromeuser:chromeuser /data/profile

# Configure direct rendering device permissions if /dev/dri is mounted
if [ -d "/dev/dri" ]; then
    echo "Hardware acceleration device /dev/dri detected. Configuring permissions..."
    chmod -R 666 /dev/dri/* 2>/dev/null || true
    usermod -aG video,render chromeuser 2>/dev/null || true
fi

SCREEN_RES="${SCREEN_RESOLUTION:-1920x1080x24}"
SCREEN_GEOMETRY="${SCREEN_RES%x*}"
WIN_SIZE="${WINDOW_SIZE:-1920,1080}"
CHROME_LANG="${BROWSER_LANG:-en-US}"
TARGET_URL="${START_URL:-https://www.google.com}"
RENDERING_MODE="${RENDERING_MODE:-host_gpu}"
GPU_DEVICE_BACKEND="${GPU_DEVICE_BACKEND:-none}"
EXTRA_CHROME_FLAGS="${EXTRA_CHROME_FLAGS:-}"
TUN2SOCKS_PID=""
PROBE_PID=""
EFFECTIVE_RENDERING_MODE="host_gpu"
RENDERING_FALLBACK_REASON=""

if [ "$RENDERING_MODE" = "software" ]; then
    EFFECTIVE_RENDERING_MODE="software"
    RENDERING_FALLBACK_REASON="software rendering was requested"
    RENDER_ARGS=(--disable-gpu --use-gl=swiftshader)
elif [ "$GPU_DEVICE_BACKEND" = "wsl_dxg" ] && [ -c /dev/dxg ] \
    && [ -f /usr/lib/wsl/lib/libd3d12.so ] && [ -f /usr/lib/wsl/lib/libdxcore.so ]; then
    export LD_LIBRARY_PATH="/usr/lib/wsl/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    export GALLIUM_DRIVER=d3d12
    export LIBGL_ALWAYS_SOFTWARE=0
    # Current Chrome accepts ANGLE here; plain --use-gl=egl is rejected and
    # causes the GPU process to restart with GL disabled.
    RENDER_ARGS=(--ignore-gpu-blocklist --enable-gpu-rasterization --enable-zero-copy --use-gl=angle --use-angle=gl-egl)
elif [ "$GPU_DEVICE_BACKEND" = "drm" ] && [ -d /dev/dri ]; then
    export LIBGL_ALWAYS_SOFTWARE=0
    # ANGLE's Vulkan backend can use the passed DRM render node directly even
    # though Xvfb's own GLX renderer is software-only.
    RENDER_ARGS=(--ignore-gpu-blocklist --enable-gpu-rasterization --enable-zero-copy --use-gl=angle --use-angle=vulkan)
else
    EFFECTIVE_RENDERING_MODE="software"
    RENDERING_FALLBACK_REASON="a compatible host GPU device was not available inside the container"
    RENDER_ARGS=(--disable-gpu --use-gl=swiftshader)
fi

json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/[[:cntrl:]]/ /g'
}

write_rendering_status() {
    local renderer="${1:-Not measured}"
    local accelerated="false"
    if [ "$EFFECTIVE_RENDERING_MODE" = "host_gpu" ] \
        && [ "$renderer" != "Not measured" ] \
        && ! printf '%s' "$renderer" | grep -Eqi 'llvmpipe|softpipe|swiftshader|software rasterizer'; then
        accelerated="true"
    fi
    printf '{"requested_mode":"%s","effective_mode":"%s","device_backend":"%s","accelerated":%s,"renderer":"%s","fallback_reason":"%s"}\n' \
        "$(json_escape "$RENDERING_MODE")" \
        "$(json_escape "$EFFECTIVE_RENDERING_MODE")" \
        "$(json_escape "$GPU_DEVICE_BACKEND")" \
        "$accelerated" \
        "$(json_escape "$renderer")" \
        "$(json_escape "$RENDERING_FALLBACK_REASON")" \
        > /run/rendering-status.json
}

# Clean up only the private embedded display.
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

# Start a fixed-resolution virtual X11 display. Keeping the remote geometry
# stable preserves the profile fingerprint while noVNC scales locally.
echo "Starting Xvfb on :99 with resolution ${SCREEN_RES}..."
Xvfb :99 -screen 0 "${SCREEN_RES}" -ac +extension GLX +render -noreset &
DISPLAY_PID=$!
export DISPLAY=:99

# Wait for X display to become ready
echo "Waiting for X display to initialize..."
for i in {1..30}; do
    if xset q &>/dev/null; then
        echo "X display ${DISPLAY} is ready."
        break
    fi
    sleep 0.2
done

OPENGL_RENDERER="$(glxinfo -B 2>/dev/null | sed -n 's/^OpenGL renderer string: //p' | head -1 || true)"
if [ -z "$OPENGL_RENDERER" ]; then
    OPENGL_RENDERER="Not measured"
fi
if printf '%s' "$OPENGL_RENDERER" | grep -Eqi 'llvmpipe|softpipe|swiftshader|software rasterizer'; then
    if [ "$RENDERING_MODE" = "host_gpu" ] && [ "$GPU_DEVICE_BACKEND" = "drm" ]; then
        RENDERING_FALLBACK_REASON="Xvfb GLX is software; awaiting Chrome WebGL measurement"
    else
        EFFECTIVE_RENDERING_MODE="software"
        if [ -z "$RENDERING_FALLBACK_REASON" ]; then
            RENDERING_FALLBACK_REASON="the active display renderer is software-only"
        fi
    fi
fi
write_rendering_status "$OPENGL_RENDERER"
echo "Rendering: requested=${RENDERING_MODE}, effective=${EFFECTIVE_RENDERING_MODE}, backend=${GPU_DEVICE_BACKEND}, renderer=${OPENGL_RENDERER}"

# Keep the RFB server private to the container and expose it only through the
# loopback-published WebSocket bridge on port 6080.
echo "Starting x11vnc on internal port 5900..."
x11vnc -display "${DISPLAY}" -forever -shared -nopw -rfbport 5900 -listen 127.0.0.1 -bg -quiet

echo "Starting noVNC WebSocket bridge on port 6080..."
websockify --web /usr/share/novnc 0.0.0.0:6080 localhost:5900 &>/dev/null &
WEBSOCKIFY_PID=$!

# Start X11 clipboard synchronization daemons
if command -v autocutsel &>/dev/null; then
    echo "Starting X11 clipboard synchronization daemon (autocutsel)..."
    autocutsel -fork -display "${DISPLAY}" &>/dev/null &
    autocutsel -selection CLIPBOARD -fork -display "${DISPLAY}" &>/dev/null &
fi

# 4. Network Proxy Setup & Fail-Closed Killswitch
if [ -n "${PROXY_HOST:-}" ]; then
    PROXY_PORT="${PROXY_PORT:-1080}"
    echo "Configuring proxy tunnel via ${PROXY_HOST}:${PROXY_PORT}..."

    network_fail() {
        echo "NETWORK PREFLIGHT FAILED: $*" >&2
        printf 'failed: %s\n' "$*" > /run/network-preflight.status
        exit 78
    }

    for command_name in ip iptables ip6tables tun2socks getent timeout; do
        command -v "$command_name" >/dev/null 2>&1 \
            || network_fail "required command is unavailable: ${command_name}"
    done

    [ -c /dev/net/tun ] \
        || network_fail "/dev/net/tun is unavailable; unsafe browser-only proxy fallback is disabled"

    # Resolve exactly one IPv4 endpoint before installing the killswitch. The
    # tunnel is then pinned to that address so DNS cannot select an unapproved
    # endpoint after the firewall is active.
    PROXY_IP=$(getent ahostsv4 "$PROXY_HOST" 2>/dev/null | awk '$2 == "STREAM" {print $1; exit}' || true)
    [ -n "$PROXY_IP" ] || network_fail "proxy hostname has no IPv4 address"
    case "$PROXY_IP" in
        127.*|0.*) network_fail "loopback/unspecified proxy endpoints are not allowed" ;;
    esac

    timeout 5 bash -c "</dev/tcp/${PROXY_IP}/${PROXY_PORT}" 2>/dev/null \
        || network_fail "proxy endpoint is not reachable"

    # Preserve the original gateway route only for the approved proxy endpoint.
    ORIGINAL_GW=$(ip route show default dev eth0 2>/dev/null | awk '{print $3}' | head -n 1)
    [ -n "$ORIGINAL_GW" ] || network_fail "container has no original IPv4 gateway"
    ip route replace "$PROXY_IP" via "$ORIGINAL_GW" dev eth0 \
        || network_fail "could not pin the proxy endpoint route"

    # Create TUN interface.
    ip tuntap add dev tun0 mode tun user chromeuser \
        || network_fail "could not create tun0"
    ip addr add 198.18.0.1/15 dev tun0 \
        || network_fail "could not address tun0"
    ip link set dev tun0 up \
        || network_fail "could not activate tun0"

    # Start tun2socks against the pinned address.
    if [ -n "${PROXY_USER:-}" ] && [ -n "${PROXY_PASS:-}" ]; then
        tun2socks -device tun0 -proxy "socks5://${PROXY_USER}:${PROXY_PASS}@${PROXY_IP}:${PROXY_PORT}" &
    else
        tun2socks -device tun0 -proxy "socks5://${PROXY_IP}:${PROXY_PORT}" &
    fi
    TUN2SOCKS_PID=$!
    sleep 1
    kill -0 "$TUN2SOCKS_PID" 2>/dev/null \
        || network_fail "tun2socks stopped during startup"

    # Route all application IPv4 traffic through tun0.
    ip route del default dev eth0 \
        || network_fail "could not remove the direct IPv4 default route"
    ip route replace default dev tun0 metric 1 \
        || network_fail "could not install the tunnel IPv4 default route"

    # Default-deny both address families. Inbound noVNC replies remain
    # available through the established-connection rules; no broad LAN egress
    # exception is necessary.
    echo "Enforcing fail-closed IPv4/IPv6 network policy..."
    iptables -F OUTPUT || network_fail "could not reset IPv4 firewall"
    iptables -A OUTPUT -o lo -j ACCEPT
    iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    iptables -A OUTPUT -o tun0 -j ACCEPT
    iptables -A OUTPUT -o eth0 -d "$PROXY_IP" -p tcp --dport "$PROXY_PORT" -j ACCEPT
    iptables -P OUTPUT DROP || network_fail "could not default-deny IPv4 output"

    ip6tables -F OUTPUT || network_fail "could not reset IPv6 firewall"
    ip6tables -A OUTPUT -o lo -j ACCEPT
    ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    ip6tables -P OUTPUT DROP || network_fail "could not default-deny IPv6 output"
    while ip -6 route show default | grep -q '^default'; do
        ip -6 route del default || network_fail "could not remove the IPv6 default route"
    done

    # DNS requests use TCP and follow the tun0 default route.
    echo "Configuring DNS over TCP through the tunnel..."
    cat <<EOF > /etc/resolv.conf
nameserver 1.1.1.1
nameserver 8.8.8.8
options use-vc timeout:3 attempts:2
EOF

    # Local preflight: do not contact an external IP-check service or expose the
    # egress address. Chrome starts only after every isolation invariant passes.
    ip link show dev tun0 >/dev/null 2>&1 || network_fail "tun0 is missing"
    # Ask iproute2 to filter by interface instead of parsing its variable
    # human-readable layout (for example, "default dev tun0 metric 1").
    ip route show default dev tun0 | grep -q '^default' \
        || network_fail "IPv4 default route does not use tun0"
    ip route get 1.1.1.1 | grep -Eq ' dev tun0([[:space:]]|$)' \
        || network_fail "public IPv4 traffic does not resolve to tun0"
    ! ip -6 route show default | grep -q '^default' \
        || network_fail "an IPv6 default route is still present"
    [ "$(iptables -S OUTPUT | head -n 1)" = "-P OUTPUT DROP" ] \
        || network_fail "IPv4 firewall is not default-deny"
    [ "$(ip6tables -S OUTPUT | head -n 1)" = "-P OUTPUT DROP" ] \
        || network_fail "IPv6 firewall is not default-deny"
    iptables -C OUTPUT -o eth0 -d "$PROXY_IP" -p tcp --dport "$PROXY_PORT" -j ACCEPT \
        || network_fail "approved proxy endpoint firewall rule is missing"
    grep -Eq '^options .*use-vc' /etc/resolv.conf \
        || network_fail "DNS-over-TCP resolver policy is missing"
    kill -0 "$TUN2SOCKS_PID" 2>/dev/null \
        || network_fail "tun2socks is not running after network setup"
    printf 'passed: proxy_tunnel_ipv4_ipv6_fail_closed\n' > /run/network-preflight.status
    touch /run/network-preflight.ok
    echo "Network preflight PASSED: proxy tunnel and IPv4/IPv6 fail-closed policy active."

    # Anti-leak Chrome network flags (WebRTC non-proxied UDP disabled, loopback bypass, disable QUIC for TCP proxy stability)
    EXTRA_CHROME_FLAGS="${EXTRA_CHROME_FLAGS} --disable-quic --disable-webrtc-hw-encoding --enforce-webrtc-ip-permission-check --webrtc-ip-handling-policy=disable_non_proxied_udp"
else
    printf 'passed: direct_mode_no_proxy_isolation_requested\n' > /run/network-preflight.status
    touch /run/network-preflight.ok
fi

# Graceful shutdown handler
cleanup() {
    echo "Received termination signal. Shutting down gracefully..."
    if [ -n "${CHROME_PID:-}" ]; then
        kill -TERM "$CHROME_PID" 2>/dev/null || true
        wait "$CHROME_PID" 2>/dev/null || true
    fi
    if [ -n "${TUN2SOCKS_PID:-}" ]; then
        kill -TERM "$TUN2SOCKS_PID" 2>/dev/null || true
    fi
    if [ -n "${PROBE_PID:-}" ]; then
        kill -TERM "$PROBE_PID" 2>/dev/null || true
    fi
    kill -TERM "$WEBSOCKIFY_PID" 2>/dev/null || true
    kill -TERM "$DISPLAY_PID" 2>/dev/null || true
    echo "Container cleanup finished."
    exit 0
}

trap cleanup SIGTERM SIGINT

# 5. Launch Google Chrome as chromeuser
echo "Clearing stale browser locks..."
rm -f /data/profile/Singleton* 2>/dev/null || true
rm -f /run/browser-observation.json

echo "Starting local browser environment observation page..."
python3 /usr/local/bin/browser_probe.py \
    --output /run/browser-observation.json \
    --target "${TARGET_URL}" \
    --port 9223 &
PROBE_PID=$!
PROBE_READY=false
for _ in {1..20}; do
    if curl -fsS http://127.0.0.1:9223/ >/dev/null 2>&1; then
        PROBE_READY=true
        break
    fi
    sleep 0.1
done
if [ "$PROBE_READY" != "true" ]; then
    echo "Browser environment observation page failed to start." >&2
    exit 79
fi

echo "Launching Google Chrome with the requested privacy and isolation policy..."
gosu chromeuser env TZ="${TZ}" google-chrome \
    --display="${DISPLAY}" \
    --user-data-dir=/data/profile \
    --no-first-run \
    --no-default-browser-check \
    --start-maximized \
    --lang="${CHROME_LANG}" \
    --window-size="${WIN_SIZE}" \
    --window-position=0,0 \
    "${RENDER_ARGS[@]}" \
    ${EXTRA_CHROME_FLAGS} \
    "http://127.0.0.1:9223/" &

CHROME_PID=$!
echo "Google Chrome running (PID: $CHROME_PID)."

# Ensure Chrome window occupies the full display geometry without black letterboxing
(
    sleep 2
    SCREEN_W="${SCREEN_GEOMETRY%x*}"
    SCREEN_H="${SCREEN_GEOMETRY#*x}"
    xdotool search --onlyvisible --class "google-chrome" windowsize "$SCREEN_W" "$SCREEN_H" 2>/dev/null || true
    xdotool search --onlyvisible --class "google-chrome" windowmove 0 0 2>/dev/null || true
) &
echo "=== Container initialization complete. noVNC ready on port 6080 ==="

# Wait on Chrome process
wait "$CHROME_PID"
