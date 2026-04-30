#!/usr/bin/env bash
# ==============================================================================
# VM Setup Script for Headless IB Gateway + IBC + LEAN Engine
# Tested on: Ubuntu 22.04 / 24.04 LTS (x86_64)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IBC_VERSION="3.19.0"
IB_GATEWAY_VERSION="10.30"   # Stable channel — change as needed
LEAN_TAG="latest"             # Docker image tag for quantconnect/lean

echo "============================================================"
echo "  Headless IB Gateway + IBC + LEAN — VM Setup"
echo "============================================================"

# ------------------------------------------------------------------
# 1. System packages
# ------------------------------------------------------------------
echo "[1/8] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    software-properties-common \
    unzip \
    wget \
    git \
    jq \
    xvfb \
    x11vnc \
    fluxbox \
    xterm \
    libxrender1 \
    libxtst6 \
    libxi6 \
    libxss1 \
    libgtk-3-0 \
    libgconf-2-4 \
    libnss3 \
    libatk-bridge2.0-0 \
    fonts-dejavu-core \
    dbus-x11 \
    socat \
    net-tools \
    supervisor \
    logrotate \
    cron

# ------------------------------------------------------------------
# 2. Java 11+ (IB Gateway requires it)
# ------------------------------------------------------------------
echo "[2/8] Installing Java runtime..."
sudo apt-get install -y openjdk-17-jre-headless
java -version

# ------------------------------------------------------------------
# 3. Docker & Docker Compose (for LEAN engine)
# ------------------------------------------------------------------
echo "[3/8] Installing Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    echo "  ➜ Docker installed. You may need to re-login for group changes."
fi
sudo systemctl enable docker
sudo systemctl start docker

# Docker Compose plugin
if ! docker compose version &>/dev/null; then
    sudo apt-get install -y docker-compose-plugin
fi

# ------------------------------------------------------------------
# 4. .NET 8 SDK (optional — only if you want to run LEAN natively)
# ------------------------------------------------------------------
echo "[4/8] Installing .NET 8 SDK..."
if ! command -v dotnet &>/dev/null; then
    wget -q https://dot.net/v1/dotnet-install.sh -O /tmp/dotnet-install.sh
    chmod +x /tmp/dotnet-install.sh
    /tmp/dotnet-install.sh --channel 8.0 --install-dir /usr/share/dotnet
    sudo ln -sf /usr/share/dotnet/dotnet /usr/local/bin/dotnet
fi
dotnet --version || echo "  ➜ .NET install skipped or version check failed"

# ------------------------------------------------------------------
# 5. Python 3.11+ & pip packages (for local algo development)
# ------------------------------------------------------------------
echo "[5/8] Installing Python 3.11..."
sudo add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
sudo apt-get update -qq
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Install LEAN CLI
pip3 install --upgrade lean

# ------------------------------------------------------------------
# 6. IB Gateway (offline installer)
# ------------------------------------------------------------------
echo "[6/8] Installing IB Gateway ${IB_GATEWAY_VERSION}..."
IB_INSTALL_DIR="/opt/ibgateway"
sudo mkdir -p "$IB_INSTALL_DIR"

# Download IB Gateway stable Linux installer
IB_INSTALLER_URL="https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"
IB_INSTALLER="/tmp/ibgateway-installer.sh"

if [ ! -f "$IB_INSTALLER" ]; then
    echo "  Downloading IB Gateway installer..."
    wget -q "$IB_INSTALLER_URL" -O "$IB_INSTALLER"
    chmod +x "$IB_INSTALLER"
fi

echo "  Running IB Gateway installer (unattended)..."
sudo "$IB_INSTALLER" -q -dir "$IB_INSTALL_DIR" || {
    echo "  ⚠ Auto-install failed. Try: sudo bash $IB_INSTALLER"
}

# ------------------------------------------------------------------
# 7. IBC (IB Controller) — automates gateway login/restarts
# ------------------------------------------------------------------
echo "[7/8] Installing IBC ${IBC_VERSION}..."
IBC_DIR="/opt/ibc"
sudo mkdir -p "$IBC_DIR"

IBC_URL="https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip"
wget -q "$IBC_URL" -O /tmp/ibc.zip
sudo unzip -o /tmp/ibc.zip -d "$IBC_DIR"
sudo chmod +x "$IBC_DIR"/*.sh

# ------------------------------------------------------------------
# 8. Create directory structure
# ------------------------------------------------------------------
echo "[8/8] Creating directory structure..."
DEPLOY_DIR="/opt/lean-trading"
sudo mkdir -p "$DEPLOY_DIR"/{algo,data,logs,results,config}
sudo chown -R "$USER:$USER" "$DEPLOY_DIR"

# Copy algorithm files
cp "$SCRIPT_DIR"/../main.py "$DEPLOY_DIR/algo/"
cp "$SCRIPT_DIR"/../production_config.py "$DEPLOY_DIR/algo/"
cp "$SCRIPT_DIR"/../production_wrapper.py "$DEPLOY_DIR/algo/"

# Copy config files from vm-deploy/
cp "$SCRIPT_DIR"/ibc-config.ini "$DEPLOY_DIR/config/"
cp "$SCRIPT_DIR"/lean-live.json "$DEPLOY_DIR/config/lean.json"
cp "$SCRIPT_DIR"/docker-compose.yml "$DEPLOY_DIR/"
cp "$SCRIPT_DIR"/start_trading.sh "$DEPLOY_DIR/"
chmod +x "$DEPLOY_DIR/start_trading.sh"

echo ""
echo "============================================================"
echo "  Setup complete!"
echo "============================================================"
echo ""
echo "Next steps:"
echo "  1. Edit /opt/lean-trading/config/ibc-config.ini"
echo "     → Set IbLoginId, IbPassword, TradingMode"
echo ""
echo "  2. Edit /opt/lean-trading/config/lean.json"
echo "     → Set ib-account, ib-user-name, ib-password"
echo ""
echo "  3. Start trading:"
echo "     cd /opt/lean-trading"
echo "     ./start_trading.sh"
echo ""
echo "  4. (Optional) Install systemd services:"
echo "     sudo cp $SCRIPT_DIR/ibgateway.service /etc/systemd/system/"
echo "     sudo cp $SCRIPT_DIR/lean-algo.service /etc/systemd/system/"
echo "     sudo systemctl daemon-reload"
echo "     sudo systemctl enable ibgateway lean-algo"
echo ""
echo "Required open ports: 4001 (IB live), 4002 (IB paper)"
echo "VNC available on :1 (port 5901) for debugging IB Gateway"
echo "============================================================"
