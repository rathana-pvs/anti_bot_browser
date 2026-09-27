#!/usr/bin/env bash
# ==============================================================================
# start.sh - Launch Standalone ReDroid + Web Scrcpy
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=== Starting ReDroid Standalone Environment ===${NC}"

# Check binder prerequisites
if [ ! -e /dev/binder ]; then
    echo -e "${RED}Error: /dev/binder not found!${NC}"
    echo "Please run the host setup script first:"
    echo "  sudo ./setup_host.sh"
    exit 1
fi

# Ensure data directory exists
mkdir -p data

# Launch Docker Compose stack
echo -e "${BLUE}Pulling and launching containers...${NC}"
docker compose up -d

echo -e "${BLUE}Waiting for Android to boot (sys.boot_completed=1)...${NC}"
MAX_WAIT=60
WAITED=0
BOOT_DONE=0

while [ $WAITED -lt $MAX_WAIT ]; do
    if docker exec redroid-standalone getprop sys.boot_completed 2>/dev/null | grep -q "1"; then
        BOOT_DONE=1
        break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    echo -n "."
done
echo ""

if [ $BOOT_DONE -eq 1 ]; then
    echo -e "${GREEN}✅ Android booted successfully in ${WAITED}s!${NC}"
else
    echo -e "${YELLOW}⚠️ Android is still booting, but containers are up.${NC}"
fi

# Link ADB from ws-scrcpy to redroid
echo -e "${BLUE}Connecting ws-scrcpy to ReDroid ADB...${NC}"
docker exec ws-scrcpy-standalone adb connect redroid:5555 2>/dev/null || true

# Connect host adb if installed
if command -v adb &>/dev/null; then
    echo -e "${BLUE}Connecting host ADB (localhost:5555)...${NC}"
    adb connect localhost:5555 2>/dev/null || true
fi

echo ""
echo -e "${GREEN}====================================================================${NC}"
echo -e "${GREEN}🎉 ReDroid Standalone is ready!${NC}"
echo -e "${GREEN}👉 Open in browser:  ${BLUE}http://localhost:8000${NC}"
echo -e "${GREEN}👉 ADB endpoint:     ${BLUE}localhost:5555${NC}"
echo -e "${GREEN}👉 To stop:          ${BLUE}./stop.sh${NC}"
echo -e "${GREEN}====================================================================${NC}"
