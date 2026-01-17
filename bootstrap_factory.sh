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
# Configure Channels for ROOT
sudo -i bash -c 'nix-channel --add https://nixos.org/channels/nixpkgs-unstable nixpkgs && nix-channel --update'

# --- 4. WORKSPACE GENERATION ---
echo "wm [4/6] Generating Automation Artifacts..."
mkdir -p ~/airgap-workspace
cd ~/airgap-workspace

# Generate manifest.json
cat <<EOF > manifest.json
{
    "comment": "Master Air-Gap Configuration",
    "pacman_packages": [
        "clamav", "rkhunter", "lynis", "binwalk", "usbguard", "btrbk", "nix"
    ],
    "nix_packages": [
        "hello", "python3", "bashInteractive", "vscodium", "postman", "vlc", "obs-studio"
    ]
}
EOF

# Generate SMART airgap-bridge.py (With Auto-Mount)
cat <<'EOF' > airgap-bridge.py
#!/usr/bin/env python3
import argparse, json, subprocess, os, shutil, sys, time
from pathlib import Path

# --- CONSTANTS ---
MOUNT_POINT = Path("/mnt")
REPO_DIR = MOUNT_POINT / "repo"
NIX_TRANSFER_DIR = MOUNT_POINT / "nix_transfer"
CACHE_DIR = Path("/var/cache/pacman/pkg")
PACMAN_BIN = "/usr/bin/pacman"

def run_cmd(cmd, cwd=None, capture=False, check=True, shell=False):
    try:
        res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE if capture else None, 
                             stderr=subprocess.PIPE if capture else None, text=True, check=check, shell=shell)
        return res.stdout.strip() if capture else None
    except subprocess.CalledProcessError as e:
        print(f"\n❌ COMMAND FAILED: {cmd}")
        if capture: print(f"Error: {e.stderr}")
        sys.exit(1)

def load_config(config_path):
    with open(config_path, 'r') as f: return json.load(f)

# --- HARDWARE ABSTRACTION LAYER ---
def auto_mount_usb():
    """Detects a single USB drive and mounts it."""
    if MOUNT_POINT.is_mount():
        print(f"✅ USB already mounted at {MOUNT_POINT}")
        return

    print("🔍 Scanning for USB drives...")
    # lsblk JSON output: find devices where transport is 'usb'
    lsblk_out = run_cmd(["lsblk", "-J", "-o", "NAME,TRAN,FSTYPE,MOUNTPOINT"], capture=True)
    data = json.loads(lsblk_out)
    
    usb_devices = []
    for device in data.get("blockdevices", []):
        if device.get("tran") == "usb":
            # Check for partitions (children)
            if "children" in device:
                for child in device["children"]:
                    # We want a partition that has a filesystem (exfat, ext4, vfat)
                    if child.get("fstype"):
                        usb_devices.append(f"/dev/{child['name']}")
            # Or if the device itself is formatted (no partition table)
            elif device.get("fstype"):
                usb_devices.append(f"/dev/{device['name']}")

    if len(usb_devices) == 0:
        sys.exit("❌ No USB drive detected. Please insert a drive.")
    if len(usb_devices) > 1:
        sys.exit(f"❌ Multiple USB drives detected: {usb_devices}. Please insert only ONE.")

    target_dev = usb_devices[0]
    print(f"🔌 Detected USB: {target_dev}")
    
    MOUNT_POINT.mkdir(parents=True, exist_ok=True)
    print(f"    Mounting to {MOUNT_POINT}...")
    run_cmd(["mount", target_dev, str(MOUNT_POINT)])

