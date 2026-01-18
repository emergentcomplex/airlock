cat <<'EOF' > airlock_pkg/airlock/ops.py
import os
import shutil
import subprocess
from pathlib import Path
from . import system, config, ui

# --- HARVEST OPERATIONS (RIG A) ---
def harvest(config_paths):
    system.check_root()
    
    # Safety Check: Ensure we are on Rig A (Has Channels)
    if not os.path.exists("/nix/var/nix/profiles/per-user/root/channels/nixpkgs"):
        ui.fail("Root Nix channel not found. Run 'sudo -i nix-channel --update' on Rig A.")

    system.auto_mount_usb()
    
    # Cleanup old transfers to prevent stale data
    if system.NIX_TRANSFER_DIR.exists():
        ui.step("Cleaning old transfers...")
        shutil.rmtree(system.NIX_TRANSFER_DIR)
    
    system.REPO_DIR.mkdir(parents=True, exist_ok=True)
    system.NIX_TRANSFER_DIR.mkdir(parents=True, exist_ok=True)

    # 1. PACMAN HARVEST
    ui.step("Harvesting Pacman Packages")
    merged_config = config.load_merged_configs(config_paths)
    
    # Get installed packages to ensure atomic OS sync
    installed = system.run_cmd([system.PACMAN_BIN, "-Qq"], capture=True).splitlines()
    targets = sorted(list(set(installed + merged_config['pacman_packages'])))
    
    with ui.Spinner(f"Processing {len(targets)} system packages..."):
        for pkg in targets:
            # Optimization: Check USB first
            try:
                info = system.run_cmd([system.PACMAN_BIN, "-Q", pkg], capture=True)
                version = info.split()[1]
            except:
                version = "*" # Package in config but not installed locally

            usb_matches = list(system.REPO_DIR.glob(f"{pkg}-{version}-*.pkg.tar.zst"))
            if usb_matches:
                sig = usb_matches[0].with_suffix(".pkg.tar.zst.sig")
                if sig.exists(): continue # Skip if valid on USB
            
            # Check Local Cache
            matches = list(system.CACHE_DIR.glob(f"{pkg}-{version}-*.pkg.tar.zst"))
            if matches:
                latest = sorted(matches)[-1]
                dest = system.REPO_DIR / latest.name
                if not dest.exists():
                    shutil.copy2(latest, dest)
                    sig = latest.with_suffix(".pkg.tar.zst.sig")
                    if sig.exists(): shutil.copy2(sig, system.REPO_DIR / sig.name)
            else:
                # Download
                try:
                    system.run_cmd([system.PACMAN_BIN, "-Syw", "--cachedir", str(system.REPO_DIR), "--noconfirm", pkg], capture=True)
                except: pass # Ignore individual failures (e.g. AUR packages)

    # 2. REPO DB
    ui.step("Building Repository Database")
    for db in system.REPO_DIR.glob("localrepo.db*"): db.unlink()
    pkg_files = list(system.REPO_DIR.glob("*.pkg.tar.zst"))
    system.run_cmd(["repo-add", "-n", "localrepo.db.tar.gz"] + [str(p) for p in pkg_files], cwd=system.REPO_DIR)

    # 3. CLAMAV DB
    ui.step("Copying ClamAV DB")
    clam_dest = system.REPO_DIR / "clamdb"
    clam_dest.mkdir(exist_ok=True)
    for db in Path("/var/lib/clamav").glob("*.cvd"): shutil.copy2(db, clam_dest)

    # 4. NIX HARVEST (Layered)
    ui.step("Harvesting Nix Layers")
    for path in config_paths:
        cfg = config.load_config(path)
        layer_name = Path(path).stem
        nix_pkgs = cfg.get('nix_packages', [])
        
        if not nix_pkgs:
            ui.info(f"Skipping {layer_name} (No Nix packages)")
            continue

        ui.info(f"Processing Layer: {layer_name}")
        nix_file = Path(f"/tmp/generated_{layer_name}.nix")
        pkgs_string = " ".join(nix_pkgs)
        
        # Hermetic Config
        nix_content = f"""
{{ pkgs ? import <nixpkgs> {{ config = {{ allowUnfree = true; }}; overlays = []; }} }}:
pkgs.buildEnv {{
  name = "airgap-{layer_name}";
  paths = with pkgs; [ {pkgs_string} ];
}}
"""
        with open(nix_file, 'w') as f: f.write(nix_content)
        
        with ui.Spinner(f"  Building {layer_name}..."):
            try:
                drv_path = system.run_cmd(["nix-build", str(nix_file), "-I", system.NIX_CHANNEL_PATH], capture=True)
            except Exception as e:
                ui.fail(f"Nix Build Failed for {layer_name}", str(e))
        
        export_file = system.NIX_TRANSFER_DIR / f"{layer_name}.closure"
        hash_file = export_file.with_suffix(".sha256")
        
        system.check_disk_space(system.MOUNT_POINT, required_gb=1.0)

        with ui.Spinner(f"  Exporting {layer_name} to USB..."):
            try:
                # Use Python file handling to avoid shell redirection issues
                with open(export_file, "wb") as outfile:
                    reqs = system.run_cmd(["nix-store", "-qR", drv_path], capture=True).splitlines()
                    subprocess.run(["nix-store", "--export"] + reqs, stdout=outfile, check=True)
            except Exception as e:
                ui.fail(f"Export Failed for {layer_name}", str(e))
        
        file_hash = system.calculate_sha256(export_file)
        with open(hash_file, "w") as f: f.write(file_hash)
        
        size_mb = export_file.stat().st_size / (1024 * 1024)
        ui.info(f"  Layer {layer_name} Complete: {size_mb:.2f} MB")

    # 5. SELF REPLICATION
    ui.step("Self-Replication")
    # Copy the entire package directory to USB
    src_dir = Path(os.getcwd())
    dst_dir = system.MOUNT_POINT / "airlock_pkg"
    if dst_dir.exists(): shutil.rmtree(dst_dir)
    shutil.copytree(src_dir, dst_dir)
    
    # Also copy the entry point script for convenience
    shutil.copy2(src_dir / "main.py", system.MOUNT_POINT / "airlock.py")
    system.run_cmd(["chmod", "+x", system.MOUNT_POINT / "airlock.py"])

    ui.step("Syncing to Disk")
    with ui.Spinner("Flushing buffers (DO NOT REMOVE USB)..."):
        system.sync_disk()
    
    ui.success("HARVEST COMPLETE. Safe to remove USB.")

