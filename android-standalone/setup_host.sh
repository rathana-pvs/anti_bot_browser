#!/usr/bin/env bash
# ==============================================================================
# setup_host.sh - Host Kernel & Device Verification for ReDroid
# ==============================================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Auto-elevate to root if not already running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}Elevating to root permissions...${NC}"
    exec sudo "$0" "$@"
fi

echo -e "${BLUE}=== Checking Host Kernel Prerequisites for ReDroid ===${NC}"

# 1. Check Binder devices
echo -n "Checking binder devices (/dev/binder, /dev/hwbinder, /dev/vndbinder)... "
if [ -e /dev/binder ] && [ -e /dev/hwbinder ] && [ -e /dev/vndbinder ]; then
    echo -e "${GREEN}OK (loaded)${NC}"
else
    echo -e "${YELLOW}NOT FOUND${NC}"
    echo -e "${BLUE}Attempting to load binder_linux module...${NC}"
    
    if modprobe binder_linux devices="binder,hwbinder,vndbinder"; then
        echo -e "${GREEN}Successfully loaded binder_linux!${NC}"
    else
        echo -e "${RED}Failed to load binder_linux module.${NC}"
        echo "Please ensure 'linux-modules-extra-$(uname -r)' is installed:"
        echo "  apt update && apt install -y linux-modules-extra-$(uname -r)"
        exit 1
    fi
fi

# Ensure permissions on binder devices
chmod 666 /dev/binder /dev/hwbinder /dev/vndbinder 2>/dev/null || true
echo -e "${GREEN}Binder devices are active with read/write permissions.${NC}"

# Enable automatic loading on boot
echo "binder_linux" > /etc/modules-load.d/redroid.conf 2>/dev/null || true
echo 'options binder_linux devices="binder,hwbinder,vndbinder"' > /etc/modprobe.d/redroid.conf 2>/dev/null || true
echo -e "${GREEN}Saved configuration so binder loads automatically on system reboot.${NC}"

# 2. Check GPU acceleration (Render nodes)
echo -n "Checking GPU hardware acceleration (/dev/dri/renderD128)... "
if [ -e /dev/dri/renderD128 ]; then
    echo -e "${GREEN}OK (GPU node detected)${NC}"
else
    echo -e "${YELLOW}No /dev/dri/renderD128 found. ReDroid will fallback to SwiftShader (software rendering).${NC}"
fi

# 3. Check Docker and Docker Compose
echo -n "Checking Docker... "
if command -v docker &> /dev/null; then
    echo -e "${GREEN}OK ($(docker --version))${NC}"
else
    echo -e "${RED}Docker is not installed!${NC}"
    exit 1
fi

echo -n "Checking Docker Compose... "
if docker compose version &> /dev/null; then
    echo -e "${GREEN}OK ($(docker compose version))${NC}"
else
    echo -e "${RED}Docker Compose plugin is not installed!${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}🎉 Host environment is ready for ReDroid!${NC}"
echo "You can now run: ./start.sh"