def harvest(config_path):
    auto_mount_usb()
    print(f"🚜 STARTING HARVEST...")
    config = load_config(config_path)
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    NIX_TRANSFER_DIR.mkdir(parents=True, exist_ok=True)

    print("\n📦 [1/5] Harvesting Pacman Packages...")
    installed = run_cmd([PACMAN_BIN, "-Qq"], capture=True).splitlines()
    targets = sorted(list(set(installed + config['pacman_packages'])))
    print(f"    Processing {len(targets)} packages...")
    for pkg in targets:
        matches = list(CACHE_DIR.glob(f"{pkg}-[0-9]*.pkg.tar.zst"))
        if matches:
            latest = sorted(matches)[-1]
            dest = REPO_DIR / latest.name
            if not dest.exists():
                shutil.copy2(latest, dest)
                sig = latest.with_suffix(".pkg.tar.zst.sig")
                if sig.exists(): shutil.copy2(sig, REPO_DIR / sig.name)
        else:
            print(f"    ⬇️ Downloading missing: {pkg}")
            subprocess.run([PACMAN_BIN, "-Syw", "--cachedir", str(REPO_DIR), "--noconfirm", pkg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("    Building Database...")
    for db in REPO_DIR.glob("localrepo.db*"): db.unlink()
    pkg_files = list(REPO_DIR.glob("*.pkg.tar.zst"))
    run_cmd(["repo-add", "-n", "localrepo.db.tar.gz"] + [str(p) for p in pkg_files], cwd=REPO_DIR)

    print("\n🦠 [2/5] Copying ClamAV DB...")
    clam_dest = REPO_DIR / "clamdb"
    clam_dest.mkdir(exist_ok=True)
    for db in Path("/var/lib/clamav").glob("*.cvd"): shutil.copy2(db, clam_dest)

    print("\n❄️ [3/5] Harvesting Nix Environment...")
    nix_file = Path("/tmp/generated_tools.nix")
    pkgs_string = " ".join(config['nix_packages'])
    nix_content = f"""
let pkgs = import <nixpkgs> {{ config = {{ allowUnfree = true; }}; overlays = []; }};
in pkgs.buildEnv {{ name = "airgap-tools"; paths = with pkgs; [ {pkgs_string} ]; }}
"""
    with open(nix_file, 'w') as f: f.write(nix_content)
    
    print("    Building Nix Closure...")
    real_user = os.environ.get('SUDO_USER')
    if real_user:
        cmd = f"sudo -u {real_user} nix-build {nix_file}"
        drv_path = run_cmd(cmd, shell=True, capture=True)
    else:
        drv_path = run_cmd(["nix-build", str(nix_file)], capture=True)
    
    print("    Exporting to USB...")
    export_file = NIX_TRANSFER_DIR / "tools.closure"
    with open(export_file, "w") as outfile:
        subprocess.run(["nix-store", "--export", drv_path], stdout=outfile, check=True)

    print("\n🤖 [4/5] Copying Bridge Script to USB...")
    shutil.copy2(sys.argv[0], MOUNT_POINT / "airgap-bridge.py")

    print("\n💾 [5/5] Syncing to Disk (WAIT)...")
    run_cmd(["sync"])
    print("✅ HARVEST COMPLETE.")

def deploy():
    auto_mount_usb()
    print("🚀 STARTING DEPLOY...")

    print("\n📦 [1/5] Atomic OS Sync...")
    conf_file = Path("/tmp/offline_pacman.conf")
    with open(conf_file, "w") as f:
        f.write(f"[options]\nHoldPkg = pacman glibc\nArchitecture = auto\nSigLevel = Optional TrustAll\nLocalFileSigLevel = Optional\n[localrepo]\nServer = file://{REPO_DIR}\n")
    
    run_cmd([PACMAN_BIN, "--config", str(conf_file), "-Syyu", "--noconfirm", "--overwrite", "*"])
    print("    Ensuring Base Tools...")
    run_cmd([PACMAN_BIN, "--config", str(conf_file), "-S", "--noconfirm", "--needed", "nix", "clamav", "usbguard", "rkhunter", "lynis"])

    print("\n⚙️ [2/5] Configuring Nix...")
    subprocess.run(["groupadd", "-r", "nix-users"], stderr=subprocess.DEVNULL)
    user = os.environ.get('SUDO_USER', os.environ.get('USER'))
    if user: subprocess.run(["gpasswd", "-a", user, "nix-users"], stderr=subprocess.DEVNULL)
    os.makedirs("/etc/nix", exist_ok=True)
    with open("/etc/nix/nix.conf", "w") as f: f.write("trusted-users = root @wheel\nrequire-sigs = false\n")
    run_cmd(["systemctl", "enable", "--now", "nix-daemon"])
    
    print("\n❄️ [3/5] Importing Nix Environment...")
    export_file = NIX_TRANSFER_DIR / "tools.closure"
    local_closure = Path("/tmp/tools.closure")
    if export_file.exists():
        print("    Copying closure to SSD...")
        shutil.copy2(export_file, local_closure)
        print("    Importing to Store...")
        with open(local_closure, "r") as infile: subprocess.run(["nix-store", "--import"], stdin=infile, check=True)
        store_paths = sorted(list(Path("/nix/store").glob("*-airgap-tools")))
        if store_paths:
            newest = store_paths[-1]
            print(f"    Switching profile to: {newest.name}")
            run_cmd(["nix-env", "-i", str(newest)])
        local_closure.unlink()
    else: print("⚠️  No Nix closure found.")

    print("\n🛡️ [4/5] Priming Security...")
    clam_src = REPO_DIR / "clamdb"
    if clam_src.exists():
        for db in clam_src.glob("*.cvd"): shutil.copy2(db, "/var/lib/clamav/")
        subprocess.run(["chown", "clamav:clamav", "/var/lib/clamav/*.cvd"], shell=True)
    
    print("    Configuring USBGuard (Dynamic Sensing)...")
    policy = run_cmd(["usbguard", "generate-policy"], capture=True)
    with open("/etc/usbguard/rules.conf", "w") as f: f.write(policy)
    
    print("\n💾 [5/5] Configuring Backup...")
    with open("/etc/btrbk/btrbk.conf", "w") as f:
        f.write("transaction_log /var/log/btrbk.log\ntimestamp_format long\nstream_buffer 256m\nsnapshot_dir _btrbk_snapshots\nsnapshot_preserve 24h 6d 4w 3m\ntarget_preserve 24h 6d 4w 3m\nvolume /mnt/btrfs_root\n  subvolume @\n  target /mnt/backup_usb/rig_b_backups\n")
    
    for svc in ["clamav-freshclam", "usbguard", "btrbk.timer"]:
        subprocess.run(["systemctl", "enable", "--now", svc], stderr=subprocess.DEVNULL)

    print("\n✅ DEPLOY COMPLETE. Please Reboot.")

if __name__ == "__main__":
    if os.geteuid() != 0: sys.exit("❌ Must run as root (sudo)")
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["harvest", "deploy"], required=True)
    parser.add_argument("--config", help="Path to config.json (Harvest only)")
    args = parser.parse_args()
    if args.mode == "harvest":
        if not args.config: sys.exit("❌ --config required for harvest")
        harvest(args.config)
    elif args.mode == "deploy": deploy()
EOF

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
echo "   The automation tool is ready at: ~/airgap-workspace/airgap-bridge.py"
echo "   The config is ready at: ~/airgap-workspace/manifest.json"
echo ""
echo "➡️  NEXT STEP: Insert your USB drive and run:"
echo "    cd ~/airgap-workspace"
echo "    sudo python3 airgap-bridge.py --mode harvest --config manifest.json"
