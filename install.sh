#!/usr/bin/env bash
# install.sh — one-shot setup for the GitHub e-paper dashboard
# Run as: bash install.sh
set -e

INSTALL_DIR="$HOME/github-epaper-dashboard"
WAVESHARE_DIR="$HOME/waveshare-epd"

echo "────────────────────────────────────────────"
echo "  GitHub e-paper dashboard — installer"
echo "────────────────────────────────────────────"

# ── 1. System packages ───────────────────────────────────────────────────────
echo "[1/5] Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y \
    python3-pip python3-pil python3-numpy \
    python3-spidev python3-rpi.gpio \
    libopenjp2-7 libopenjp2-tools \
    fonts-dejavu-core \
    git

# ── 2. Enable SPI ────────────────────────────────────────────────────────────
echo "[2/5] Enabling SPI interface…"
if ! grep -q "^dtparam=spi=on" /boot/config.txt 2>/dev/null && \
   ! grep -q "^dtparam=spi=on" /boot/firmware/config.txt 2>/dev/null; then
    # Try both boot paths (Bookworm vs Bullseye)
    BOOT_CFG="/boot/firmware/config.txt"
    [ -f "$BOOT_CFG" ] || BOOT_CFG="/boot/config.txt"
    echo "dtparam=spi=on" | sudo tee -a "$BOOT_CFG"
    echo "  → SPI enabled in $BOOT_CFG (reboot required after install)"
else
    echo "  → SPI already enabled"
fi

# ── 3. Waveshare e-Paper library ─────────────────────────────────────────────
echo "[3/5] Installing Waveshare e-Paper library…"
if [ -d "$WAVESHARE_DIR" ]; then
    echo "  → Updating existing clone"
    git -C "$WAVESHARE_DIR" pull
else
    git clone --depth 1 \
        https://github.com/waveshare/e-Paper.git \
        "$WAVESHARE_DIR"
fi
pip3 install --break-system-packages -e \
    "$WAVESHARE_DIR/RaspberryPi_JetsonNano/python/" 2>/dev/null || \
pip3 install -e \
    "$WAVESHARE_DIR/RaspberryPi_JetsonNano/python/"

# ── 4. Python dependencies ───────────────────────────────────────────────────
echo "[4/5] Installing Python dependencies…"
pip3 install --break-system-packages -r "$INSTALL_DIR/requirements.txt" 2>/dev/null || \
pip3 install -r "$INSTALL_DIR/requirements.txt"

# ── 5. Systemd service ───────────────────────────────────────────────────────
echo "[5/5] Installing systemd service…"
sudo cp "$INSTALL_DIR/github-dashboard.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable github-dashboard.service
echo "  → Service installed and enabled (will start on next boot)"
echo ""
echo "────────────────────────────────────────────"
echo "  Setup complete!"
echo ""
echo "  NEXT STEPS:"
echo "  1. Edit config.py and add your GitHub token + username"
echo "     nano $INSTALL_DIR/config.py"
echo ""
echo "  2. Test manually first:"
echo "     cd $INSTALL_DIR && python3 dashboard.py"
echo ""
echo "  3. If it works, reboot to start the service:"
echo "     sudo reboot"
echo ""
echo "  To check service logs:"
echo "     journalctl -u github-dashboard -f"
echo "────────────────────────────────────────────"
