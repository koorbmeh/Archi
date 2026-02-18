#!/usr/bin/env python3
r"""
Archi Stop — NUCLEAR shutdown of all Archi processes.

When you run this, everything dies. No graceful waiting, no "finish
your current API call". Kill it dead.

Usage:
    python scripts/stop.py              (stop everything)
    python scripts/stop.py restart      (stop then start)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, PYTHON, header

LOCK_FILE = ROOT / "data" / "archi.pid"

# Broad set of identifiers — match any Python process that looks like Archi.
# Checked against the full command line string.
ARCHI_IDENTIFIERS = [
    "start_archi", "archi_service", "run_discord_bot",
    "agent_loop", "scripts/start.py", "scripts\\start.py",
    "src/service/archi_service", "src\\service\\archi_service",
]

# Also match any python process whose working directory is the Archi project
ARCHI_ROOT_STR = str(ROOT).lower()


def _find_archi_processes_psutil():
    """Find all Archi-related Python processes using psutil. Returns list of (pid, reason)."""
    try:
        import psutil
    except ImportError:
        return None  # Signal to use fallback

    found = []
    current_pid = os.getpid()

    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd", "exe"]):
        try:
            if proc.pid == current_pid:
                continue
            info = proc.info
            name = (info.get("name") or "").lower()
            if "python" not in name:
                continue

            cmdline = " ".join(info.get("cmdline") or [])

            # Check 1: Command line contains an Archi identifier
            for ident in ARCHI_IDENTIFIERS:
                if ident in cmdline:
                    found.append((proc, f"cmdline match: {ident}"))
                    break
            else:
                # Check 2: Working directory is the Archi project
                try:
                    cwd = (proc.cwd() or "").lower()
                    if cwd and ARCHI_ROOT_STR in cwd:
                        found.append((proc, f"cwd match: {cwd}"))
                        continue
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    pass

                # Check 3: Command line contains the Archi project path
                if ARCHI_ROOT_STR in cmdline.lower():
                    found.append((proc, f"path match in cmdline"))

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return found


def _kill_processes_psutil(procs) -> int:
    """Force-kill a list of (proc, reason) tuples. Returns count killed."""
    import psutil
    killed = 0

    for proc, reason in procs:
        try:
            pid = proc.pid
            # SIGKILL / taskkill /F — no graceful anything
            proc.kill()
            print(f"  KILLED PID {pid} ({reason})")
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"  Could not kill PID {proc.pid}: {e}")

    # Wait briefly for processes to actually die
    if killed:
        gone, alive = psutil.wait_procs(
            [p for p, _ in procs], timeout=5
        )
        for p in alive:
            try:
                p.kill()  # Double-tap
            except Exception:
                pass

    return killed


def _kill_archi_processes_windows() -> int:
    """Windows fallback (no psutil): use PowerShell to find and force-kill."""
    killed = 0
    try:
        # Get all python processes with their command lines
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

            is_archi = False
            reason = ""

            # Check identifiers
            for ident in ARCHI_IDENTIFIERS:
                if ident in cmdline:
                    is_archi = True
                    reason = ident
                    break

            # Check project path
            if not is_archi and ARCHI_ROOT_STR in cmdline.lower():
                is_archi = True
                reason = "project path"

            if is_archi:
                try:
                    pid = int(pid_str.strip())
                    if pid != os.getpid():
                        # Force kill — /F means no asking nicely
                        subprocess.run(
                            f"taskkill /PID {pid} /F /T",
                            shell=True, capture_output=True,
                        )
                        print(f"  KILLED PID {pid} ({reason})")
                        killed += 1
                except (ValueError, OSError):
                    pass

    except Exception as e:
        print(f"  PowerShell fallback error: {e}")
    return killed


def _clear_lock() -> None:
    """Remove PID lock file."""
    try:
        LOCK_FILE.unlink()
        print("  Cleared PID lock file.")
    except OSError:
        pass


def stop_all() -> None:
    header("Stopping All Archi Processes (FORCE)")

    total = 0

    # Try psutil first (more reliable, cross-platform)
    procs = _find_archi_processes_psutil()
    if procs is not None:
        if procs:
            total = _kill_processes_psutil(procs)
        else:
            # psutil found nothing — but double-check with PID file
            if LOCK_FILE.exists():
                try:
                    import psutil
                    old_pid = int(LOCK_FILE.read_text().strip())
                    if psutil.pid_exists(old_pid):
                        try:
                            p = psutil.Process(old_pid)
                            p.kill()
                            print(f"  KILLED PID {old_pid} (from lock file)")
                            total += 1
                        except Exception:
                            pass
                except Exception:
                    pass
    else:
        # No psutil — Windows fallback
        total = _kill_archi_processes_windows()

    _clear_lock()

    if total == 0:
        print("  No Archi processes found running.")
    else:
        print(f"\n  Force-killed {total} process(es).")
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
