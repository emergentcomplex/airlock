#!/usr/bin/env python3
import argparse, json, subprocess, os, shutil, sys, time, threading, itertools, hashlib
from pathlib import Path

# --- CONFIGURATION ---
APP_NAME = "AIRLOCK PROTOCOL"
VERSION = "v11.0.0 (Canonical)"
# UNIFIED MOUNT POINT: All scripts must use this path.
MOUNT_POINT = Path("/mnt/airlock")
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
        print(f"{TUI.BOLD}{TUI.CYAN}║  {APP_NAME} {VERSION}                         ║{TUI.ENDC}")
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

def load_config(config_path):
    if not Path(config_path).exists():
        # Create default if missing
        default_conf = {
            "comment": "Master Air-Gap Configuration",
            "pacman_packages": ["clamav", "rkhunter", "lynis", "binwalk", "usbguard", "btrbk", "nix", "mbuffer"],
            "nix_packages": ["hello", "python3", "bashInteractive"]
        }
        with open(config_path, 'w') as f: json.dump(default_conf, f, indent=4)
        return default_conf
    with open(config_path, 'r') as f: return json.load(f)

def load_configs(config_paths):
    master_config = {"pacman_packages": [], "nix_packages": []}
    for path in config_paths:
        p = Path(path)
        if not p.exists(): TUI.fail(f"Config file not found: {path}")
        with open(p, 'r') as f:
            data = json.load(f)
            master_config["pacman_packages"].extend(data.get("pacman_packages", []))
            master_config["nix_packages"].extend(data.get("nix_packages", []))
            TUI.info(f"Loaded: {p.name}")
    master_config["pacman_packages"] = sorted(list(set(master_config["pacman_packages"])))
    master_config["nix_packages"] = sorted(list(set(master_config["nix_packages"])))
    return master_config

def save_config(config_path, data):
    with open(config_path, 'w') as f: json.dump(data, f, indent=4)

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

# --- PACKAGE MANAGEMENT ---
def interactive_search(config_path):
    TUI.banner()
    if not os.path.exists("/nix/var/nix/profiles/per-user/root/channels/nixpkgs"):
        TUI.fail("Root Nix channel not found. Run 'sudo -i nix-channel --update' on Rig A.")

    while True:
        print(f"\n{TUI.BOLD}--- Interactive Package Search ---{TUI.ENDC}")
        query = input(f"Enter search term (or 'q' to quit, 'l' to list current): ").strip()
        if query.lower() == 'q': break
        if query.lower() == 'l':
            list_manifest(config_path)
            continue
        if not query: continue

        cmd = ["nix-env", "-I", NIX_CHANNEL_PATH, "-qaP", f".*{query}.*", "--description"]
        with Spinner("Querying Nix Universe..."):
            try: output = run_cmd(cmd, capture=True, check=False)
            except: output = ""

        if not output:
            TUI.warn("No packages found.")
            continue

        lines = output.splitlines()
        results = []
        print(f"\n    {'ID':<4} {'PACKAGE NAME':<30} {'VERSION':<15} {'DESCRIPTION'}")
        print(f"    {'-'*4} {'-'*30} {'-'*15} {'-'*30}")

        for idx, line in enumerate(lines[:20]):
            parts = line.split(maxsplit=2)
            if len(parts) >= 2:
                raw_name = parts[0]
                version = parts[1]
                desc = parts[2] if len(parts) > 2 else ""
                clean_name = raw_name.replace("nixpkgs.", "")
                results.append(clean_name)
                if len(desc) > 50: desc = desc[:47] + "..."
                TUI.table_row(idx+1, clean_name, version, desc)

        if len(lines) > 20: print(f"    ... and {len(lines) - 20} more results.")
        print("")
        selection = input(f"Enter IDs to add (e.g. '1 3 5') or 'a' for all visible: ")
        
        to_add = []
        if selection.lower() == 'a': to_add = results
        else:
            for s in selection.split():
                if s.isdigit():
                    i = int(s) - 1
                    if 0 <= i < len(results): to_add.append(results[i])
        
        if to_add: batch_add(to_add, config_path)

def batch_add(packages, config_path):
    data = load_config(config_path)
    added_count = 0
    for pkg in packages:
        if pkg not in data['nix_packages']:
            data['nix_packages'].append(pkg)
            added_count += 1
            print(f"    + Added: {pkg}")
        else: print(f"    . Skipping {pkg} (Already exists)")
    
    if added_count > 0:
        data['nix_packages'].sort()
        save_config(config_path, data)
        TUI.success(f"Saved {added_count} new packages to manifest.")

