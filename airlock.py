#!/usr/bin/env python3
import argparse, json, subprocess, os, shutil, sys, time, threading, itertools, hashlib
from pathlib import Path

# --- CONFIGURATION ---
APP_NAME = "AIRLOCK PROTOCOL"
VERSION = "v8.0.0 (Modular)"
MOUNT_POINT = Path("/mnt/usbc")
REPO_DIR = MOUNT_POINT / "repo"
NIX_TRANSFER_DIR = MOUNT_POINT / "nix_transfer"
CACHE_DIR = Path("/var/cache/pacman/pkg")
PACMAN_BIN = "/usr/bin/pacman"
NIX_CHANNEL_PATH = "nixpkgs=/nix/var/nix/profiles/per-user/root/channels/nixpkgs"

# --- TUI SYSTEM ---
class TUI:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

    @staticmethod
    def banner():
        os.system('clear')
        print(f"{TUI.BOLD}{TUI.CYAN}╔══════════════════════════════════════════════════════════════╗{TUI.ENDC}")
        print(f"{TUI.BOLD}{TUI.CYAN}║  {APP_NAME} {VERSION}                           ║{TUI.ENDC}")
        print(f"{TUI.BOLD}{TUI.CYAN}╚══════════════════════════════════════════════════════════════╝{TUI.ENDC}")
        print("")

    @staticmethod
    def step(msg): print(f"{TUI.BOLD}{TUI.BLUE}[*] {msg}{TUI.ENDC}")
    @staticmethod
    def success(msg): print(f"{TUI.BOLD}{TUI.GREEN}[✔] {msg}{TUI.ENDC}")
    @staticmethod
    def warn(msg): print(f"{TUI.BOLD}{TUI.WARNING}[!] {msg}{TUI.ENDC}")
    @staticmethod
    def fail(msg):
        print(f"{TUI.BOLD}{TUI.FAIL}[✘] FATAL: {msg}{TUI.ENDC}")
        sys.exit(1)
    @staticmethod
    def info(msg): print(f"    {msg}")
    
    @staticmethod
    def table_row(idx, col1, col2, col3):
        print(f"    {TUI.BOLD}[{idx}]{TUI.ENDC} {TUI.CYAN}{col1:<25}{TUI.ENDC} {col2:<15} {col3}")

class Spinner:
    def __init__(self, message="Processing..."):
        self.message = message
        self.stop_running = False
        self.thread = threading.Thread(target=self._animate)
    def _animate(self):
        for c in itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']):
            if self.stop_running: break
            sys.stdout.write(f'\r{TUI.CYAN}{c}{TUI.ENDC} {self.message}')
            sys.stdout.flush()
            time.sleep(0.1)
        sys.stdout.write('\r' + ' ' * (len(self.message) + 2) + '\r')
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self, exc_type, exc_value, tb):
        self.stop_running = True
        self.thread.join()

# --- CORE LOGIC ---
def run_cmd(cmd, cwd=None, capture=False, check=True, shell=False):
    try:
        res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE if capture else None, 
                             stderr=subprocess.PIPE if capture else None, text=True, check=check, shell=shell)
        return res.stdout.strip() if capture else None
    except subprocess.CalledProcessError as e:
        if not capture: print("")
        TUI.fail(f"Command failed: {cmd}\nError: {e.stderr if capture else 'See output above'}")

def load_configs(config_paths):
    """Merges multiple JSON config files into one master dictionary."""
    master_config = {"pacman_packages": [], "nix_packages": []}
    
    for path in config_paths:
        p = Path(path)
        if not p.exists():
            TUI.fail(f"Config file not found: {path}")
        
        with open(p, 'r') as f:
            data = json.load(f)
            master_config["pacman_packages"].extend(data.get("pacman_packages", []))
            master_config["nix_packages"].extend(data.get("nix_packages", []))
            TUI.info(f"Loaded: {p.name}")

    # Deduplicate
    master_config["pacman_packages"] = sorted(list(set(master_config["pacman_packages"])))
    master_config["nix_packages"] = sorted(list(set(master_config["nix_packages"])))
    return master_config

def auto_mount_usb():
    if MOUNT_POINT.is_mount(): return
    TUI.step("Scanning for USB drives...")
    lsblk_out = run_cmd(["lsblk", "-J", "-o", "NAME,TRAN,FSTYPE,MOUNTPOINT"], capture=True)
    data = json.loads(lsblk_out)
    usb_devices = []
    for device in data.get("blockdevices", []):
        if device.get("tran") == "usb":
            if "children" in device:
                for child in device["children"]:
                    if child.get("fstype"): usb_devices.append(f"/dev/{child['name']}")
            elif device.get("fstype"): usb_devices.append(f"/dev/{device['name']}")
    
    if len(usb_devices) == 0: TUI.fail("No USB drive detected.")
    if len(usb_devices) > 1: TUI.fail(f"Multiple USBs detected: {usb_devices}. Insert only ONE.")
    
    target = usb_devices[0]
    TUI.info(f"Detected: {target}")
    MOUNT_POINT.mkdir(parents=True, exist_ok=True)
    run_cmd(["mount", target, str(MOUNT_POINT)])

