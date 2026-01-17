#!/bin/bash
set -e

echo "🏭 INITIALIZING FACTORY (RIG A)..."

# --- 1. CRITICAL CONNECTIVITY & TIME ---
echo "🌐 [1/6] Fixing SSL & Time..."
sudo timedatectl set-ntp true
sudo pacman -S --noconfirm ca-certificates-mozilla
sudo trust extract-compat

# --- 2. INSTALLATION ---
echo "📦 [2/6] Installing Dependencies..."
sudo pacman-key --init
sudo pacman-key --populate archlinux chaotic
# Update and install all tools in one go
sudo pacman -Syu --noconfirm nix git python base-devel

# --- 3. NIX CONFIGURATION (ROOT) ---
echo "❄️ [3/6] Configuring Nix..."
sudo systemctl enable --now nix-daemon

# Configure Channels for ROOT (Since the bridge runs as sudo)
# We use a subshell to ensure the environment is fresh
sudo -i bash -c 'nix-channel --add https://nixos.org/channels/nixpkgs-unstable nixpkgs && nix-channel --update'

# --- 4. WORKSPACE GENERATION ---
echo "wm [4/6] Creating Workspace..."
mkdir -p ~/airgap-workspace
cd ~/airgap-workspace

# --- 5. VERIFICATION ---
echo "🔍 [5/6] Verifying Nix Readiness..."
if sudo -i nix-instantiate --eval -E '<nixpkgs>' > /dev/null 2>&1; then
    echo "✅ Nix is operational for Root."
else
    echo "❌ Nix verification failed. Retrying channel update..."
    sudo -i nix-channel --update
fi

# --- 6. COMPLETION ---
echo "✅ FACTORY SETUP COMPLETE."
echo "   Copy 'airlock.py' and your 'configs/' folder to ~/airgap-workspace/"
echo ""
echo "➡️  NEXT STEP: Insert your USB drive and run:"
echo "    cd ~/airgap-workspace"
echo "    sudo python3 airlock.py --mode harvest --config configs/00_base.json configs/01_ide.json"