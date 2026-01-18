#!/bin/bash
# ==============================================================================
# AIRLOCK PROTOCOL: FACTORY BOOTSTRAP (v1.0.0)
# ------------------------------------------------------------------------------
# This script provisions "Rig A" (The Forge). It handles the "Chicken and Egg"
# problem of setting up the build environment required to run the Airlock Python
# suite.
# ==============================================================================

set -e  # Exit immediately if any command fails

# --- VISUALS ---
BOLD='\033[1m'
CYAN='\033[96m'
GREEN='\033[92m'
RED='\033[91m'
RESET='\033[0m'

log_step() { echo -e "\n${BOLD}${CYAN}[*] $1${RESET}"; }
log_success() { echo -e "${BOLD}${GREEN}[✔] $1${RESET}"; }
log_fail() { echo -e "${BOLD}${RED}[✘] FATAL: $1${RESET}"; exit 1; }

clear
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}║  AIRLOCK FACTORY BOOTSTRAP                           ║${RESET}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════╝${RESET}"

# --- 1. CONNECTIVITY & TIME ---
# Critical Fix: Fresh ISOs often have drifted clocks or stale certs, breaking SSL.
log_step "Stabilizing Time and SSL Trust..."

if ! ping -c 1 google.com &>/dev/null; then
    log_fail "No internet connection. Please connect to WiFi/Ethernet."
fi

sudo timedatectl set-ntp true
echo "    > Syncing Package Database (Force Refresh)..."
sudo pacman -Syy

echo "    > Reinstalling CA Certificates..."
sudo pacman -S --noconfirm ca-certificates-mozilla
sudo trust extract-compat
log_success "Connectivity Verified."

# --- 2. SYSTEM DEPENDENCIES ---
log_step "Installing Builder Dependencies..."

echo "    > Initializing Keyrings..."
sudo pacman-key --init
sudo pacman-key --populate archlinux chaotic

echo "    > Installing Tools (Nix, Git, Python)..."
# We install base-devel for compilation tools needed by some Nix builds
sudo pacman -S --noconfirm nix git python base-devel
log_success "Dependencies Installed."

# --- 3. NIX CONFIGURATION ---
log_step "Configuring Nix Build System..."

# Enable the daemon
sudo systemctl enable --now nix-daemon

# Configure Channels for ROOT
# The Airlock Bridge runs as sudo, so ROOT needs the channels, not just the user.
echo "    > Setting up Root Channels (Unstable)..."
if ! sudo -i nix-channel --list | grep -q "nixpkgs"; then
    sudo -i bash -c 'nix-channel --add https://nixos.org/channels/nixpkgs-unstable nixpkgs && nix-channel --update'
else
    echo "    > Channels already configured."
fi

# Configure User Permissions
# We add the current user to nix-users so you can run ad-hoc nix commands if needed
if ! groups $USER | grep -q "nix-users"; then
    echo "    > Adding $USER to nix-users group..."
    # Create group if it doesn't exist (Garuda quirk)
    getent group nix-users || sudo groupadd -r nix-users
    sudo gpasswd -a $USER nix-users
fi
log_success "Nix Configured."

# --- 4. WORKSPACE SETUP ---
log_step "Initializing Workspace..."
WORKSPACE_DIR="$HOME/airlock_workspace"
mkdir -p "$WORKSPACE_DIR"
log_success "Workspace created at $WORKSPACE_DIR"

# --- 5. VERIFICATION ---
log_step "Verifying Build Capability..."

# We verify that ROOT can evaluate a Nix expression.
# This proves the bridge script will be able to run.
if sudo -i nix-instantiate --eval -E '<nixpkgs>' > /dev/null 2>&1; then
    log_success "Nix is operational for Root."
else
    echo "    > Verification failed. Retrying channel update..."
    sudo -i nix-channel --update
    if sudo -i nix-instantiate --eval -E '<nixpkgs>' > /dev/null 2>&1; then
        log_success "Nix is operational after retry."
    else
        log_fail "Nix is not responding. Check internet or channel config."
    fi
fi

# --- COMPLETION ---
echo ""
echo -e "${BOLD}${GREEN}✅ FACTORY READY.${RESET}"
echo "   You may now clone the Airlock repository or create the package structure"
echo "   inside: $WORKSPACE_DIR"
echo ""
echo "   Next Step: Generate the Python Package Artifacts."
