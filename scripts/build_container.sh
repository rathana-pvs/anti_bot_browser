#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE_NAME="isolated-chrome:latest"

echo "=================================================="
echo " Building Docker Image: ${IMAGE_NAME}"
echo " Context: ${ROOT_DIR}/container"
echo "=================================================="

docker build -t "${IMAGE_NAME}" "${ROOT_DIR}/container"

echo ""
echo "✅ Build completed successfully: ${IMAGE_NAME}"
