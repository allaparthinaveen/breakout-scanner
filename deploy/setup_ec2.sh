#!/usr/bin/env bash
# ==============================================================================
# 🚀 Breakout Scanner - AWS EC2 Provisioning Script
# Designed for Amazon Linux 2023 or Ubuntu 22.04+ (t4g.small ARM64 Graviton)
# ==============================================================================

set -euo pipefail

echo "========================================================================"
echo "⚡ Starting Breakout Scanner EC2 Environment Setup"
echo "========================================================================"

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    OS="unknown"
fi

echo "📦 Detected OS: $OS ($VERSION_ID)"

# 1. Update packages & install Python 3.11 + Git
if [[ "$OS" == "amzn" ]]; then
    sudo dnf update -y
    sudo dnf install -y python3.11 python3.11-pip python3.11-devel git gcc gcc-c++ make
    APP_USER="ec2-user"
elif [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
    sudo apt-get update -y
    sudo apt-get install -y python3.11 python3.11-venv python3.11-dev git build-essential curl
    APP_USER="ubuntu"
else
    echo "⚠️ Unsupported OS: $OS. Attempting generic install..."
    APP_USER=$(whoami)
fi

# 2. Configure 2GB Swapfile (Protects memory from OOM crashes on t4g.micro/small instances)
if ! swapon --show | grep -q "/swapfile"; then
    echo "🧠 Configuring 2GB Swapfile for memory safety..."
    sudo dd if=/dev/zero of=/swapfile bs=128M count=16
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo "/swapfile swap swap defaults 0 0" | sudo tee -a /etc/fstab
    echo "✅ 2GB Swapfile created and activated."
else
    echo "✅ Swapfile already active."
fi

REPO_DIR="/home/$APP_USER/breakout-scanner"

# 3. Setup Virtual Environment
if [ ! -d "$REPO_DIR" ]; then
    echo "❌ Repository not found at $REPO_DIR. Please clone repository first."
    exit 1
fi

cd "$REPO_DIR"

echo "🐍 Creating Python 3.11 Virtual Environment..."
if [ ! -d "venv" ]; then
    python3.11 -m venv venv
fi

echo "📦 Installing Dependencies from requirements.txt..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 3. Create required directories
mkdir -p data logs config/profiles
chmod -R 755 data logs

# 4. Install and enable Systemd service
echo "⚙️ Configuring Systemd Service..."
sed -i "s/ec2-user/$APP_USER/g" deploy/breakout-scanner.service
sudo cp deploy/breakout-scanner.service /etc/systemd/system/breakout-scanner.service
sudo systemctl daemon-reload
sudo systemctl enable breakout-scanner.service

echo "========================================================================"
echo "✅ EC2 SETUP COMPLETE!"
echo "   To start the daemon: sudo systemctl start breakout-scanner"
echo "   To check status:     sudo systemctl status breakout-scanner"
echo "   To view logs:        tail -f logs/cloud_daemon.log"
echo "========================================================================"
