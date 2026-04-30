#!/usr/bin/env bash
# ==============================================================================
# Deploy trading bot to VPS
# Usage: ./deploy.sh user@your-vps-ip
# ==============================================================================
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 user@host"
    exit 1
fi

TARGET="$1"
REMOTE_DIR="/opt/tradingbot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Deploying to ${TARGET}:${REMOTE_DIR} ..."

# Create remote directory + venv if it doesn't exist
ssh "$TARGET" "sudo mkdir -p ${REMOTE_DIR}/{logs} && sudo chown -R \$(whoami):\$(whoami) ${REMOTE_DIR}"

# Copy bot files
scp "$SCRIPT_DIR/main_ib.py" \
    "$SCRIPT_DIR/bot_config.py" \
    "$SCRIPT_DIR/requirements.txt" \
    "${TARGET}:${REMOTE_DIR}/"

# Copy .env template only if .env doesn't exist yet
ssh "$TARGET" "test -f ${REMOTE_DIR}/.env || true"
scp "$SCRIPT_DIR/.env.bot" "${TARGET}:${REMOTE_DIR}/.env.template"
ssh "$TARGET" "test -f ${REMOTE_DIR}/.env || cp ${REMOTE_DIR}/.env.template ${REMOTE_DIR}/.env"

# Copy systemd service
scp "$SCRIPT_DIR/tradingbot.service" "${TARGET}:/tmp/"
ssh "$TARGET" "sudo cp /tmp/tradingbot.service /etc/systemd/system/ && sudo systemctl daemon-reload"

# Setup venv + install deps
ssh "$TARGET" "cd ${REMOTE_DIR} && \
    (test -d venv || python3 -m venv venv) && \
    source venv/bin/activate && \
    pip install --upgrade pip && \
    pip install -r requirements.txt"

echo ""
echo "Deploy complete!"
echo ""
echo "Next steps on VPS:"
echo "  1. Edit ${REMOTE_DIR}/.env (set email password, IB port)"
echo "  2. Test: cd ${REMOTE_DIR} && source venv/bin/activate && python3 main_ib.py"
echo "  3. Enable: sudo systemctl enable --now tradingbot"
echo "  4. Monitor: journalctl -u tradingbot -f"