# --- DEPLOY OPERATIONS (RIG B) ---
def deploy(gc=False):
    system.check_root()
    system.auto_mount_usb()
    ui.banner("DEPLOY")

    # 1. ATOMIC OS SYNC
    ui.step("Atomic OS Sync")
    conf_file = Path("/tmp/offline_pacman.conf")
    with open(conf_file, "w") as f:
        f.write(f"[options]\nHoldPkg = pacman glibc\nArchitecture = auto\nSigLevel = Optional TrustAll\nLocalFileSigLevel = Optional\n[localrepo]\nServer = file://{system.REPO_DIR}\n")
    
    with ui.Spinner("Syncing System State..."):
        try:
            system.run_cmd([system.PACMAN_BIN, "--config", str(conf_file), "-Syyu", "--noconfirm", "--overwrite", "*"])
            # Force install base tools
            system.run_cmd([system.PACMAN_BIN, "--config", str(conf_file), "-S", "--noconfirm", "--needed", "nix", "clamav", "usbguard", "rkhunter", "lynis", "btrbk", "mbuffer"])
        except Exception as e:
            ui.fail("Pacman Sync Failed", str(e))

    # 2. NIX CONFIG
    ui.step("Configuring Nix")
    subprocess.run(["groupadd", "-r", "nix-users"], stderr=subprocess.DEVNULL)
    user = os.environ.get('SUDO_USER', os.environ.get('USER'))
    if user: subprocess.run(["gpasswd", "-a", user, "nix-users"], stderr=subprocess.DEVNULL)
    
    os.makedirs("/etc/nix", exist_ok=True)
    with open("/etc/nix/nix.conf", "w") as f: f.write("trusted-users = root @wheel\nrequire-sigs = false\n")
    system.run_cmd(["systemctl", "enable", "--now", "nix-daemon"])
    
    # 3. NIX IMPORT
    ui.step("Importing Nix Layers")
    closures = sorted(list(system.NIX_TRANSFER_DIR.glob("*.closure")))
    paths_to_install = []

    if not closures:
        ui.warn("No Nix closures found.")
    else:
        for closure in closures:
            layer_name = closure.stem
            hash_file = closure.with_suffix(".sha256")
            local_closure = Path(f"/tmp/{closure.name}")
            
            ui.info(f"Processing Layer: {layer_name}")
            
            if hash_file.exists():
                with ui.Spinner("  Verifying Integrity..."):
                    expected = hash_file.read_text().strip()
                    actual = system.calculate_sha256(closure)
                    if expected != actual: ui.fail(f"Corruption detected in {layer_name}")
            
            with ui.Spinner("  Importing..."):
                try:
                    shutil.copy2(closure, local_closure)
                    with open(local_closure, "r") as infile: 
                        subprocess.run(["nix-store", "--import"], stdin=infile, check=True)
                    local_closure.unlink()
                except Exception as e:
                    ui.fail(f"Import Failed for {layer_name}", str(e))
            
            # Find store path
            store_paths = sorted(list(Path("/nix/store").glob(f"*-airgap-{layer_name}")))
            if store_paths:
                newest = store_paths[-1]
                paths_to_install.append(str(newest))
                ui.info(f"  Queued: {newest.name}")

    # 4. ATOMIC INSTALL
    if paths_to_install:
        ui.step("Installing Nix Profiles (Atomic)")
        # Install to default profile so all users see it
        cmd = ["nix-env", "-p", system.NIX_PROFILE_PATH, "-i"] + paths_to_install
        with ui.Spinner("Linking Profiles..."):
            system.run_cmd(cmd)
        ui.success("All layers installed.")

    if gc:
        ui.step("Garbage Collection")
        with ui.Spinner("Removing old generations..."):
            system.run_cmd(["nix-collect-garbage", "-d"])

    # 5. SECURITY
    ui.step("Priming Security")
    clam_src = system.REPO_DIR / "clamdb"
    if clam_src.exists():
        os.makedirs("/var/lib/clamav", exist_ok=True)
        for db in clam_src.glob("*.cvd"): shutil.copy2(db, "/var/lib/clamav/")
        system.run_cmd("chown -R clamav:clamav /var/lib/clamav", shell=True)
    
    ui.step("Configuring USBGuard")
    policy = system.run_cmd(["usbguard", "generate-policy"], capture=True)
    os.makedirs("/etc/usbguard", exist_ok=True)
    with open("/etc/usbguard/rules.conf", "w") as f: f.write(policy)
    
    ui.step("Configuring Backup")
    btrfs_root_mount = Path("/mnt/btrfs_root")
    btrfs_root_mount.mkdir(exist_ok=True)
    findmnt = system.run_cmd(["findmnt", "-n", "-o", "SOURCE", "/"], capture=True)
    root_dev = findmnt.split("[")[0]
    subprocess.run(["mount", "-o", "subvolid=5", root_dev, str(btrfs_root_mount)], stderr=subprocess.DEVNULL)

    with open("/etc/btrbk/btrbk.conf", "w") as f:
        f.write(f"transaction_log /var/log/btrbk.log\ntimestamp_format long\nstream_buffer 256m\nsnapshot_dir _btrbk_snapshots\nsnapshot_preserve 24h 6d 4w 3m\ntarget_preserve 24h 6d 4w 3m\nvolume {btrfs_root_mount}\n  subvolume @\n  target /mnt/backup_usb/rig_b_backups\n")
    
    for svc in ["clamav-freshclam", "usbguard", "btrbk.timer"]:
        subprocess.run(["systemctl", "enable", "--now", svc], stderr=subprocess.DEVNULL)

    ui.success("DEPLOY COMPLETE. Please Reboot.")

