#!/usr/bin/env bash
# ==============================================================================
# stop.sh - Stop Standalone ReDroid Environment
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

BLUE='\033[0;34m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${BLUE}Stopping ReDroid and ws-scrcpy containers...${NC}"
docker compose down

echo -e "${GREEN}Containers stopped cleanly.${NC}"
