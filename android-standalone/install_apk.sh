#!/usr/bin/env bash
# ==============================================================================
# install_apk.sh - Install APK into running ReDroid container
# Usage: ./install_apk.sh /path/to/app.apk
# ==============================================================================
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

if [ -z "$1" ]; then
    echo -e "${RED}Usage: $0 <path-to-apk-file>${NC}"
    exit 1
fi

APK_PATH="$1"
if [ ! -f "$APK_PATH" ]; then
    echo -e "${RED}File not found: ${APK_PATH}${NC}"
    exit 1
fi

echo "Installing ${APK_PATH}..."
if command -v adb &>/dev/null; then
    adb -s localhost:5555 install -r "$APK_PATH"
else
    docker exec -i ws-scrcpy-standalone adb install -r "$APK_PATH"
fi

echo -e "${GREEN}✅ APK installed successfully!${NC}"