# --- SEARCH MODE ---
def search(query, config_path):
    ui.banner("SEARCH")
    if not os.path.exists("/nix/var/nix/profiles/per-user/root/channels/nixpkgs"):
        ui.fail("Root Nix channel not found.")

    cmd = ["nix-env", "-I", system.NIX_CHANNEL_PATH, "-qaP", f".*{query}.*", "--description"]
    with ui.Spinner("Querying Nix Universe..."):
        try: output = system.run_cmd(cmd, capture=True, check=False)
        except: output = ""

    if not output:
        ui.warn("No packages found.")
        return

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
            ui.table_row(idx+1, clean_name, version, desc)

    print("")
    selection = input(f"Enter IDs to add (e.g. '1 3') or Enter to quit: ")
    
    to_add = []
    for s in selection.split():
        if s.isdigit():
            i = int(s) - 1
            if 0 <= i < len(results): to_add.append(results[i])
    
    if to_add:
        for pkg in to_add:
            config.add_package(config_path, "nix", pkg)

# --- INGEST MODE ---
def ingest(file_path):
    ui.banner("INGEST")
    system.auto_mount_usb()
    target = Path(file_path)
    if not target.exists(): ui.fail(f"File not found: {target}")
    
    ui.step(f"Processing Artifact: {target.name}")
    with ui.Spinner("Scanning for Malware..."):
        try: system.run_cmd(["clamscan", "--no-summary", str(target)])
        except: ui.fail("Malware Detected! Ingestion Aborted.")
    ui.info("ClamAV: Clean")

    mime = system.run_cmd(["file", "--mime-type", "-b", str(target)], capture=True)
    ui.info(f"Detected Type: {mime}")
    if "executable" in mime or "x-dosexec" in mime:
        if target.suffix not in ['.exe', '.bin', '.elf']:
            ui.fail(f"Type Mismatch! Executable disguised as {target.suffix}")

    safe_zone = Path.home() / "Safe_Zone"
    safe_zone.mkdir(exist_ok=True)
    shutil.copy2(target, safe_zone / target.name)
    ui.success(f"Imported to {safe_zone}")

# --- BACKUP INIT ---
def init_backup(device):
    ui.banner("INIT BACKUP")
    ui.warn(f"This will WIPE ALL DATA on {device} and format as LUKS+BTRFS.")
    if not ui.confirm("Proceed?"): sys.exit("Aborted.")

    ui.step(f"Encrypting {device}...")
    subprocess.run(["cryptsetup", "luksFormat", device], check=True)
    subprocess.run(["cryptsetup", "open", device, "backup_crypt"], check=True)

    ui.step("Formatting BTRFS...")
    system.run_cmd(["mkfs.btrfs", "-f", "-L", "RIG_B_BACKUP", "/dev/mapper/backup_crypt"])

    ui.step("Mounting...")
    Path("/mnt/backup_usb").mkdir(parents=True, exist_ok=True)
    system.run_cmd(["mount", "/dev/mapper/backup_crypt", "/mnt/backup_usb"])
    ui.success("Backup Drive Ready.")
EOF
