#!/usr/bin/env python3
r"""
Archi Start — consolidated launcher for all Archi components.

Usage:
    python scripts/start.py              (full service — default)
    python scripts/start.py service       (agent loop + discord)
    python scripts/start.py discord       (Discord bot only)
    python scripts/start.py watchdog      (service with auto-restart)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# Shared script utilities
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, PYTHON, header, load_env

import json

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

# Tag this process (and all children) so stop.py can identify Archi reliably
os.environ["ARCHI_RUNNING_INSTANCE"] = "1"

# PID lock to prevent multiple instances
LOCK_FILE = ROOT / "data" / "archi.pid"

# Marker file: once the user declines profile setup, don't nag again
_PROFILE_DECLINED = ROOT / "data" / ".profile_setup_declined"


def _needs_profile_setup() -> bool:
    """Check if user profile is missing or empty (no facts populated)."""
    if _PROFILE_DECLINED.exists():
        return False
    um_path = ROOT / "data" / "user_model.json"
    if not um_path.exists():
        return True
    try:
        data = json.loads(um_path.read_text(encoding="utf-8"))
        facts = data.get("facts", [])
        return len(facts) == 0
    except (json.JSONDecodeError, OSError):
        return True


def _offer_profile_setup() -> None:
    """Offer to run profile setup before launching Archi."""
    print()
    print("  ┌─────────────────────────────────────────────────┐")
    print("  │  No user profile found. Archi works better when │")
    print("  │  it knows a bit about you (name, schedule, etc) │")
    print("  └─────────────────────────────────────────────────┘")
    print()
    answer = input("  Run profile setup now? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        try:
            from scripts.profile_setup import main as profile_main
            profile_main()
        except Exception as e:
            print(f"  [WARNING] Profile setup failed: {e}")
            print("  You can run it manually later: python scripts/profile_setup.py")
    else:
        # Write marker so we don't ask again
        _PROFILE_DECLINED.parent.mkdir(parents=True, exist_ok=True)
        _PROFILE_DECLINED.write_text("", encoding="utf-8")
        print("  No problem — you can run 'python scripts/profile_setup.py' anytime.\n")


def _acquire_lock() -> bool:
    """Check if Archi is already running. Returns True if lock acquired."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            try:
                import psutil
                if psutil.pid_exists(old_pid):
                    proc = psutil.Process(old_pid)
                    if proc.is_running() and "python" in proc.name().lower():
                        return False
            except ImportError:
                try:
                    os.kill(old_pid, 0)
                    return False
                except OSError:
                    pass
        except (ValueError, OSError):
            pass
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock() -> None:
    """Remove PID lock file."""
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


# ── Service (full) ────────────────────────────────────────────

def start_service() -> None:
    """Start the Archi service (agent loop + Discord)."""
    if not _acquire_lock():
        print("  [ERROR] Archi is already running (PID lock exists).")
        print("  Use 'scripts/stop.py' first, or delete data/archi.pid if stale.")
        return
    header("Starting Archi Service")
    try:
        from src.service.archi_service import main as service_main
        service_main()
    finally:
        _release_lock()


# ── Discord Bot ───────────────────────────────────────────────

def start_discord() -> None:
    """Start the Discord bot."""
    header("Starting Archi Discord Bot")
    load_env()

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("  [ERROR] DISCORD_BOT_TOKEN not set in .env")
        return

    from src.core.goal_manager import GoalManager

    goal_manager = GoalManager()
    router = None
    try:
        from src.models.router import ModelRouter
        router = ModelRouter()
    except Exception as e:
        print(f"  [WARNING] Router not available: {e}")

    from src.interfaces.discord_bot import init_discord_bot, run_bot

    init_discord_bot(goal_manager, router=router)
    print("  Discord bot starting...")
    run_bot(token=token)


# ── Watchdog ──────────────────────────────────────────────────

def start_watchdog() -> None:
    """Run the service with auto-restart on crash."""
    if not _acquire_lock():
        print("  [ERROR] Archi is already running (PID lock exists).")
        print("  Use 'scripts/stop.py' first, or delete data/archi.pid if stale.")
        return

    header("Archi Watchdog — Auto-restart on crash")

    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    crash_log = log_dir / "archi_crashes.log"

    restart_delay = 15
    restart_count = 0

    print("  Archi will automatically restart if it crashes.")
    print("  Press Ctrl+C to stop.\n")

    try:
        while True:
            restart_count += 1
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"  [{ts}] Starting Archi (run #{restart_count})...")

            # Release watchdog lock so the child can acquire its own
            _release_lock()
            cmd = [PYTHON, str(ROOT / "scripts" / "start.py"), "service"]

            # Use Popen + poll loop.  KeyboardInterrupt reliably
            # interrupts time.sleep() on Windows (unlike custom signal
            # handlers, which only run after sleep finishes).
            proc = subprocess.Popen(cmd, cwd=str(ROOT))
            _shutting_down = False
            while proc.poll() is None:
                try:
                    time.sleep(0.3)
                except KeyboardInterrupt:
                    if not _shutting_down:
                        _shutting_down = True
                        print(
                            "\n"
                            "  ============================================\n"
                            "  Ctrl+C received — waiting for Archi to stop\n"
                            "  ============================================"
                        )
                        sys.stdout.flush()
                    else:
                        print("  Shutdown in progress, please wait...")
                        sys.stdout.flush()

            exit_code = proc.returncode
            # Re-acquire to guard the restart-delay window
            _acquire_lock()

            # If Ctrl+C was pressed, stop regardless of exit code
            if _shutting_down:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                print(f"  [{ts}] Archi stopped.")
                break

            ts = time.strftime("%Y-%m-%d %H:%M:%S")

            if exit_code in (0, -2, 2):
                print(f"  [{ts}] Archi stopped cleanly (code {exit_code}).")
                break

            msg = f"{ts} | Archi crashed with code {exit_code} (run #{restart_count})"
            print(f"  [{ts}] {msg}")

            with open(crash_log, "a", encoding="utf-8") as f:
                f.write(msg + "\n")

            print(f"  Restarting in {restart_delay} seconds...")
            try:
                time.sleep(restart_delay)
            except KeyboardInterrupt:
                print("\n  Watchdog stopped.")
                break
    finally:
        _release_lock()


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        dispatch = {
            "service": start_service,
            "discord": start_discord,
            "watchdog": start_watchdog,
        }
        func = dispatch.get(cmd)
        if func:
            func()
        else:
            print(f"Unknown command: {cmd}")
            print("Available: service, discord, watchdog")
            sys.exit(1)
    else:
        # Offer profile setup on first run (interactive mode only)
        if _needs_profile_setup():
            _offer_profile_setup()

        header("Archi Launcher")
        print("  [1] Full agent (agent loop + dream cycle + discord) — default")
        print("  [2] Discord chat only (no dream cycle or background work)")
        print("  [3] Full agent with auto-restart on crash (production)")
        print("  [Q] Quit\n")

        choice = input("Select [1]: ").strip() or "1"
        dispatch = {"1": start_service, "2": start_discord, "3": start_watchdog}
        if choice.upper() != "Q":
            func = dispatch.get(choice)
            if func:
                func()
            else:
                print(f"  Unknown option: {choice}")


if __name__ == "__main__":
    main()
