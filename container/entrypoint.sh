#!/bin/bash
set -e

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
WIN_SIZE="${WINDOW_SIZE:-1920,1080}"
CHROME_LANG="${LANG:-en-US}"
CHROME_UA="${USER_AGENT:-Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36}"
TARGET_URL="${START_URL:-https://www.google.com}"

# Clean up any stale X locks
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

# 2. Start Xvfb Virtual Framebuffer
echo "Starting Xvfb on :99 with resolution ${SCREEN_RES}..."
Xvfb :99 -screen 0 "${SCREEN_RES}" -ac +extension GLX +render -noreset &
XVFB_PID=$!
export DISPLAY=:99

# Wait for X display to become ready
echo "Waiting for X display to initialize..."
for i in {1..30}; do
    if xset q &>/dev/null; then
        echo "X display :99 is ready."
        break
    fi
    sleep 0.2
done

# 3. Start VNC Server (x11vnc bound to 0.0.0.0 on port 5900)
echo "Starting VNC server on port 5900..."
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -listen 0.0.0.0 -bg -quiet

# Start WebSocket bridge for web/Tauri noVNC embedding
echo "Starting noVNC WebSocket bridge on port 6080..."
websockify --web /usr/share/novnc 0.0.0.0:6080 localhost:5900 &>/dev/null &
WEBSOCKIFY_PID=$!

# Start X11 clipboard synchronization daemons
if command -v autocutsel &>/dev/null; then
    echo "Starting X11 clipboard synchronization daemon (autocutsel)..."
    autocutsel -fork -display :99 &>/dev/null &
    autocutsel -selection CLIPBOARD -fork -display :99 &>/dev/null &
fi

# 4. Network Proxy Setup & Fail-Closed Killswitch
if [ -n "$PROXY_HOST" ]; then
    PROXY_PORT="${PROXY_PORT:-1080}"
    echo "Configuring proxy tunnel via ${PROXY_HOST}:${PROXY_PORT}..."

    # Resolve PROXY_HOST to IP if provided as hostname
    PROXY_IP=$(getent hosts "$PROXY_HOST" 2>/dev/null | awk '{print $1}' | head -n 1)
    if [ -z "$PROXY_IP" ]; then
        PROXY_IP="$PROXY_HOST"
    fi

    if [ -e /dev/net/tun ]; then
        # Create TUN interface
        ip tuntap add dev tun0 mode tun user chromeuser 2>/dev/null || true
        ip addr add 198.18.0.1/15 dev tun0 2>/dev/null || true
        ip link set dev tun0 up 2>/dev/null || true

        # Start tun2socks tunnel
        if [ -n "$PROXY_USER" ] && [ -n "$PROXY_PASS" ]; then
            tun2socks -device tun0 -proxy "socks5://${PROXY_USER}:${PROXY_PASS}@${PROXY_HOST}:${PROXY_PORT}" &
        else
            tun2socks -device tun0 -proxy "socks5://${PROXY_HOST}:${PROXY_PORT}" &
        fi
        TUN2SOCKS_PID=$!
        sleep 0.5

        # Route proxy server IP directly via eth0 gateway before changing default route
        ORIGINAL_GW=$(ip route show default dev eth0 2>/dev/null | awk '{print $3}' | head -n 1)
        if [ -n "$ORIGINAL_GW" ] && [ -n "$PROXY_IP" ]; then
            ip route add "$PROXY_IP" via "$ORIGINAL_GW" dev eth0 2>/dev/null || true
        fi

        # Route all other traffic through tun0
        ip route del default dev eth0 2>/dev/null || true
        ip route add default dev tun0 metric 1 2>/dev/null || true
        echo "tun2socks proxy tunnel active."

        # ENFORCE FAIL-CLOSED KILLSWITCH (iptables)
        # Blocks any direct internet connection if proxy fails or disconnects
        echo "Enforcing fail-closed network killswitch (iptables)..."
        iptables -F OUTPUT 2>/dev/null || true

        # Allow loopback (essential for X11, websockify, VNC)
        iptables -A OUTPUT -o lo -j ACCEPT

        # Allow tun0 (all proxied application traffic)
        iptables -A OUTPUT -o tun0 -j ACCEPT

        # Allow direct connection ONLY to proxy IP and port on eth0
        if [ -n "$PROXY_IP" ]; then
            iptables -A OUTPUT -o eth0 -d "$PROXY_IP" -p tcp --dport "$PROXY_PORT" -j ACCEPT
        fi

        # Allow local Docker subnets (for host VNC :5900, noVNC WebSocket :6080)
        iptables -A OUTPUT -o eth0 -d 172.16.0.0/12 -j ACCEPT
        iptables -A OUTPUT -o eth0 -d 192.168.0.0/16 -j ACCEPT
        iptables -A OUTPUT -o eth0 -d 10.0.0.0/8 -j ACCEPT

        # KILLSWITCH: Drop all other outbound internet traffic on eth0
        iptables -A OUTPUT -o eth0 -j DROP
        echo "✅ Fail-closed killswitch ACTIVE: direct connections strictly blocked."
        # Configure DNS over TCP (use-vc) so DNS queries route through the SOCKS5 TCP tunnel
        echo "Configuring DNS over TCP (1.1.1.1, 8.8.8.8)..."
        cat <<EOF > /etc/resolv.conf
