FROM ubuntu:22.04

LABEL org.automat-fb.browser-image-version="3"

# Prevent interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=America/New_York
ENV DISPLAY=:99
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Install core system dependencies and virtual display packages
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates tzdata locales unzip gosu \
    xvfb x11vnc websockify novnc \
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
    ffmpeg libva2 vainfo mesa-utils libgl1-mesa-dri mesa-va-drivers \
    mesa-vulkan-drivers vulkan-tools intel-media-va-driver python3-minimal \
    --no-install-recommends && \
    locale-gen en_US.UTF-8 && \
    update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 && \
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

# Configure ordinary Linux LCD font rendering.
COPY fonts.conf /etc/fonts/local.conf
RUN fc-cache -fv

# Local-only page used to record values exposed by Chrome itself. This avoids
# mistaking container-side GLX/system values for the browser's web fingerprint.
COPY browser_probe.py /usr/local/bin/browser_probe.py
RUN chmod 0755 /usr/local/bin/browser_probe.py

# Create non-root chromeuser for safe unprivileged browser execution
RUN useradd -m -s /bin/bash chromeuser && \
    mkdir -p /data/profile && \
    chown -R chromeuser:chromeuser /data

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Persistent profile volume mount
VOLUME ["/data/profile"]

# WebSocket noVNC client. The raw VNC port remains internal to the container.
EXPOSE 6080

ENTRYPOINT ["/entrypoint.sh"]
