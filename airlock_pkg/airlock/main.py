cat <<'EOF' > airlock_pkg/airlock/main.py
#!/usr/bin/env python3
import sys
import argparse
import glob
import os
from pathlib import Path

# Handle imports whether run as package or script
try:
    from . import ui, system, ops, config
except ImportError:
    import ui, system, ops, config

VERSION = "1.0.0"

def get_configs():
    """Auto-discovers config files in local or USB directories."""
    # Priority 1: USB Configs
    usb_configs = glob.glob(str(system.MOUNT_POINT / "configs/*.json"))
    if usb_configs: return sorted(usb_configs)
    
    # Priority 2: Local Configs
    local_configs = glob.glob("configs/*.json")
    if local_configs: return sorted(local_configs)
    
    # Priority 3: Default manifest
    if Path("manifest.json").exists(): return ["manifest.json"]
    
    return []

def interactive_menu():
    while True:
        ui.banner(VERSION)
        choice = ui.menu({
            "1": "Harvest (Rig A -> USB)",
            "2": "Deploy (USB -> Rig B)",
            "3": "Search Packages (Add to Config)",
            "4": "Ingest Artifact (File -> Safe Zone)",
            "5": "Initialize Backup Drive",
            "6": "System Maintenance (GC)",
            "q": "Quit"
        })

        if choice == "q":
            sys.exit(0)
        
        elif choice == "1":
            configs = get_configs()
            if not configs:
                ui.warn("No config files found in configs/ or on USB.")
                if ui.confirm("Create default manifest?"):
                    config.save_config("manifest.json", config.DEFAULT_CONFIG)
                    configs = ["manifest.json"]
                else:
                    continue
            
            print(f"\n{ui.Colors.BOLD}Selected Configs:{ui.Colors.ENDC}")
            for c in configs: print(f"  - {Path(c).name}")
            
            if ui.confirm("Start Harvest?"):
                ops.harvest(configs)
                input("\nPress Enter to continue...")

        elif choice == "2":
            if ui.confirm("Start Deployment?"):
                ops.deploy()
                input("\nPress Enter to continue...")

        elif choice == "3":
            configs = get_configs()
            if not configs:
                ui.fail("No configs found. Create one first.")
            
            # Select config to edit
            print(f"\n{ui.Colors.BOLD}Select config to modify:{ui.Colors.ENDC}")
            for i, c in enumerate(configs):
                print(f"  [{i+1}] {Path(c).name}")
            
            try:
                idx = int(input(f"\n{ui.Colors.BOLD}> {ui.Colors.ENDC}")) - 1
                target_config = configs[idx]
                
                query = input("Enter search term: ").strip()
                if query:
                    ops.search(query, target_config)
            except (ValueError, IndexError):
                ui.warn("Invalid selection")
            
            input("\nPress Enter to continue...")

        elif choice == "4":
            fpath = input("Enter path to file on USB: ").strip()
            if fpath:
                ops.ingest(fpath)
            input("\nPress Enter to continue...")

        elif choice == "5":
            device = input("Enter target device (e.g. /dev/sdb): ").strip()
            if device:
                ops.init_backup(device)
            input("\nPress Enter to continue...")

        elif choice == "6":
            if ui.confirm("Run Garbage Collection on Rig B?"):
                ops.deploy(gc=True)
            input("\nPress Enter to continue...")

def main():
    system.check_root()
    
    parser = argparse.ArgumentParser(description="Airlock Protocol CLI")
    parser.add_argument("--mode", choices=["harvest", "deploy", "init-backup", "search", "ingest"])
    parser.add_argument("--config", nargs='+', help="Path(s) to config.json")
    parser.add_argument("--device", help="Target device for init-backup")
    parser.add_argument("--file", help="File path for ingest")
    parser.add_argument("--query", help="Search term")
    parser.add_argument("--gc", action="store_true", help="Run GC during deploy")
    
    args = parser.parse_args()

    # If no args, enter Interactive Mode
    if len(sys.argv) == 1:
        interactive_menu()
        sys.exit(0)

    # CLI Mode
    if args.mode == "harvest":
        configs = args.config if args.config else get_configs()
        if not configs:
            ui.fail("No configs provided or found.")
        ops.harvest(configs)
        
    elif args.mode == "deploy":
        ops.deploy(gc=args.gc)
        
    elif args.mode == "init-backup":
        if not args.device: ui.fail("--device required")
        ops.init_backup(args.device)
        
    elif args.mode == "search":
        if not args.query: ui.fail("--query required")
        # Default to first found config if not specified
        configs = args.config if args.config else get_configs()
        if not configs: ui.fail("No config found to update.")
        ops.search(args.query, configs[0])
        
    elif args.mode == "ingest":
        if not args.file: ui.fail("--file required")
        ops.ingest(args.file)

if __name__ == "__main__":
    main()
EOF
chmod +x airlock_pkg/airlock/main.py
