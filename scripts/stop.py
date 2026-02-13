#!/usr/bin/env python3
r"""
Archi Stop — gracefully stop all Archi processes and services.

Consolidates: restart_archi.ps1/.bat, remove_auto_restart.ps1,
              and all process-killing logic.

Usage:
    .\venv\Scripts\python.exe scripts\stop.py              (stop everything)
    .\venv\Scripts\python.exe scripts\stop.py service       (stop Archi service only)
    .\venv\Scripts\python.exe scripts\stop.py ports         (free ports 5000/5001)
    .\venv\Scripts\python.exe scripts\stop.py restart       (stop then start)
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

# Archi-related script identifiers (matched against process command lines)
ARCHI_IDENTIFIERS = [
    "start_archi", "archi_service", "run_web_chat", "run_dashboard",
    "run_discord_bot", "agent_loop", "web_chat", "scripts/start.py",
    "scripts\\start.py",
]

ARCHI_PORTS = [5000, 5001]


def _header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def _kill_by_ports() -> int:
    """Kill processes listening on Archi ports (5000, 5001). Returns count killed."""
    killed = 0
    if sys.platform == "win32":
        for port in ARCHI_PORTS:
            try:
                # Use netstat to find PIDs on the port
                result = subprocess.run(
                    f'netstat -ano | findstr ":{port} "',
                    shell=True, capture_output=True, text=True,
                )
                for line in result.stdout.strip().split("\n"):
                    if "LISTENING" in line:
                        parts = line.strip().split()
                        if parts:
                            pid = int(parts[-1])
                            if pid > 0:
                                try:
                                    os.kill(pid, signal.SIGTERM)
                                    print(f"  Killed PID {pid} on port {port}")
                                    killed += 1
                                except (OSError, PermissionError):
                                    # Force kill on Windows
                                    subprocess.run(
                                        f"taskkill /PID {pid} /F",
                                        shell=True, capture_output=True,
                                    )
                                    print(f"  Force-killed PID {pid} on port {port}")
                                    killed += 1
            except Exception as e:
                print(f"  Error checking port {port}: {e}")
    else:
        # Linux/macOS: use lsof
        for port in ARCHI_PORTS:
            try:
                result = subprocess.run(
                    f"lsof -ti:{port}", shell=True, capture_output=True, text=True,
                )
                for pid_str in result.stdout.strip().split("\n"):
                    if pid_str.strip():
                        pid = int(pid_str.strip())
                        os.kill(pid, signal.SIGTERM)
                        print(f"  Killed PID {pid} on port {port}")
                        killed += 1
            except Exception:
                pass
    return killed


def _kill_archi_processes() -> int:
    """Kill Python processes running Archi scripts. Returns count killed."""
    killed = 0
    try:
        import psutil
    except ImportError:
        # Fallback without psutil
        if sys.platform == "win32":
            return _kill_archi_processes_windows()
        print("  psutil not available, using port-based kill only")
        return 0

    current_pid = os.getpid()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.pid == current_pid:
                continue
            info = proc.info
            name = (info.get("name") or "").lower()
            if "python" not in name:
                continue
            cmdline = " ".join(info.get("cmdline") or [])
            for ident in ARCHI_IDENTIFIERS:
                if ident in cmdline:
                    proc.terminate()
                    print(f"  Terminated PID {proc.pid}: {ident}")
                    killed += 1
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return killed


def _kill_archi_processes_windows() -> int:
    """Windows fallback: use WMIC to find and kill Archi Python processes."""
    killed = 0
    try:
        result = subprocess.run(
            'wmic process where "Name=\'python.exe\'" get ProcessId,CommandLine /FORMAT:CSV',
            shell=True, capture_output=True, text=True,
        )
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("Node"):
                continue
            for ident in ARCHI_IDENTIFIERS:
                if ident in line:
                    # Extract PID (last CSV field)
                    parts = line.split(",")
                    if parts:
                        try:
                            pid = int(parts[-1].strip())
                            if pid != os.getpid():
                                subprocess.run(
                                    f"taskkill /PID {pid} /F",
                                    shell=True, capture_output=True,
                                )
                                print(f"  Killed PID {pid}: {ident}")
                                killed += 1
                        except (ValueError, OSError):
                            pass
                    break
    except Exception as e:
        print(f"  WMIC fallback error: {e}")
    return killed


# ── Stop Everything ───────────────────────────────────────────

def stop_all() -> None:
    _header("Stopping All Archi Processes")

    total = 0

    print("  Killing Archi Python processes...")
    total += _kill_archi_processes()

    print("\n  Freeing ports (5000, 5001)...")
    total += _kill_by_ports()

    if total == 0:
        print("\n  No Archi processes found running.")
    else:
        print(f"\n  Stopped {total} process(es).")
        # Brief pause for cleanup
        time.sleep(1)


def stop_service_only() -> None:
    _header("Stopping Archi Service")
    killed = _kill_archi_processes()
    if killed == 0:
        print("  No Archi service processes found.")
    else:
        print(f"\n  Stopped {killed} process(es).")


def free_ports() -> None:
    _header("Freeing Archi Ports")
    killed = _kill_by_ports()
    if killed == 0:
        print("  Ports 5000/5001 are already free.")
    else:
        print(f"\n  Freed {killed} port(s).")


def restart() -> None:
    """Stop everything, then start the service."""
    stop_all()
    print("\n  Starting Archi...\n")
    time.sleep(2)

    # Start via subprocess so this script can exit
    start_script = ROOT / "scripts" / "start.py"
    if sys.platform == "win32":
        # Start in a new window on Windows
        subprocess.Popen(
            f'start "Archi" "{PYTHON}" "{start_script}"',
            shell=True, cwd=str(ROOT),
        )
        print("  Archi started in a new window.")
    else:
        subprocess.Popen(
            [PYTHON, str(start_script)],
            cwd=str(ROOT),
            start_new_session=True,
        )
        print("  Archi started in background.")


# ── Main ──────────────────────────────────────────────────────

def main_menu() -> None:
    _header("Archi Stop")
    print("  [1] Stop all Archi processes (default)")
    print("  [2] Stop service only (keep ports free)")
    print("  [3] Free ports (5000/5001) only")
    print("  [4] Restart (stop + start in new window)")
    print("  [Q] Quit\n")

    choice = input("Select [1]: ").strip() or "1"

    dispatch = {
        "1": stop_all,
        "2": stop_service_only,
        "3": free_ports,
        "4": restart,
    }

    if choice.upper() == "Q":
        return

    func = dispatch.get(choice)
    if func:
        func()
    else:
        print(f"  Unknown option: {choice}")


def main() -> None:
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        dispatch = {
            "service": stop_service_only,
            "ports": free_ports,
            "restart": restart,
            "all": stop_all,
        }
        func = dispatch.get(cmd)
        if func:
            func()
        else:
            print(f"Unknown command: {cmd}")
            print("Available: service, ports, restart, all")
            sys.exit(1)
    else:
        main_menu()


if __name__ == "__main__":
    main()