def batch_remove(packages, config_path):
    data = load_config(config_path)
    removed_count = 0
    for pkg in packages:
        if pkg in data['nix_packages']:
            data['nix_packages'].remove(pkg)
            removed_count += 1
            print(f"    - Removed: {pkg}")
        else: TUI.warn(f"Package '{pkg}' not found in manifest.")
    
    if removed_count > 0:
        save_config(config_path, data)
        TUI.success(f"Removed {removed_count} packages.")

def list_manifest(config_path):
    data = load_config(config_path)
    print(f"\n{TUI.BOLD}Current Nix Packages in Manifest:{TUI.ENDC}")
    for pkg in data['nix_packages']: print(f"  - {pkg}")
    print(f"Total: {len(data['nix_packages'])}")

# --- HARVEST MODE ---
def harvest(config_paths):
    TUI.banner()
    if not os.path.exists("/nix/var/nix/profiles/per-user/root/channels/nixpkgs"):
        TUI.fail("Root Nix channel not found. Run 'sudo -i nix-channel --update' on Rig A.")

    auto_mount_usb()
    
    # CLEANUP: Remove old transfers to save space
    if NIX_TRANSFER_DIR.exists():
        TUI.step("Cleaning old transfers...")
        shutil.rmtree(NIX_TRANSFER_DIR)
    
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    NIX_TRANSFER_DIR.mkdir(parents=True, exist_ok=True)

    # 1. PACMAN HARVEST
    TUI.step("Harvesting Pacman Packages")
    all_pacman_pkgs = []
    for path in config_paths:
        cfg = load_config(path)
        all_pacman_pkgs.extend(cfg.get('pacman_packages', []))
    
    installed = run_cmd([PACMAN_BIN, "-Qq"], capture=True).splitlines()
    targets = sorted(list(set(installed + all_pacman_pkgs)))
    
    with Spinner(f"Processing {len(targets)} system packages..."):
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

    # 2. NIX HARVEST (Layered)
    TUI.step("Harvesting Nix Layers")
    
    for path in config_paths:
        cfg_path = Path(path)
        cfg = load_config(cfg_path)
        layer_name = cfg_path.stem
        nix_pkgs = cfg.get('nix_packages', [])
        
        if not nix_pkgs:
            TUI.info(f"Skipping {layer_name} (No Nix packages)")
            continue

        TUI.info(f"Processing Layer: {layer_name}")
        nix_file = Path(f"/tmp/generated_{layer_name}.nix")
        pkgs_string = " ".join(nix_pkgs)
        nix_content = f"""
{{ pkgs ? import <nixpkgs> {{ config = {{ allowUnfree = true; }}; overlays = []; }} }}:
pkgs.buildEnv {{
  name = "airgap-{layer_name}";
  paths = with pkgs; [ {pkgs_string} ];
}}
"""
        with open(nix_file, 'w') as f: f.write(nix_content)
        
        with Spinner(f"  Building {layer_name}..."):
            drv_path = run_cmd(["nix-build", str(nix_file), "-I", NIX_CHANNEL_PATH], capture=True)
        
        export_file = NIX_TRANSFER_DIR / f"{layer_name}.closure"
        hash_file = export_file.with_suffix(".sha256")
        
        with Spinner(f"  Exporting {layer_name} to USB..."):
            with open(export_file, "wb") as outfile:
                reqs = run_cmd(["nix-store", "-qR", drv_path], capture=True).splitlines()
                subprocess.run(["nix-store", "--export"] + reqs, stdout=outfile, check=True)
        
        file_hash = calculate_sha256(export_file)
        with open(hash_file, "w") as f: f.write(file_hash)
        
        size_mb = export_file.stat().st_size / (1024 * 1024)
        TUI.info(f"  Layer {layer_name} Complete: {size_mb:.2f} MB")

    TUI.step("Self-Replication")
    shutil.copy2(sys.argv[0], MOUNT_POINT / "airlock.py")
    run_cmd(["chmod", "+x", MOUNT_POINT / "airlock.py"])

    TUI.step("Syncing to Disk")
    with Spinner("Flushing buffers (DO NOT REMOVE USB)..."):
        run_cmd(["sync"])
    
    TUI.success("HARVEST COMPLETE. Safe to remove USB.")

