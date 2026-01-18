cat <<'EOF' > airlock_pkg/airlock/config.py
import json
from pathlib import Path
from . import ui

DEFAULT_CONFIG = {
    "comment": "Master Air-Gap Configuration",
    "pacman_packages": ["clamav", "rkhunter", "lynis", "binwalk", "usbguard", "btrbk", "nix", "mbuffer"],
    "nix_packages": ["hello", "python3", "bashInteractive"]
}

def load_config(config_path):
    """Loads a single JSON config file."""
    path = Path(config_path)
    if not path.exists():
        # Auto-create if missing (Self-Healing)
        save_config(path, DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        ui.fail(f"Invalid JSON in {path}", str(e))

def load_merged_configs(config_paths):
    """
    Merges multiple config files into a single master dictionary.
    Used during Harvest to calculate the total state.
    """
    master_config = {"pacman_packages": [], "nix_packages": []}
    
    for path in config_paths:
        p = Path(path)
        if not p.exists():
            ui.fail(f"Config file not found: {path}")
        
        try:
            with open(p, 'r') as f:
                data = json.load(f)
                master_config["pacman_packages"].extend(data.get("pacman_packages", []))
                master_config["nix_packages"].extend(data.get("nix_packages", []))
                ui.info(f"Loaded Config: {p.name}")
        except json.JSONDecodeError as e:
            ui.fail(f"Invalid JSON in {p.name}", str(e))
            
    # Deduplicate and Sort
    master_config["pacman_packages"] = sorted(list(set(master_config["pacman_packages"])))
    master_config["nix_packages"] = sorted(list(set(master_config["nix_packages"])))
    
    return master_config

def save_config(config_path, data):
    """Writes data to JSON with pretty printing."""
    try:
        with open(config_path, 'w') as f:
            json.dump(data, f, indent=4)
    except OSError as e:
        ui.fail(f"Could not write to {config_path}", str(e))

def add_package(config_path, manager, package_name):
    """Adds a package to the specified list (pacman or nix)."""
    data = load_config(config_path)
    key = f"{manager}_packages"
    
    if key not in data:
        data[key] = []
        
    if package_name in data[key]:
        ui.warn(f"Package '{package_name}' already exists in {config_path}")
        return False
    
    data[key].append(package_name)
    data[key].sort()
    save_config(config_path, data)
    ui.success(f"Added '{package_name}' to {config_path} ({manager})")
    return True

def remove_package(config_path, manager, package_name):
    """Removes a package from the specified list."""
    data = load_config(config_path)
    key = f"{manager}_packages"
    
    if key not in data or package_name not in data[key]:
        ui.warn(f"Package '{package_name}' not found in {config_path}")
        return False
        
    data[key].remove(package_name)
    save_config(config_path, data)
    ui.success(f"Removed '{package_name}' from {config_path}")
    return True

def list_content(config_path):
    """Displays the content of a config file."""
    data = load_config(config_path)
    print(f"\n{ui.Colors.BOLD}Config: {config_path}{ui.Colors.ENDC}")
    
    print(f"{ui.Colors.CYAN}[Pacman Packages]{ui.Colors.ENDC}")
    for p in data.get('pacman_packages', []):
        print(f"  - {p}")
        
    print(f"{ui.Colors.CYAN}[Nix Packages]{ui.Colors.ENDC}")
    for p in data.get('nix_packages', []):
        print(f"  - {p}")
EOF
