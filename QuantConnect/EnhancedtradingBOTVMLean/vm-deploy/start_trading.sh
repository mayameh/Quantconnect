#!/usr/bin/env bash
# ==============================================================================
# Start Trading — launches IB Gateway + LEAN via Docker Compose
# ==============================================================================
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DEPLOY_DIR"

# Load env
if [ ! -f .env ]; then
    echo "ERROR: .env file not found. Copy .env.template → .env and fill in credentials."
    exit 1
fi

source .env

# Validate required vars
for var in IB_LOGIN_ID IB_PASSWORD IB_ACCOUNT; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set in .env"
        exit 1
    fi
done

echo "============================================================"
echo "  Starting IB Gateway + LEAN Engine"
echo "  Mode: ${IB_TRADING_MODE:-paper}"
echo "  Account: ${IB_ACCOUNT}"
echo "  Port: ${IB_PORT:-4002}"
echo "============================================================"

# Ensure algo files are current
echo "Syncing algorithm files..."
mkdir -p algo
cp -v algo_source/main.py algo/ 2>/dev/null || true
cp -v algo_source/production_config.py algo/ 2>/dev/null || true
cp -v algo_source/production_wrapper.py algo/ 2>/dev/null || true

# Pull latest images
echo "Pulling Docker images..."
docker compose pull

# Start services
echo "Starting services..."
docker compose up -d

echo ""
echo "Services started. Useful commands:"
echo "  docker compose logs -f ibgateway   # IB Gateway logs"
echo "  docker compose logs -f lean        # LEAN engine logs"
echo "  docker compose ps                  # service status"
echo "  docker compose down                # stop everything"
echo ""
echo "VNC: connect to localhost:5900 (password: ${VNC_PASSWORD:-headless})"
echo "============================================================"