def calculate_sha256(filepath):
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

# --- HARVEST MODE ---
def harvest(config_paths):
    TUI.banner()
    if not os.path.exists("/nix/var/nix/profiles/per-user/root/channels/nixpkgs"):
        TUI.fail("Root Nix channel not found. Run 'sudo -i nix-channel --update' on Rig A.")

    auto_mount_usb()
    
    # Load and Merge Configs
    TUI.step("Loading Configurations")
    config = load_configs(config_paths)
    
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    NIX_TRANSFER_DIR.mkdir(parents=True, exist_ok=True)

    TUI.step("Harvesting Pacman Packages")
    installed = run_cmd([PACMAN_BIN, "-Qq"], capture=True).splitlines()
    targets = sorted(list(set(installed + config['pacman_packages'])))
    
    with Spinner(f"Processing {len(targets)} packages..."):
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
                subprocess.run([PACMAN_BIN, "-Syw", "--cachedir", str(REPO_DIR), "--noconfirm", pkg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    TUI.step("Building Repository Database")
    for db in REPO_DIR.glob("localrepo.db*"): db.unlink()
    pkg_files = list(REPO_DIR.glob("*.pkg.tar.zst"))
    run_cmd(["repo-add", "-n", "localrepo.db.tar.gz"] + [str(p) for p in pkg_files], cwd=REPO_DIR)

    TUI.step("Copying ClamAV DB")
    clam_dest = REPO_DIR / "clamdb"
    clam_dest.mkdir(exist_ok=True)
    for db in Path("/var/lib/clamav").glob("*.cvd"): shutil.copy2(db, clam_dest)

    TUI.step("Harvesting Nix Environment")
    nix_file = Path("/tmp/generated_tools.nix")
    pkgs_string = " ".join(config['nix_packages'])
    nix_content = f"""
{{ pkgs ? import <nixpkgs> {{ config = {{ allowUnfree = true; }}; overlays = []; }} }}:
pkgs.buildEnv {{
  name = "airgap-tools";
  paths = with pkgs; [ {pkgs_string} ];
}}
"""
    with open(nix_file, 'w') as f: f.write(nix_content)
    
    with Spinner("Building Nix Closure (This takes time)..."):
        drv_path = run_cmd(["nix-build", str(nix_file), "-I", NIX_CHANNEL_PATH], capture=True)
    
    with Spinner("Exporting to USB..."):
        export_file = NIX_TRANSFER_DIR / "tools.closure"
        with open(export_file, "wb") as outfile:
            reqs = run_cmd(["nix-store", "-qR", drv_path], capture=True).splitlines()
            subprocess.run(["nix-store", "--export"] + reqs, stdout=outfile, check=True)
    
    # INTEGRITY: Generate Hash
    with Spinner("Generating Integrity Hash..."):
        file_hash = calculate_sha256(export_file)
        with open(export_file.with_suffix(".sha256"), "w") as f: f.write(file_hash)

    TUI.step("Self-Replication")
    shutil.copy2(sys.argv[0], MOUNT_POINT / "airlock.py")
    run_cmd(["chmod", "+x", MOUNT_POINT / "airlock.py"])

    TUI.step("Syncing to Disk")
    with Spinner("Flushing buffers (DO NOT REMOVE USB)..."):
        run_cmd(["sync"])
    
    TUI.success("HARVEST COMPLETE. Safe to remove USB.")

# --- DEPLOY MODE ---
def deploy():
    TUI.banner()
    auto_mount_usb()

    TUI.step("Atomic OS Sync")
    conf_file = Path("/tmp/offline_pacman.conf")
    with open(conf_file, "w") as f:
        f.write(f"[options]\nHoldPkg = pacman glibc\nArchitecture = auto\nSigLevel = Optional TrustAll\nLocalFileSigLevel = Optional\n[localrepo]\nServer = file://{REPO_DIR}\n")
    
    with Spinner("Syncing System State..."):
        run_cmd([PACMAN_BIN, "--config", str(conf_file), "-Syyu", "--noconfirm", "--overwrite", "*"])
        run_cmd([PACMAN_BIN, "--config", str(conf_file), "-S", "--noconfirm", "--needed", "nix", "clamav", "usbguard", "rkhunter", "lynis", "btrbk", "mbuffer"])

    TUI.step("Configuring Nix")
    subprocess.run(["groupadd", "-r", "nix-users"], stderr=subprocess.DEVNULL)
    user = os.environ.get('SUDO_USER', os.environ.get('USER'))
    if user: subprocess.run(["gpasswd", "-a", user, "nix-users"], stderr=subprocess.DEVNULL)
    os.makedirs("/etc/nix", exist_ok=True)
    with open("/etc/nix/nix.conf", "w") as f: f.write("trusted-users = root @wheel\nrequire-sigs = false\n")
    run_cmd(["systemctl", "enable", "--now", "nix-daemon"])
    
    TUI.step("Importing Nix Environment")
    export_file = NIX_TRANSFER_DIR / "tools.closure"
    hash_file = export_file.with_suffix(".sha256")
    local_closure = Path("/tmp/tools.closure")
    
    if export_file.exists():
        if hash_file.exists():
            with Spinner("Verifying Integrity (SHA256)..."):
                expected = hash_file.read_text().strip()
                actual = calculate_sha256(export_file)
                if expected != actual: TUI.fail("USB Corruption Detected. Hash mismatch.")
            TUI.info("Integrity Verified.")
        else: TUI.warn("No hash file found. Skipping verification.")

        with Spinner("Copying to SSD..."):
            shutil.copy2(export_file, local_closure)
        with Spinner("Importing to Store..."):
            with open(local_closure, "r") as infile: subprocess.run(["nix-store", "--import"], stdin=infile, check=True)
        
        store_paths = sorted(list(Path("/nix/store").glob("*-airgap-tools")))
        if store_paths:
            newest = store_paths[-1]
            TUI.info(f"Switching profile to: {newest.name}")
            run_cmd(["nix-env", "-i", str(newest)])
        local_closure.unlink()
    else: TUI.warn("No Nix closure found.")

    TUI.step("Priming Security")
    clam_src = REPO_DIR / "clamdb"
    if clam_src.exists():
        os.makedirs("/var/lib/clamav", exist_ok=True)
        for db in clam_src.glob("*.cvd"): shutil.copy2(db, "/var/lib/clamav/")
        run_cmd("chown -R clamav:clamav /var/lib/clamav", shell=True)
    
    TUI.step("Configuring USBGuard (Dynamic Sensing)")
    policy = run_cmd(["usbguard", "generate-policy"], capture=True)
    os.makedirs("/etc/usbguard", exist_ok=True)
    with open("/etc/usbguard/rules.conf", "w") as f: f.write(policy)
    
    TUI.step("Configuring Backup")
    btrfs_root_mount = Path("/mnt/btrfs_root")
    btrfs_root_mount.mkdir(exist_ok=True)
    findmnt = run_cmd(["findmnt", "-n", "-o", "SOURCE", "/"], capture=True)
    root_dev = findmnt.split("[")[0]
    subprocess.run(["mount", "-o", "subvolid=5", root_dev, str(btrfs_root_mount)], stderr=subprocess.DEVNULL)

    with open("/etc/btrbk/btrbk.conf", "w") as f:
        f.write(f"transaction_log /var/log/btrbk.log\ntimestamp_format long\nstream_buffer 256m\nsnapshot_dir _btrbk_snapshots\nsnapshot_preserve 24h 6d 4w 3m\ntarget_preserve 24h 6d 4w 3m\nvolume {btrfs_root_mount}\n  subvolume @\n  target /mnt/backup_usb/rig_b_backups\n")
    
    for svc in ["clamav-freshclam", "usbguard", "btrbk.timer"]:
        subprocess.run(["systemctl", "enable", "--now", svc], stderr=subprocess.DEVNULL)

    TUI.success("DEPLOY COMPLETE. Please Reboot.")

def init_backup(device):
    TUI.banner()
    TUI.warn(f"This will WIPE ALL DATA on {device} and format as LUKS+BTRFS.")
    confirm = input("    Type 'YES' to proceed: ")
    if confirm != "YES": sys.exit("Aborted.")

    TUI.step(f"Encrypting {device}...")
    subprocess.run(["cryptsetup", "luksFormat", device], check=True)
    subprocess.run(["cryptsetup", "open", device, "backup_crypt"], check=True)

    TUI.step("Formatting BTRFS...")
    run_cmd(["mkfs.btrfs", "-f", "-L", "RIG_B_BACKUP", "/dev/mapper/backup_crypt"])

    TUI.step("Mounting...")
    Path("/mnt/backup_usb").mkdir(parents=True, exist_ok=True)
    run_cmd(["mount", "/dev/mapper/backup_crypt", "/mnt/backup_usb"])
    TUI.success("Backup Drive Ready.")

if __name__ == "__main__":
    if os.geteuid() != 0: sys.exit("❌ Must run as root (sudo)")
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["harvest", "deploy", "init-backup"], required=True)
    parser.add_argument("--config", nargs='+', help="Path(s) to config.json (Harvest only)")
    parser.add_argument("--device", help="Target device for init-backup")
    
    args = parser.parse_args()

    if args.mode == "harvest":
        if not args.config: sys.exit("❌ --config required for harvest")
        harvest(args.config)
    elif args.mode == "deploy":
        deploy()
    elif args.mode == "init-backup":
        if not args.device: sys.exit("❌ --device required")
        init_backup(args.device)
