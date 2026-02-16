#!/usr/bin/env python3
r"""
Archi Stop — gracefully stop all Archi processes and services.

Usage:
    python scripts/stop.py              (stop everything)
    python scripts/stop.py service       (stop Archi service only)
    python scripts/stop.py restart       (stop then start)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, PYTHON, header

ARCHI_IDENTIFIERS = [
    "start_archi", "archi_service", "run_discord_bot",
    "agent_loop", "scripts/start.py", "scripts\\start.py",
]

LOCK_FILE = ROOT / "data" / "archi.pid"


def _kill_archi_processes() -> int:
    """Kill Python processes running Archi scripts. Returns count killed."""
    killed = 0
    try:
        import psutil
    except ImportError:
        if sys.platform == "win32":
            return _kill_archi_processes_windows()
        print("  psutil not available, cannot find Archi processes")
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
        except Exception:
            continue

    return killed


def _kill_archi_processes_windows() -> int:
    """Windows fallback: use PowerShell to find and kill Archi Python processes."""
    killed = 0
    try:
        ps_cmd = (
            'powershell -Command "Get-Process python* -ErrorAction SilentlyContinue '
            '| ForEach-Object { $id=$_.Id; $cmd=(Get-CimInstance Win32_Process '
            '-Filter \\\"ProcessId=$id\\\" -ErrorAction SilentlyContinue).CommandLine; '
            'if($cmd){Write-Output \\\"$id|$cmd\\\"} }"'
        )
        result = subprocess.run(ps_cmd, shell=True, capture_output=True, text=True)
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            pid_str, cmdline = line.split("|", 1)
            for ident in ARCHI_IDENTIFIERS:
                if ident in cmdline:
                    try:
                        pid = int(pid_str.strip())
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
        print(f"  PowerShell fallback error: {e}")
    return killed


def _clear_lock() -> None:
    """Remove PID lock file after stopping processes."""
    try:
        LOCK_FILE.unlink()
        print("  Cleared PID lock file.")
    except OSError:
        pass


def stop_all() -> None:
    header("Stopping All Archi Processes")
    total = _kill_archi_processes()
    had_lock = LOCK_FILE.exists()
    _clear_lock()
    if total == 0:
        if had_lock:
            print("  No Archi processes found running.")
            print("  Cleared stale PID lock file.")
        else:
            print("  No Archi processes found running.")
    else:
        print(f"\n  Stopped {total} process(es).")
        time.sleep(1)


def restart() -> None:
    """Stop everything, then start the service."""
    stop_all()
    print("\n  Starting Archi...\n")
    time.sleep(2)

    start_script = ROOT / "scripts" / "start.py"
    if sys.platform == "win32":
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


def main() -> None:
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        dispatch = {"restart": restart, "all": stop_all}
        func = dispatch.get(cmd, stop_all)
        func()
    else:
        header("Archi Stop")
        print("  [1] Stop all Archi processes (default)")
        print("  [2] Restart (stop + start in new window)")
        print("  [Q] Quit\n")

        choice = input("Select [1]: ").strip() or "1"
        dispatch = {"1": stop_all, "2": restart}
        if choice.upper() != "Q":
            func = dispatch.get(choice)
            if func:
                func()
            else:
                print(f"  Unknown option: {choice}")


if __name__ == "__main__":
    main()