nameserver 1.1.1.1
nameserver 8.8.8.8
options use-vc
EOF
    else
        echo "Notice: /dev/net/tun not found, using Chrome-level proxy flags."
        EXTRA_CHROME_FLAGS="${EXTRA_CHROME_FLAGS} --proxy-server=socks5://${PROXY_HOST}:${PROXY_PORT}"
    fi

    # Anti-leak Chrome network flags (WebRTC non-proxied UDP disabled, loopback bypass, disable QUIC for TCP proxy stability)
    EXTRA_CHROME_FLAGS="${EXTRA_CHROME_FLAGS} --disable-quic --disable-webrtc-hw-encoding --enforce-webrtc-ip-permission-check --webrtc-ip-handling-policy=disable_non_proxied_udp"
fi

# Graceful shutdown handler
cleanup() {
    echo "Received termination signal. Shutting down gracefully..."
    if [ -n "$CHROME_PID" ]; then
        kill -TERM "$CHROME_PID" 2>/dev/null || true
        wait "$CHROME_PID" 2>/dev/null || true
    fi
    if [ -n "$TUN2SOCKS_PID" ]; then
        kill -TERM "$TUN2SOCKS_PID" 2>/dev/null || true
    fi
    kill -TERM "$WEBSOCKIFY_PID" 2>/dev/null || true
    kill -TERM "$XVFB_PID" 2>/dev/null || true
    echo "Container cleanup finished."
    exit 0
}

trap cleanup SIGTERM SIGINT

# 5. Launch Google Chrome as chromeuser
echo "Clearing stale browser locks..."
rm -f /data/profile/Singleton* 2>/dev/null || true

echo "Launching Google Chrome with anti-detection flags..."
gosu chromeuser env TZ="${TZ}" google-chrome \
    --display=:99 \
    --user-data-dir=/data/profile \
    --no-first-run \
    --no-default-browser-check \
    --lang="${CHROME_LANG}" \
    --window-size="${WIN_SIZE}" \
    --window-position=0,0 \
    --ignore-gpu-blocklist \
    --enable-gpu-rasterization \
    --enable-zero-copy \
    --disable-features=UserAgentClientHint \
    --user-agent="${CHROME_UA}" \
    ${EXTRA_CHROME_FLAGS} \
    "${TARGET_URL}" &

CHROME_PID=$!
echo "Google Chrome running (PID: $CHROME_PID)."
echo "=== Container initialization complete. VNC ready on port 5900 ==="

# Wait on Chrome process
wait "$CHROME_PID"
