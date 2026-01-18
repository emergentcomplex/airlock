# 1. Create the Package Structure
mkdir -p airlock_pkg/airlock
touch airlock_pkg/airlock/__init__.py

# 2. Create the UI Module
cat <<'EOF' > airlock_pkg/airlock/ui.py
import sys
import time
import threading
import itertools
import os

# --- VISUAL CONSTANTS ---
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

# --- COMPONENT: BANNER ---
def banner(version):
    os.system('clear')
    print(f"{Colors.BOLD}{Colors.CYAN}╔══════════════════════════════════════════════════════════════╗{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}║  AIRLOCK PROTOCOL {version:<34} ║{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}║  Sovereign State Synchronization                             ║{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}╚══════════════════════════════════════════════════════════════╝{Colors.ENDC}")
    print("")

# --- COMPONENT: LOGGING ---
def step(msg):
    print(f"{Colors.BOLD}{Colors.BLUE}[*] {msg}{Colors.ENDC}")

def success(msg):
    print(f"{Colors.BOLD}{Colors.GREEN}[✔] {msg}{Colors.ENDC}")

def warn(msg):
    print(f"{Colors.BOLD}{Colors.WARNING}[!] {msg}{Colors.ENDC}")

def info(msg):
    print(f"    {msg}")

def fail(msg, detail=None):
    print(f"\n{Colors.BOLD}{Colors.FAIL}[✘] FATAL ERROR: {msg}{Colors.ENDC}")
    if detail:
        print(f"{Colors.FAIL}--- ERROR DETAILS ---{Colors.ENDC}")
        print(detail.strip())
        print(f"{Colors.FAIL}---------------------{Colors.ENDC}")
    sys.exit(1)

def table_row(col1, col2, col3):
    """Prints a formatted table row for search results."""
    print(f"    {Colors.CYAN}{col1:<30}{Colors.ENDC} {col2:<15} {col3}")

# --- COMPONENT: SPINNER ---
class Spinner:
    """
    A threaded spinner for long-running operations.
    Usage: with Spinner("Doing work..."): do_work()
    """
    def __init__(self, message="Processing..."):
        self.message = message
        self.stop_running = False
        self.thread = threading.Thread(target=self._animate)

    def _animate(self):
        for c in itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']):
            if self.stop_running: break
            sys.stdout.write(f'\r{Colors.CYAN}{c}{Colors.ENDC} {self.message}')
            sys.stdout.flush()
            time.sleep(0.1)
        # Clear the line on exit
        sys.stdout.write('\r' + ' ' * (len(self.message) + 2) + '\r')

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.stop_running = True
        self.thread.join()
        # If an exception occurred inside the block, re-raise it
        if exc_type:
            return False

# --- COMPONENT: MENU ---
def menu(options, prompt="Select an operation"):
    """
    Renders an interactive menu.
    options: dict of {key: description}
    """
    print(f"\n{Colors.BOLD}{prompt}:{Colors.ENDC}")
    for key, desc in options.items():
        print(f"  {Colors.CYAN}[{key}]{Colors.ENDC} {desc}")
    
    while True:
        try:
            choice = input(f"\n{Colors.BOLD}> {Colors.ENDC}").strip().lower()
            if choice in options:
                return choice
            print(f"{Colors.WARNING}Invalid selection.{Colors.ENDC}")
        except KeyboardInterrupt:
            print("\n")
            sys.exit(0)

def confirm(question):
    """Prompts for Yes/No."""
    while True:
        choice = input(f"{Colors.BOLD}{question} (y/n): {Colors.ENDC}").strip().lower()
        if choice in ['y', 'yes']: return True
        if choice in ['n', 'no']: return False
EOF
