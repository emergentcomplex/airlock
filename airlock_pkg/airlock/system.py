cat <<'EOF' > airlock_pkg/airlock/system.py
import subprocess
import json
import shutil
import os
import hashlib
import sys
from pathlib import Path
from . import ui

# --- CONSTANTS ---
MOUNT_POINT = Path("/mnt/airlock")
REPO_DIR = MOUNT_POINT / "repo"
NIX_TRANSFER_DIR = MOUNT_POINT / "nix_transfer"
CACHE_DIR = Path("/var/cache/pacman/pkg")
PACMAN_BIN = "/usr/bin/pacman"
# Hardcoded path to Root's Nix Channel to bypass sudo env stripping
NIX_CHANNEL_PATH = "nixpkgs=/nix/var/nix/profiles/per-user/root/channels/nixpkgs"
NIX_PROFILE_PATH = "/nix/var/nix/profiles/default"

# --- SHELL EXECUTION ---
def run_cmd(cmd, cwd=None, capture=False, check=True, shell=False):
    """
    Executes a shell command with robust error handling.
    Returns stripped stdout if capture=True, else None.
    """
    try:
        res = subprocess.run(
            cmd, 
            cwd=cwd, 
            stdout=subprocess.PIPE if capture else None, 
            stderr=subprocess.PIPE if capture else None, 
            text=True, 
            check=check, 
            shell=shell
        )
        return res.stdout.strip() if capture else None
    except subprocess.CalledProcessError as e:
        # If we are capturing, the error is likely in e.stderr
        # If not capturing, the error was likely printed to screen already
        error_msg = e.stderr if capture else "See output above"
        
        # We re-raise to allow the caller (like a Spinner) to handle the UI cleanup
        # before crashing.
        raise e

def check_root():
    """Enforces sudo execution."""
    if os.geteuid() != 0:
        ui.fail("Must run as root (sudo)")

# --- HARDWARE ABSTRACTION ---
def auto_mount_usb():
    """
    Scans for a single valid USB block device and mounts it.
    Fails if 0 or >1 devices are found to prevent ambiguity.
    """
    if MOUNT_POINT.is_mount():
        return

    ui.step("Scanning for USB drives...")
    try:
        # Get JSON output from lsblk for reliable parsing
        lsblk_out = run_cmd(["lsblk", "-J", "-o", "NAME,TRAN,FSTYPE,MOUNTPOINT"], capture=True)
        data = json.loads(lsblk_out)
    except Exception as e:
        ui.fail("Failed to scan USB devices", str(e))

    usb_devices = []
    for device in data.get("blockdevices", []):
        if device.get("tran") == "usb":
            # Check partitions (children) first
            if "children" in device:
                for child in device["children"]:
                    if child.get("fstype"): 
                        usb_devices.append(f"/dev/{child['name']}")
            # Check main device if it has a filesystem directly
            elif device.get("fstype"): 
                usb_devices.append(f"/dev/{device['name']}")
    
    if len(usb_devices) == 0:
        ui.fail("No USB drive detected. Please insert a drive.")
    if len(usb_devices) > 1:
        ui.fail(f"Multiple USBs detected: {usb_devices}. Insert only ONE to ensure safety.")
    
    target = usb_devices[0]
    ui.info(f"Detected: {target}")
    
    MOUNT_POINT.mkdir(parents=True, exist_ok=True)
    try:
        run_cmd(["mount", target, str(MOUNT_POINT)])
    except subprocess.CalledProcessError as e:
        ui.fail(f"Failed to mount {target}", e.stderr)

def check_disk_space(path, required_gb=1.0):
    """Ensures the target has enough free space before writing."""
    try:
        total, used, free = shutil.disk_usage(path)
        free_gb = free / (1024**3)
        if free_gb < required_gb:
            ui.fail(f"Insufficient disk space on {path}", 
                   f"Required: {required_gb} GB\nAvailable: {free_gb:.2f} GB")
    except FileNotFoundError:
        ui.fail(f"Path not found for disk check: {path}")

# --- CRYPTOGRAPHY ---
def calculate_sha256(filepath):
    """Calculates SHA256 hash of a file in chunks (memory safe)."""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except FileNotFoundError:
        ui.fail(f"File not found for hashing: {filepath}")
    except OSError as e:
        ui.fail(f"Error reading file {filepath}", str(e))

def sync_disk():
    """Forces a physical write to the NAND flash."""
    run_cmd(["sync"])
EOF
