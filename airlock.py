#!/usr/bin/env python3
import sys
import os

# --- BOOTSTRAP LOGIC ---
# We need to tell Python where to find the 'airlock' package.
# It is located in the 'airlock_pkg' subdirectory.

current_dir = os.path.dirname(os.path.abspath(__file__))
package_dir = os.path.join(current_dir, "airlock_pkg")

# Add to path so "import airlock" works
if os.path.exists(package_dir):
    sys.path.insert(0, package_dir)
else:
    # Fallback: Maybe we are running inside the package dir?
    sys.path.insert(0, current_dir)

# --- LAUNCH ---
try:
    from airlock.main import main
    if __name__ == "__main__":
        main()
except ImportError as e:
    print("\033[91m[✘] FATAL: Could not load Airlock Package.\033[0m")
    print(f"    Looking in: {package_dir}")
    print(f"    Error: {e}")
    sys.exit(1)
except KeyboardInterrupt:
    print("\n\033[93m[!] Interrupted by user.\033[0m")
    sys.exit(0)
