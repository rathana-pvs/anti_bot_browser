FROM ubuntu:22.04

# Prevent interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=America/New_York
ENV DISPLAY=:99

# Install core system dependencies and virtual display packages
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates tzdata unzip gosu \
    redsocks iptables iproute2 \
    fonts-liberation \
    fonts-noto \
    fonts-ubuntu \
    fonts-dejavu-core \
    fonts-noto-color-emoji \
    fontconfig \
    software-properties-common \
    dbus-x11 x11-utils x11-xserver-utils scrot xdotool \
    xclip autocutsel \
    ffmpeg libva2 vainfo mesa-va-drivers intel-media-va-driver \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# KasmVNC replaces Xvfb + x11vnc + websockify + noVNC. Pin the package and
# verify its release checksum so container builds remain reproducible.
ARG KASMVNC_VERSION=1.5.0
RUN wget -q -O /tmp/kasmvnc.deb \
    "https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/kasmvncserver_jammy_${KASMVNC_VERSION}_amd64.deb" && \
    echo "485c190b1d9269fc40fc7205f850ecf71292d737cdb5482e778f2e52f128cd8a  /tmp/kasmvnc.deb" | sha256sum -c - && \
    apt-get update && \
    apt-get install -y /tmp/kasmvnc.deb && \
    rm -f /tmp/kasmvnc.deb && \
    rm -rf /var/lib/apt/lists/*

# Install tun2socks release binary
RUN curl -sL https://github.com/xjasonlyu/tun2socks/releases/download/v2.5.2/tun2socks-linux-amd64.zip -o /tmp/tun2socks.zip && \
    unzip /tmp/tun2socks.zip -d /tmp/tun2socks_pkg && \
    mv /tmp/tun2socks_pkg/tun2socks-linux-amd64 /usr/local/bin/tun2socks && \
    chmod +x /usr/local/bin/tun2socks && \
    rm -rf /tmp/tun2socks*

# Install Google Chrome stable
RUN wget -q -O /tmp/chrome.deb \
    https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    apt-get update && \
    apt-get install -y /tmp/chrome.deb && \
    rm -f /tmp/chrome.deb && \
    rm -rf /var/lib/apt/lists/*

# Configure font rendering to mimic Windows ClearType
COPY fonts.conf /etc/fonts/local.conf
RUN fc-cache -fv

# Create non-root chromeuser for safe unprivileged browser execution
RUN useradd -m -s /bin/bash chromeuser && \
    mkdir -p /data/profile && \
    chown -R chromeuser:chromeuser /data

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Persistent profile volume mount
VOLUME ["/data/profile"]

# KasmVNC's built-in HTTP/WebSocket client.
EXPOSE 6080

ENTRYPOINT ["/entrypoint.sh"]
