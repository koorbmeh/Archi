#!/usr/bin/env python3
r"""
Archi Start — consolidated launcher for all Archi components.

Usage:
    .\venv\Scripts\python.exe scripts\start.py              (full service — default, no web)
    .\venv\Scripts\python.exe scripts\start.py service       (agent loop + discord, no web)
    .\venv\Scripts\python.exe scripts\start.py service --web (agent loop + dashboard + web chat + discord)
    .\venv\Scripts\python.exe scripts\start.py chat          (CLI terminal chat only)
    .\venv\Scripts\python.exe scripts\start.py web           (web chat on port 5001)
    .\venv\Scripts\python.exe scripts\start.py dashboard     (dashboard on port 5000)
    .\venv\Scripts\python.exe scripts\start.py discord       (Discord bot only)
    .\venv\Scripts\python.exe scripts\start.py watchdog      (service with auto-restart, no web)
    .\venv\Scripts\python.exe scripts\start.py watchdog --web (service with auto-restart + web)
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

# Add project root to path
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def _header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def _load_env() -> None:
    """Load .env into os.environ."""
    try:
        from dotenv import load_dotenv
        env_path = ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=True)
    except ImportError:
        pass


# ── Service (full) ────────────────────────────────────────────

def start_service(enable_web: bool = False) -> None:
    """Start the Archi service. Web interfaces only start when enable_web=True."""
    _header("Starting Archi Service" + (" + Web" if enable_web else ""))
    from src.service.archi_service import main as service_main
    service_main(enable_web=enable_web)


# ── CLI Chat ──────────────────────────────────────────────────

def start_chat() -> None:
    """Start the interactive CLI chat."""
    _header("Archi CLI Chat")
    _load_env()

    from src.core.goal_manager import GoalManager

    goal_manager = GoalManager()
    router = None
    try:
        from src.models.router import ModelRouter
        router = ModelRouter()
    except Exception as e:
        print(f"  [WARNING] Model router not available: {e}")
        print("  Chat will work with limited capabilities.\n")

    from src.interfaces.action_executor import process_message

    print("Type your message (or /help for commands, /exit to quit):\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        cmd = user_input.lower()
        if cmd in ("/exit", "/quit", "/q"):
            print("Bye!")
            break
        elif cmd == "/help":
            print("  /goal <text>  — Create a new goal")
            print("  /goals        — List active goals")
            print("  /status       — System status")
            print("  /cost         — Today's API cost")
            print("  /exit         — Quit")
            continue
        elif cmd.startswith("/goal "):
            desc = user_input[6:].strip()
            goal = goal_manager.create_goal(
                description=desc, user_intent=desc, priority=5,
            )
            goal_manager.save_state()
            print(f"  Goal created: {goal.goal_id}")
            continue
        elif cmd == "/goals":
            goals = goal_manager.get_active_goals()
            if goals:
                for g in goals:
                    print(f"  [{g.status}] {g.description[:60]}")
            else:
                print("  No active goals.")
            continue
        elif cmd == "/status":
            try:
                from src.monitoring.health_check import health_check
                health = health_check.check_all()
                print(f"  Status: {health['overall_status']}")
                print(f"  Summary: {health['summary']}")
            except Exception as e:
                print(f"  Health check failed: {e}")
            continue
        elif cmd == "/cost":
            try:
                from src.monitoring.cost_tracker import get_cost_tracker
                tracker = get_cost_tracker()
                summary = tracker.get_summary("today")
                print(f"  Today's cost: ${summary.get('total_cost', 0):.4f}")
            except Exception as e:
                print(f"  Cost tracker failed: {e}")
            continue

        # Process message
        try:
            response, _, _ = process_message(
                user_input, router, source="cli", goal_manager=goal_manager,
            )
            print(f"Archi: {response}\n")
        except Exception as e:
            print(f"  [ERROR] {e}\n")


# ── Web Chat ──────────────────────────────────────────────────

def start_web_chat() -> None:
    """Start the web chat interface on port 5001."""
    _header("Starting Archi Web Chat")
    _load_env()

    from src.core.goal_manager import GoalManager

    goal_manager = GoalManager()
    router = None
    try:
        from src.models.router import ModelRouter
        router = ModelRouter()
    except Exception as e:
        print(f"  [WARNING] Router not available (will init on first message): {e}")

    from src.interfaces.web_chat import init_web_chat, run_web_chat

    init_web_chat(goal_manager, router=router)
    print("  Web chat: http://127.0.0.1:5001/chat")
    run_web_chat(host="127.0.0.1", port=5001)


# ── Dashboard ─────────────────────────────────────────────────

def start_dashboard() -> None:
    """Start the monitoring dashboard on port 5000."""
    _header("Starting Archi Dashboard")
    _load_env()

    from src.web.dashboard import init_dashboard, run_dashboard

    init_dashboard(None, None)
    print("  Dashboard: http://127.0.0.1:5000")
    run_dashboard(host="127.0.0.1", port=5000)


# ── Discord Bot ───────────────────────────────────────────────

def start_discord() -> None:
    """Start the Discord bot."""
    _header("Starting Archi Discord Bot")
    _load_env()

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("  [ERROR] DISCORD_BOT_TOKEN not set in .env")
        print("  Set it and try again.")
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

def start_watchdog(enable_web: bool = False) -> None:
    """Run the service with auto-restart on crash."""
    _header("Archi Watchdog — Auto-restart on crash" + (" + Web" if enable_web else ""))

    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    crash_log = log_dir / "archi_crashes.log"

    restart_delay = 15
    restart_count = 0

    print("  Archi will automatically restart if it crashes.")
    if not enable_web:
        print("  Web interfaces disabled (use --web to enable).")
    print("  Press Ctrl+C to stop.\n")

    while True:
        restart_count += 1
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"  [{ts}] Starting Archi (run #{restart_count})...")

        try:
            # Run start.py service as a subprocess so watchdog can monitor it
            cmd = [PYTHON, str(ROOT / "scripts" / "start.py"), "service"]
            if enable_web:
                cmd.append("--web")
            result = subprocess.run(
                cmd,
                cwd=str(ROOT),
            )
            exit_code = result.returncode
        except KeyboardInterrupt:
            # Ctrl+C while subprocess is running — service handles its own shutdown
            print("\n  Watchdog received Ctrl+C — shutting down.")
            break

        ts = time.strftime("%Y-%m-%d %H:%M:%S")

        # Exit code 0 or SIGINT (Ctrl+C forwarded to child) = clean shutdown, don't restart
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


# ── Main ──────────────────────────────────────────────────────

def main_menu() -> None:
    _header("Archi Launcher")
    print("  [1] Service — no web (agent loop + discord) — default")
    print("  [2] Service + Web (agent loop + dashboard + web chat + discord)")
    print("  [3] CLI chat (terminal)")
    print("  [4] Web chat only (port 5001)")
    print("  [5] Dashboard only (port 5000)")
    print("  [6] Discord bot only")
    print("  [7] Watchdog — no web (auto-restart)")
    print("  [8] Watchdog + Web (auto-restart + dashboard + web chat)")
    print("  [Q] Quit\n")

    choice = input("Select [1]: ").strip() or "1"

    dispatch = {
        "1": lambda: start_service(enable_web=False),
        "2": lambda: start_service(enable_web=True),
        "3": start_chat,
        "4": start_web_chat,
        "5": start_dashboard,
        "6": start_discord,
        "7": lambda: start_watchdog(enable_web=False),
        "8": lambda: start_watchdog(enable_web=True),
    }

    if choice.upper() == "Q":
        return

    func = dispatch.get(choice)
    if func:
        func()
    else:
        print(f"  Unknown option: {choice}")


def main() -> None:
    # Support direct subcommand
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        enable_web = "--web" in [a.lower() for a in sys.argv[2:]]

        dispatch = {
            "service": lambda: start_service(enable_web=enable_web),
            "chat": start_chat,
            "web": start_web_chat,
            "dashboard": start_dashboard,
            "discord": start_discord,
            "watchdog": lambda: start_watchdog(enable_web=enable_web),
        }
        func = dispatch.get(cmd)
        if func:
            func()
        else:
            print(f"Unknown command: {cmd}")
            print("Available: service, chat, web, dashboard, discord, watchdog")
            print("Options:  --web  (enable web dashboard + web chat for service/watchdog)")
            sys.exit(1)
    else:
        main_menu()


if __name__ == "__main__":
    main()