# --- DEPLOY MODE ---
def deploy(gc=False):
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
    
    TUI.step("Importing Nix Layers")
    closures = sorted(list(NIX_TRANSFER_DIR.glob("*.closure")))
    
    if not closures:
        TUI.warn("No Nix closures found.")
    else:
        for closure in closures:
            layer_name = closure.stem
            hash_file = closure.with_suffix(".sha256")
            local_closure = Path(f"/tmp/{closure.name}")
            
            TUI.info(f"Processing Layer: {layer_name}")
            
            if hash_file.exists():
                with Spinner("  Verifying Integrity..."):
                    expected = hash_file.read_text().strip()
                    actual = calculate_sha256(closure)
                    if expected != actual: TUI.fail(f"Corruption detected in {layer_name}")
            
            with Spinner("  Importing..."):
                shutil.copy2(closure, local_closure)
                with open(local_closure, "r") as infile: 
                    subprocess.run(["nix-store", "--import"], stdin=infile, check=True)
                local_closure.unlink()
            
            store_paths = sorted(list(Path("/nix/store").glob(f"*-airgap-{layer_name}")))
            if store_paths:
                newest = store_paths[-1]
                TUI.info(f"  Installing Profile: {newest.name}")
                run_cmd(["nix-env", "-i", str(newest)])
            else:
                TUI.warn(f"  Could not find store path for {layer_name}")

    if gc:
        TUI.step("Garbage Collection")
        with Spinner("Removing old generations..."):
            run_cmd(["nix-collect-garbage", "-d"])
        TUI.success("Disk space reclaimed.")

    TUI.step("Priming Security")
    clam_src = REPO_DIR / "clamdb"
    if clam_src.exists():
        os.makedirs("/var/lib/clamav", exist_ok=True)
        for db in clam_src.glob("*.cvd"): shutil.copy2(db, "/var/lib/clamav/")
        run_cmd("chown -R clamav:clamav /var/lib/clamav", shell=True)
    
    TUI.step("Configuring USBGuard")
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

# --- INGEST MODE ---
def ingest(file_path):
    TUI.banner()
    auto_mount_usb()
    target = Path(file_path)
    if not target.exists(): TUI.fail(f"File not found: {target}")
    
    TUI.step(f"Processing Artifact: {target.name}")
    with Spinner("Scanning for Malware..."):
        try: run_cmd(["clamscan", "--no-summary", str(target)])
        except: TUI.fail("Malware Detected! Ingestion Aborted.")
    TUI.info("ClamAV: Clean")

    mime = run_cmd(["file", "--mime-type", "-b", str(target)], capture=True)
    TUI.info(f"Detected Type: {mime}")
    if "executable" in mime or "x-dosexec" in mime:
        if target.suffix not in ['.exe', '.bin', '.elf']:
            TUI.fail(f"Type Mismatch! Executable disguised as {target.suffix}")

    safe_zone = Path.home() / "Safe_Zone"
    safe_zone.mkdir(exist_ok=True)
    shutil.copy2(target, safe_zone / target.name)
    TUI.success(f"Imported to {safe_zone}")

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
    parser.add_argument("--mode", choices=["harvest", "deploy", "init-backup", "search", "ingest"], required=True)
    parser.add_argument("--config", nargs='+', help="Path(s) to config.json (Harvest only)", default=["manifest.json"])
    parser.add_argument("--device", help="Target device for init-backup")
    parser.add_argument("--file", help="File path for ingest")
    parser.add_argument("--add", nargs='+', help="Add packages to manifest")
    parser.add_argument("--remove", nargs='+', help="Remove packages from manifest")
    parser.add_argument("--list", action="store_true", help="List manifest packages")
    parser.add_argument("--query", help="Search term")
    parser.add_argument("--gc", action="store_true", help="Run Garbage Collection during deploy")
    
    args = parser.parse_args()

    # Config Management (Defaults to first config if multiple)
    target_config = args.config[0] if isinstance(args.config, list) else args.config

    if args.add:
        batch_add(args.add, target_config)
        sys.exit(0)
    if args.remove:
        batch_remove(args.remove, target_config)
        sys.exit(0)
    if args.list:
        list_manifest(target_config)
        sys.exit(0)

    if args.mode == "harvest": harvest(args.config)
    elif args.mode == "deploy": deploy(gc=args.gc)
    elif args.mode == "init-backup":
        if not args.device: sys.exit("❌ --device required")
        init_backup(args.device)
    elif args.mode == "search":
        if not args.query: sys.exit("❌ --query required")
        interactive_search(target_config)
    elif args.mode == "ingest":
        if not args.file: sys.exit("❌ --file required")
        ingest(args.file)