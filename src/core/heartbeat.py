"""
Heartbeat — Archi's single background processing loop.

Session 89: merged agent_loop.py + dream_cycle.py into one module.
The heartbeat is Archi's pulse: check for problems (emergency stop,
hardware throttle), check for work, do work if it's time.

Flow:
- Emergency stop check (EMERGENCY_STOP file)
- Hardware throttle check (CPU/memory/temp/disk)
- If idle long enough → run a cycle:
  - Execute pending tasks from user-created goals
  - Suggest work to the user if nothing to do
  - Learning review, periodic synthesis, file cleanup

Delegates to:
- autonomous_executor.py — task execution loop
- idea_generator.py — work suggestion (no auto-approval)
- reporting.py — morning report + hourly summary
"""

import json
import logging
import os
import time
import threading
from datetime import datetime, date
from typing import Optional, Dict, Any, List

import yaml

from src.core.goal_manager import GoalManager
from src.core.goal_worker_pool import GoalWorkerPool
from src.core.learning_system import LearningSystem
from src.core import autonomous_executor
from src.core import idea_generator
from src.core import reporting
from src.memory.memory_manager import MemoryManager
from src.utils.paths import base_path as _base_path_str
from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Cap in-memory cycle history to prevent unbounded growth.
# Older entries are already persisted to data/dream_log.jsonl.
_MAX_CYCLE_HISTORY = 500


class EmergencyStop:
    """Check for EMERGENCY_STOP file; if present, agent must exit immediately."""

    def __init__(self, stop_file_path: Optional[str] = None) -> None:
        base = _base_path_str()
        self.stop_file = stop_file_path or os.path.join(base, "EMERGENCY_STOP")

    def check(self) -> bool:
        """Return True if emergency stop is triggered."""
        try:
            if os.path.isfile(self.stop_file):
                logger.critical("EMERGENCY STOP TRIGGERED: %s", self.stop_file)
                return True
            return False
        except OSError as e:
            logger.debug("Emergency stop check failed: %s", e)
            return False


class Heartbeat:
    """
    Archi's single background loop — the heartbeat.

    Each tick:
    - Check for emergency stop (EMERGENCY_STOP file)
    - Check hardware throttle (CPU/memory/temp/disk)
    - If idle long enough, run a cycle:
      - Execute pending tasks from user-created goals
      - Suggest work to the user if nothing to do
      - Review and learn from past actions
      - Periodic synthesis (informational only)
    """

    # Fixed poll chunk for stop_flag responsiveness inside sleeps.
    _POLL_CHUNK = 5.0

    def __init__(self, interval_seconds: int = 300):
        self.interval = interval_seconds
        self.last_activity = datetime.now()
        self.is_running_cycle = False
        self._monitor_thread: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()
        self.task_queue: List[Dict[str, Any]] = []
        self.cycle_history: List[Dict[str, Any]] = []

        # Emergency stop + hardware throttle (merged from agent_loop.py, session 89)
        self._emergency_stop = EmergencyStop()
        self._system_monitor: Optional[Any] = None  # lazy-init on first tick

        # Autonomous execution components
        self.goal_manager: Optional[GoalManager] = None
        self.autonomous_mode = False
        self._router: Optional[Any] = None
        self.learning_system = LearningSystem()
        self.goal_worker_pool: Optional[GoalWorkerPool] = None

        # Long-term semantic memory (LanceDB) for research recall.
        # Initialized in a background thread to avoid blocking startup
        # (sentence-transformers import loads torch, ~10-30s cold).
        # _memory_ready event signals when self.memory is safe to read.
        self._memory: Optional[MemoryManager] = None
        self._memory_ready = threading.Event()
        self._memory_init_thread = threading.Thread(
            target=self._init_memory, daemon=True,
        )
        self._memory_init_thread.start()

        # Morning report tracking
        self._morning_report_sent: Optional[date] = None
        self._overnight_results: List[Dict[str, Any]] = []
        self._overnight_results_path = _base_path() / "data" / "overnight_results.json"
        self._overnight_results = reporting.load_overnight_results(
            self._overnight_results_path,
        )

        # Hourly notification accumulator
        self._hourly_task_results: List[Dict[str, Any]] = []
        self._last_hourly_notify: float = time.monotonic()

        # Work suggestion tracking
        self._last_suggest_time: Optional[datetime] = None
        self._pending_suggestions: List[Dict[str, Any]] = []
        self._pending_batch_id: Optional[str] = None
        # Recent suggestions (last 20) for recovering old/dismissed suggestions
        self._recent_suggestions: List[Dict[str, Any]] = []
        # Adaptive suggestion cooldown: doubles each time user doesn't respond,
        # resets to base when user sends any message.
        self._suggest_cooldown_base = 120  # 2 minutes (was 600; bump for production)
        self._suggest_cooldown_max = 14400  # 4 hours cap
        self._suggest_cooldown = self._suggest_cooldown_base
        self._unanswered_suggest_count = 0

        self.identity = self._load_identity()
        self.project_context = self._load_project_context()
        self.prime_directive = self._load_prime_directive()
        role = self.identity.get("identity", {}).get("role", "Archi")
        logger.info(
            "Heartbeat initialized (interval: %ds) — identity: %s",
            interval_seconds, role,
        )

    @property
    def memory(self) -> Optional[MemoryManager]:
        """Thread-safe accessor for memory (None until _memory_ready is set)."""
        if self._memory_ready.is_set():
            return self._memory
        return None

    def set_memory(self, mem: MemoryManager) -> None:
        """Set the memory manager reference (used by _init_memory and external callers)."""
        self._memory = mem
        self._memory_ready.set()

    def _init_memory(self) -> None:
        """Background-initialize MemoryManager (heavy ML imports).

        Also updates the GoalWorkerPool reference if it was created before
        memory was ready (pool passes memory=None until this completes).
        """
        try:
            mem = MemoryManager()
            self.set_memory(mem)
            # Update worker pool if it was created before memory finished loading
            if self.goal_worker_pool:
                self.goal_worker_pool._memory = mem
            _mem_count = mem.get_stats().get("long_term_count", 0)
            logger.info("Long-term memory initialized (%d entries)", _mem_count)
        except Exception as e:
            logger.warning("Long-term memory unavailable: %s", e)

    def _load_identity(self) -> dict:
        """Load identity configuration from config/archi_identity.yaml."""
        identity_file = _base_path() / "config" / "archi_identity.yaml"
        if not identity_file.exists():
            logger.warning("No identity file found at %s", identity_file)
            return {}
        try:
            with open(identity_file, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error("Error loading identity: %s", e)
            return {}

    def _load_project_context(self) -> dict:
        """Load dynamic project context from data/project_context.json.

        If the loaded context has no active_projects, auto-populate by
        scanning workspace/projects/ (session 42).
        """
        from src.utils.project_context import load, auto_populate
        ctx = load()
        if not ctx.get("active_projects"):
            logger.info("Project context empty — auto-populating from workspace/projects/")
            ctx = auto_populate()
        return ctx

    def _load_prime_directive(self) -> str:
        """Load the Prime Directive text from config/prime_directive.txt."""
        directive_file = _base_path() / "config" / "prime_directive.txt"
        if not directive_file.exists():
            logger.warning("No Prime Directive found at %s", directive_file)
            return ""
        try:
            with open(directive_file, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error("Error loading Prime Directive: %s", e)
            return ""

    # -- Activity tracking & idle detection ---

    def mark_activity(self):
        """Mark that user activity occurred (resets idle timer).

        In the API-only world, we do NOT interrupt an active cycle
        when the user sends a message — background work and chat can
        coexist since we're not competing for a local GPU anymore.
        """
        self.last_activity = datetime.now()

    def is_idle(self) -> bool:
        """Check if enough time has passed since last activity/cycle."""
        idle_time = (datetime.now() - self.last_activity).total_seconds()
        return idle_time >= self.interval

    def set_interval(self, seconds: int) -> str:
        """Change the heartbeat interval at runtime. Returns confirmation."""
        old = self.interval
        self.interval = max(60, seconds)
        logger.info("Heartbeat interval changed: %ds → %ds", old, self.interval)
        mins = self.interval / 60
        if mins == int(mins):
            return f"Heartbeat interval set to {int(mins)} minute{'s' if mins != 1 else ''}."
        return f"Heartbeat interval set to {mins:.1f} minutes."

    def get_interval(self) -> int:
        """Return the current heartbeat interval in seconds."""
        return self.interval

    # Back-compat aliases (discord_bot command parsing, tests)
    set_idle_threshold = set_interval
    get_idle_threshold = get_interval

    # -- Autonomous mode setup ---

    def enable_autonomous_mode(self, goal_manager: GoalManager) -> None:
        """Enable autonomous task execution during cycles.

        Creates the GoalWorkerPool for concurrent goal execution.
        The pool requires a router, so if one isn't set yet it will be
        created lazily on first use.
        """
        self.goal_manager = goal_manager
        self.autonomous_mode = True

        # Create the worker pool — router may be set later via set_router()
        router = self._get_router()
        if router:
            self.goal_worker_pool = GoalWorkerPool(
                goal_manager=goal_manager,
                router=router,
                learning_system=self.learning_system,
                overnight_results=self._overnight_results,
                save_overnight_results=self._save_overnight_results_callback,
                memory=self.memory,  # may be None; updated by _init_memory when ready
                on_clear_suggest_cooldown=self.clear_suggest_cooldown,
            )
            logger.info("Autonomous execution mode ENABLED (with worker pool)")
        else:
            logger.info("Autonomous execution mode ENABLED (pool deferred until router available)")

    def kick(self, goal_id: Optional[str] = None, reactive: bool = False) -> None:
        """Signal that new work is available — start immediately.

        If a goal_id is provided and the worker pool is available, the goal
        is submitted directly to the pool for zero-latency start.  Otherwise
        falls back to back-dating last_activity so the monitor loop picks
        it up on the next tick.

        Args:
            goal_id: Goal to submit. If None, just triggers the monitor loop.
            reactive: True for user-requested goals (Phase 5 priority).
                      Reactive goals get worker slots before proactive ones.
        """
        if goal_id and self.goal_worker_pool:
            self.goal_worker_pool.submit_goal(goal_id, reactive=reactive)
            logger.info("Goal %s submitted directly to worker pool [%s]",
                        goal_id, "reactive" if reactive else "proactive")
        else:
            from datetime import timedelta
            self.last_activity = datetime.now() - timedelta(seconds=self.interval + 1)
            logger.info("Heartbeat kicked — will start on next check")

    def queue_task(self, task: Dict[str, Any]):
        """Add a task to the work queue."""
        task["queued_at"] = datetime.now().isoformat()
        self.task_queue.append(task)
        logger.info("Queued task: %s", task.get("description", "Unknown"))

    def set_router(self, router: Any) -> None:
        """Use shared ModelRouter (avoids loading model again).

        Also initializes the worker pool if autonomous mode is enabled
        but the pool wasn't created yet (because router wasn't available).
        """
        self._router = router

        # Late-init the worker pool if autonomous mode was enabled before router
        if self.autonomous_mode and self.goal_manager and not self.goal_worker_pool:
            self.goal_worker_pool = GoalWorkerPool(
                goal_manager=self.goal_manager,
                router=router,
                learning_system=self.learning_system,
                overnight_results=self._overnight_results,
                save_overnight_results=self._save_overnight_results_callback,
                memory=self.memory,  # may be None; updated by _init_memory when ready
                on_clear_suggest_cooldown=self.clear_suggest_cooldown,
            )
            logger.info("GoalWorkerPool created (late-init via set_router)")

    def _get_router(self) -> Any:
        """Return shared or lazy-load ModelRouter for task execution."""
        if not hasattr(self, "_router") or self._router is None:
            try:
                from src.models.router import ModelRouter
                self._router = ModelRouter()
                logger.info("Heartbeat: model router initialized (lazy)")
            except Exception as e:
                logger.warning("Heartbeat: router not available: %s", e)
                self._router = None
        return self._router

    def _all_providers_down(self) -> bool:
        """Check if all LLM providers are down (Phase 8).

        Used to skip cycles that would just fail.  Returns False
        if the router isn't available (can't know provider state).
        """
        router = self._get_router()
        if router is None:
            return False  # Can't determine — assume not down
        try:
            return router.all_providers_down()
        except AttributeError:
            return False  # Router doesn't have fallback chain yet

    # -- Monitoring loop ---

    def start(self):
        """Start background thread that watches for idle periods."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            logger.warning("Heartbeat monitoring already running")
            return

        self.stop_flag.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("Heartbeat started")

    # Back-compat alias (archi_service, tests)
    start_monitoring = start

    def stop(self):
        """Stop heartbeat monitoring, worker pool, and flush pending data."""
        self.stop_flag.set()
        # Signal worker pool to stop (non-blocking — workers will finish
        # their current API call naturally; process exits via os._exit).
        if self.goal_worker_pool:
            try:
                self.goal_worker_pool.shutdown()
            except Exception as e:
                logger.debug("Worker pool shutdown error: %s", e)
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        if self.learning_system:
            try:
                self.learning_system.flush()
            except Exception as e:
                logger.debug("Learning system flush failed: %s", e)
        logger.info("Heartbeat stopped")

    # Back-compat alias (archi_service, tests)
    stop_monitoring = stop

    def _should_run_cycle(self) -> bool:
        """Decide whether a cycle would accomplish anything.

        Returns False (skip) when there's no pending work AND the
        suggest-work cooldown hasn't expired yet — avoids the pattern of
        waking up every 30 s just to discover there's nothing to do.

        Phase 8: Also returns False when all LLM providers are down —
        no point running model calls that will all fail.
        """
        # Phase 8: Skip if all providers are down (avoid burning budget on retries)
        if self._all_providers_down():
            logger.info("All LLM providers down — skipping cycle")
            return False

        if self._has_pending_work():
            return True  # Always run if there are goals/tasks to execute

        # Workers are busy executing goals — don't suggest more work
        if self.goal_worker_pool and self.goal_worker_pool.is_working():
            return False

        # No work — only worth running if suggest_work cooldown has expired
        if self._last_suggest_time and (
            datetime.now() - self._last_suggest_time
        ).total_seconds() < self._suggest_cooldown:
            return False  # Cooldown active, nothing useful to do

        return True  # Cooldown expired or never suggested — run to ask user

    def _get_system_monitor(self) -> Any:
        """Lazy-init the system monitor (avoids import at module load)."""
        if self._system_monitor is None:
            try:
                from src.monitoring.system_monitor import SystemMonitor
                from src.utils.config import get_monitoring
                cfg = get_monitoring()
                self._system_monitor = SystemMonitor(
                    cpu_threshold=float(cfg.get("cpu_threshold", 80)),
                    memory_threshold=float(cfg.get("memory_threshold", 90)),
                    temp_threshold=float(cfg.get("temp_threshold", 80)),
                    disk_threshold=float(cfg.get("disk_threshold", 90)),
                )
            except Exception as e:
                logger.debug("System monitor unavailable: %s", e)
        return self._system_monitor

    def _monitor_loop(self):
        """Background thread: the heartbeat.

        Each tick:
        1. Emergency stop check
        2. Hardware throttle check (sleep longer if overloaded)
        3. If idle long enough → run a cycle

        Sleeps in small chunks (``_POLL_CHUNK``) so stop_flag / kick()
        are noticed promptly.
        """
        while not self.stop_flag.is_set():
            # 1. Emergency stop
            if self._emergency_stop.check():
                logger.critical("Exiting due to emergency stop")
                self.stop_flag.set()
                break

            # 2. Hardware throttle — double the sleep chunk if overloaded
            sleep_chunk = self._POLL_CHUNK
            monitor = self._get_system_monitor()
            if monitor and monitor.should_throttle():
                sleep_chunk *= 2.0

            # 3. Run a cycle if idle and not already running
            if self.is_idle() and not self.is_running_cycle:
                if self._should_run_cycle():
                    logger.info("Starting cycle")
                    self._run_cycle()

            # Chunked sleep for stop_flag responsiveness
            self._chunked_sleep(sleep_chunk)

    def _chunked_sleep(self, chunk: float) -> None:
        """Sleep in small chunks so stop_flag is noticed quickly."""
        if not self.stop_flag.is_set():
            self.stop_flag.wait(timeout=chunk)

    # -- Sleep gap detection ---

    def _check_sleep_gap(self, phase_name: str, phase_start: float,
                         max_expected_seconds: float = 600) -> bool:
        """Detect if the system likely slept during a cycle phase.

        Returns True if a sleep gap was detected (caller should abort).
        """
        elapsed = time.monotonic() - phase_start
        if elapsed > max_expected_seconds:
            logger.warning(
                "SLEEP GAP DETECTED in phase '%s': took %.0fs (max expected %.0fs). "
                "Aborting cycle to avoid stale state.",
                phase_name, elapsed, max_expected_seconds,
            )
            return True
        return False

    # -- Main cycle orchestration ---

    def _save_overnight_results_callback(self) -> None:
        """Callback for autonomous_executor to persist overnight results."""
        reporting.save_overnight_results(
            self._overnight_results, self._overnight_results_path,
        )

    def _has_pending_work(self) -> bool:
        """Check if there are active goals with tasks ready to execute."""
        if not self.goal_manager:
            return False
        # Check for queued manual tasks
        if self.task_queue:
            return True
        # Snapshot to avoid RuntimeError from concurrent dict modification
        goals = list(self.goal_manager.goals.values())
        # Check for goals with ready tasks
        for goal in goals:
            if not goal.is_complete() and goal.get_ready_tasks():
                return True
        # Check for undecomposed goals (they'll produce tasks once decomposed)
        for goal in goals:
            if not goal.is_decomposed and not goal.is_complete():
                return True
        return False

    def _try_proactive_initiative(self) -> bool:
        """Attempt to self-initiate a small work item from active projects.

        Returns True if a goal was created and submitted, False otherwise.
        Respects quiet hours, daily budget, and max-per-day limits.
        """
        try:
            from src.utils.time_awareness import is_quiet_hours
            from src.core.initiative_tracker import InitiativeTracker
            from src.interfaces.discord_bot import send_notification, is_outbound_ready
        except ImportError as e:
            logger.debug("Proactive initiative unavailable: %s", e)
            return False

        tracker = InitiativeTracker()

        if tracker.respect_quiet_hours and is_quiet_hours():
            logger.debug("Proactive initiative skipped (quiet hours)")
            return False

        if not tracker.can_initiate():
            logger.debug(
                "Proactive initiative skipped (budget: $%.2f/$%.2f, count: %d/%d)",
                tracker.spend_today, tracker.daily_budget,
                tracker.count_today, tracker.max_per_day,
            )
            return False

        if not self.goal_worker_pool:
            return False

        # Generate suggestions (same as _ask_user_for_work, but we pick one)
        suggestions, self._last_suggest_time = idea_generator.suggest_work(
            router=self._get_router(),
            goal_manager=self.goal_manager,
            learning_system=self.learning_system,
            project_context=self.project_context,
            last_suggest=self._last_suggest_time,
            stop_flag=self.stop_flag,
            memory=self.memory,
            cooldown_secs=self._suggest_cooldown,
        )

        if not suggestions:
            logger.debug("Proactive initiative: no good ideas found")
            return False

        # Pick the top-scoring suggestion
        chosen = suggestions[0]
        title = chosen.get("description", "")[:200]
        category = chosen.get("category", "general")

        if not title:
            return False

        # Extract rich context from the opportunity scanner
        user_value = chosen.get("user_value", "")
        reasoning = chosen.get("reasoning", "")
        source = chosen.get("project_link", "")

        # Use user_value if available, fall back to generic
        why = user_value or f"Relates to your {category} work — I thought this could help."

        # Estimate cost (conservative: $0.15 per small task)
        est_cost = 0.20
        if tracker.budget_remaining() < est_cost:
            logger.debug("Proactive initiative: insufficient budget ($%.2f remaining)", tracker.budget_remaining())
            return False

        # Create the goal
        goal = self.goal_manager.create_goal(
            description=title,
            user_intent=f"Self-initiated: {why}",
            priority=4,  # Lower than user-requested work (priority 5)
        )

        # Log the initiative
        tracker.record(
            title=title,
            why_jesse_cares=why,
            estimated_cost=est_cost,
            goal_id=goal.goal_id,
        )

        # Submit to worker pool
        self.goal_worker_pool.submit_goal(goal.goal_id)

        # Notify Jesse (after starting, not asking permission)
        if is_outbound_ready():
            from src.core.notification_formatter import format_initiative_announcement
            fmt = format_initiative_announcement(
                title, why, router=self._get_router(),
                reasoning=reasoning, source=source,
            )
            send_notification(fmt["message"])

        logger.info(
            "Proactive initiative created: %s (goal %s, est $%.2f)",
            title[:60], goal.goal_id, est_cost,
        )
        return True

    def _ask_user_for_work(self) -> None:
        """Brainstorm suggestions and ask the user what to work on via Discord.

        Uses the Notification Formatter (Phase 3) for natural, varied messages.
        Sends a message with numbered suggestions and returns immediately.
        The user's reply is handled by discord_bot.py which creates a goal.
        """
        try:
            from src.interfaces.discord_bot import send_notification, is_outbound_ready
        except ImportError:
            return

        if not is_outbound_ready():
            logger.debug("Discord not ready — skipping work suggestion")
            return

        # Generate suggestions
        suggestions, self._last_suggest_time = idea_generator.suggest_work(
            router=self._get_router(),
            goal_manager=self.goal_manager,
            learning_system=self.learning_system,
            project_context=self.project_context,
            last_suggest=self._last_suggest_time,
            stop_flag=self.stop_flag,
            memory=self.memory,
            cooldown_secs=self._suggest_cooldown,
        )

        if not suggestions:
            # Cooldown not met or no good ideas — just send a simple prompt
            if self._last_suggest_time and (
                datetime.now() - self._last_suggest_time
            ).total_seconds() < self._suggest_cooldown:
                logger.info("No suggestions and cooldown active — staying quiet")
                return

            from src.core.notification_formatter import format_idle_prompt
            fmt = format_idle_prompt(router=self._get_router())
            send_notification(fmt["message"])
            self._pending_suggestions = []
            return

        # Mark any previous pending suggestions as ignored before replacing.
        # If previous suggestions went unanswered, increase the cooldown.
        if getattr(self, '_pending_batch_id', None):
            self._unanswered_suggest_count += 1
            new_cooldown = min(
                self._suggest_cooldown_base * (2 ** self._unanswered_suggest_count),
                self._suggest_cooldown_max,
            )
            if new_cooldown != self._suggest_cooldown:
                logger.info(
                    "Suggestion cooldown increased: %ds → %ds "
                    "(%d unanswered rounds)",
                    self._suggest_cooldown, new_cooldown,
                    self._unanswered_suggest_count,
                )
                self._suggest_cooldown = new_cooldown
            try:
                from src.core.idea_history import get_idea_history
                get_idea_history().mark_batch_ignored(self._pending_batch_id)
            except Exception:
                pass

        # Store for discord_bot to reference when user replies with a number
        self._pending_suggestions = suggestions

        # Also keep in recent suggestions for late-reply recovery
        self._recent_suggestions.extend(suggestions)
        # Trim to last 20
        if len(self._recent_suggestions) > 20:
            self._recent_suggestions = self._recent_suggestions[-20:]

        # Record these as presented in idea history
        try:
            from src.core.idea_history import get_idea_history
            descs = [s.get("description", "") for s in suggestions if s.get("description")]
            self._pending_batch_id = get_idea_history().record_presented(descs)
        except Exception:
            self._pending_batch_id = None

        from src.core.notification_formatter import format_suggestions
        fmt = format_suggestions(
            suggestions=suggestions,
            router=self._get_router(),
        )
        send_notification(fmt["message"])
        logger.info(
            "Sent %d work suggestions to user (next cooldown: %ds)",
            len(suggestions), self._suggest_cooldown,
        )

    def clear_suggest_cooldown(self) -> None:
        """Nullify last suggest time so the next cycle can suggest immediately.

        Called by GoalWorkerPool when a self-initiated goal fails —
        Archi shouldn't sit idle for an hour after its own initiative didn't
        work out.  Unlike reset_suggest_cooldown(), this does not reset the
        exponential backoff counter.
        """
        self._last_suggest_time = None
        logger.info("Suggest cooldown cleared (next cycle can suggest immediately)")

    def reset_suggest_cooldown(self) -> None:
        """Reset suggestion cooldown to base value.

        Called when the user sends any message — they're active, so Archi
        can go back to the normal suggestion interval.
        """
        if self._suggest_cooldown != self._suggest_cooldown_base:
            logger.info(
                "Suggestion cooldown reset: %ds → %ds (user active)",
                self._suggest_cooldown, self._suggest_cooldown_base,
            )
        self._suggest_cooldown = self._suggest_cooldown_base
        self._unanswered_suggest_count = 0

    def _run_cycle(self):
        """Execute a background cycle.

        Flow:
          1. Morning report (if morning and not sent today)
          2. If pending work exists → execute tasks
          3. If no work → ask user for work (with suggestions)
          4. Learning review
          5. Periodic synthesis (informational only, every 10 cycles)
          6. Periodic file cleanup (every 10 cycles, offset by 5)
        """
        self.is_running_cycle = True
        cycle_start = datetime.now()

        try:
            logger.info("=== CYCLE START ===")

            # Phase 0: Morning report (send once per morning, 6-9 AM)
            current_hour = cycle_start.hour
            if 6 <= current_hour <= 9 and self._morning_report_sent != cycle_start.date():
                reporting.send_morning_report(
                    self._overnight_results, self._overnight_results_path,
                    router=self._get_router(),
                )
                self._morning_report_sent = cycle_start.date()

            # Phase 1: Dispatch work to pool OR ask user for work
            _phase_t0 = time.monotonic()
            tasks_processed = 0
            _results_before = len(self._overnight_results)

            if self._has_pending_work() and self.goal_worker_pool:
                # Submit any unstarted goals to the worker pool
                _submitted = 0
                for goal in list(self.goal_manager.goals.values()):
                    if self.stop_flag.is_set():
                        break
                    if not goal.is_complete():
                        if self.goal_worker_pool.submit_goal(goal.goal_id):
                            _submitted += 1
                if _submitted:
                    logger.info("Dispatched %d goals to worker pool", _submitted)
                    tasks_processed = _submitted  # Approximate — actual tasks run in workers

                # Also handle legacy manual queue tasks (if any)
                while self.task_queue and not self.stop_flag.is_set():
                    task = self.task_queue.pop(0)
                    try:
                        desc = task.get("description", "") or str(task.get("type", "unknown"))
                        logger.info("Executing queued task: %s", desc)
                        result = autonomous_executor._execute_queued_task(
                            task, self._get_router(), self.goal_manager,
                        )
                        if result.get("executed"):
                            tasks_processed += 1
                    except Exception as e:
                        logger.error("Queued task error: %s", e)

            elif self._has_pending_work():
                # Fallback: no pool available, use old sequential executor
                tasks_processed = autonomous_executor.process_task_queue(
                    task_queue=self.task_queue,
                    goal_manager=self.goal_manager,
                    router=self._get_router(),
                    learning_system=self.learning_system,
                    stop_flag=self.stop_flag,
                    autonomous_mode=self.autonomous_mode,
                    overnight_results=self._overnight_results,
                    save_overnight_results=self._save_overnight_results_callback,
                    memory=self.memory,
                )
            else:
                # Nothing to do — ask user first; only go proactive if
                # suggestions have gone unanswered (user isn't engaging).
                if not self.stop_flag.is_set():
                    if self._unanswered_suggest_count > 0:
                        # User ignored previous suggestions — try doing
                        # something useful on our own instead of nagging.
                        initiative_started = self._try_proactive_initiative()
                        if not initiative_started:
                            self._ask_user_for_work()
                    else:
                        self._ask_user_for_work()

            _results_after = len(self._overnight_results)
            _this_cycle_results = self._overnight_results[_results_before:_results_after]

            if self._check_sleep_gap("work_phase", _phase_t0, max_expected_seconds=900):
                logger.info("=== CYCLE ABORTED (sleep gap) ===")
                self.cycle_history.append({
                    "started_at": cycle_start.isoformat(),
                    "duration_seconds": (datetime.now() - cycle_start).total_seconds(),
                    "tasks_processed": tasks_processed,
                    "insights": 0,
                    "interrupted": True,
                    "sleep_gap": True,
                })
                if len(self.cycle_history) > _MAX_CYCLE_HISTORY:
                    self.cycle_history = self.cycle_history[-_MAX_CYCLE_HISTORY:]
                return

            # Phase 2: Review recent history (learning)
            insights = self._review_history()

            # Phase 3: Periodic synthesis (every 10 cycles, informational only)
            if not self.stop_flag.is_set() and len(self.cycle_history) % 10 == 0 and len(self.cycle_history) > 0:
                try:
                    self._run_synthesis()
                except Exception as se:
                    logger.debug("Synthesis skipped: %s", se)

            # Phase 4: Periodic stale file cleanup (every 10 cycles, offset by 5)
            if not self.stop_flag.is_set() and len(self.cycle_history) % 10 == 5:
                try:
                    self._run_file_cleanup()
                except Exception as fce:
                    logger.debug("File cleanup skipped: %s", fce)

            cycle_duration = (datetime.now() - cycle_start).total_seconds()

            # Record cycle (capped to prevent unbounded growth;
            # older entries are persisted in data/dream_log.jsonl).
            self.cycle_history.append({
                "started_at": cycle_start.isoformat(),
                "duration_seconds": cycle_duration,
                "tasks_processed": tasks_processed,
                "insights": insights,
                "interrupted": self.stop_flag.is_set(),
            })
            if len(self.cycle_history) > _MAX_CYCLE_HISTORY:
                self.cycle_history = self.cycle_history[-_MAX_CYCLE_HISTORY:]

            logger.info("=== CYCLE END (duration: %.1fs) ===", cycle_duration)

            # Persist cycle summary to JSONL
            try:
                log_path = _base_path() / "data" / "dream_log.jsonl"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                task_summaries = []
                for r in _this_cycle_results:
                    task_summaries.append({
                        "task": r.get("task", ""),
                        "goal": r.get("goal", ""),
                        "success": r.get("success", False),
                        "files": [os.path.basename(f) for f in r.get("files_created", [])[:3]],
                    })
                entry = {
                    "ts": cycle_start.isoformat(),
                    "duration_s": round(cycle_duration, 1),
                    "tasks_done": tasks_processed,
                    "tasks": task_summaries,
                    "insights": len(insights) if isinstance(insights, list) else 0,
                }
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception:
                pass

            # Accumulate results for hourly notification
            if _this_cycle_results:
                self._hourly_task_results.extend(_this_cycle_results)

            _HOURLY_INTERVAL = 3600
            _since_last = time.monotonic() - self._last_hourly_notify
            if self._hourly_task_results and _since_last >= _HOURLY_INTERVAL:
                reporting.send_hourly_summary(
                    self._hourly_task_results, router=self._get_router(),
                )
                self._last_hourly_notify = time.monotonic()
            elif tasks_processed > 0:
                logger.info(
                    "Cycle: %d tasks done (hourly summary in %.0f min)",
                    tasks_processed,
                    max(0, (_HOURLY_INTERVAL - _since_last) / 60),
                )

        except Exception as e:
            logger.error("Cycle error: %s", e, exc_info=True)
        finally:
            self.is_running_cycle = False
            # Don't clear stop_flag here — stop() owns the flag lifecycle.
            # Clearing would race: monitor loop could start a new cycle
            # before _monitor_thread.join() completes.
            self.last_activity = datetime.now()

    # -- Learning & synthesis ---

    def _review_history(self) -> List[str]:
        """Review recent actions and extract insights via learning system."""
        insights = []

        if self.stop_flag.is_set():
            return insights

        logger.info("Reviewing recent history for insights...")

        try:
            if (
                self._router
                and len(self.learning_system.experiences) >= 5
            ):
                patterns = self.learning_system.extract_patterns(self._router)
                if patterns:
                    insights.extend(patterns[:3])
                suggestions = self.learning_system.get_improvement_suggestions(
                    self._router
                )
                if suggestions:
                    insights.extend(suggestions[:2])
        except (RuntimeError, OSError, MemoryError) as e:
            logger.warning("Learning system skipped: %s", e, exc_info=False)
        except Exception as e:
            logger.debug("Learning system review skipped: %s", e)

        return insights

    def _run_synthesis(self) -> None:
        """Combine findings from multiple completed goals into insights.

        Runs every 10 cycles. Informational only — identifies themes
        and logs them, but does NOT create follow-up goals.
        """
        router = self._get_router()
        if not router or not self.goal_manager:
            return

        if self.stop_flag.is_set():
            return

        completed = [
            g for g in self.goal_manager.goals.values()
            if g.is_complete()
        ]
        if len(completed) < 2:
            logger.debug("Synthesis skipped: fewer than 2 completed goals")
            return

        logger.info("=== SYNTHESIS START (%d completed goals) ===", len(completed))

        goal_lines = "\n".join(
            f"- {g.description[:100]}" for g in completed[-8:]
        )

        prompt = f"""You are Archi, reviewing completed research and tasks for Jesse.

Completed goals:
{goal_lines}

Identify:
1. Common themes across this work
2. An integrated insight or action plan that combines multiple findings

Return ONLY a JSON object:
{{
  "theme": "Overarching theme in 1 sentence",
  "integrated_insight": "How these findings connect (2-3 sentences)"
}}
JSON only:"""

        try:
            resp = router.generate(
                prompt=prompt, max_tokens=300, temperature=0.4,
            )
            text = resp.get("text", "")

            from src.utils.parsing import extract_json
            parsed = extract_json(text)
            if not parsed:
                return

            # Save to synthesis log (informational only)
            synthesis_path = _base_path() / "data" / "synthesis_log.jsonl"
            synthesis_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now().isoformat(),
                "goals_synthesized": len(completed),
                **parsed,
            }
            with open(synthesis_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            logger.info("Synthesis: theme='%s'", parsed.get("theme", "")[:80])

            # Store synthesis insight in long-term memory
            if self.memory and parsed.get("integrated_insight"):
                try:
                    self.memory.store_long_term(
                        text=(f"Cycle insight: {parsed.get('theme', '')} "
                              f"— {parsed.get('integrated_insight', '')}"),
                        memory_type="cycle_summary",
                        metadata={"goals_synthesized": len(completed)},
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.debug("Synthesis failed: %s", e)

    def _run_file_cleanup(self) -> None:
        """Check for stale workspace files and propose cleanup via Discord.

        Runs every ~10 cycles.  Asks the user for approval before
        deleting anything.  Supports "never <path>" to mark files persistent.
        """
        logger.info("=== FILE CLEANUP CHECK ===")
        try:
            from src.core.file_tracker import FileTracker

            tracker = FileTracker()
            stale = tracker.get_stale_files()

            if not stale:
                logger.info("No stale files found")
                return

            logger.info("Found %d stale files, requesting approval", len(stale))

            from src.interfaces.discord_bot import request_cleanup_approval

            response = request_cleanup_approval(stale, timeout=120)

            if response == "yes":
                deleted = 0
                for path in stale:
                    if tracker.remove_file(path):
                        deleted += 1
                logger.info("Cleaned up %d/%d stale files", deleted, len(stale))
                from src.core.reporting import _notify
                _notify(f"🗑️ Cleaned up {deleted} stale files.")

            elif response.startswith("never:"):
                never_path = response[6:]
                matched = None
                for sp in stale:
                    if never_path in sp or os.path.basename(sp) == never_path:
                        matched = sp
                        break
                if matched:
                    tracker.mark_persistent(matched)
                    from src.core.reporting import _notify
                    _notify(f"📌 Marked `{matched}` as never-purge. Skipping cleanup this time.")
                else:
                    from src.core.reporting import _notify
                    _notify(f"Couldn't find `{never_path}` in stale list. Skipping cleanup.")
                logger.info("File marked persistent: %s (matched: %s)", never_path, matched)

            elif response == "no":
                logger.info("File cleanup denied by user")

            else:
                logger.info("File cleanup timed out — skipping (safe default)")

        except Exception as e:
            logger.debug("File cleanup failed: %s", e)

    # -- Status ---

    def get_status(self) -> Dict[str, Any]:
        """Get current heartbeat status."""
        idle_time = (datetime.now() - self.last_activity).total_seconds()
        status = {
            "is_running_cycle": self.is_running_cycle,
            "is_idle": self.is_idle(),
            "idle_seconds": idle_time,
            "queued_tasks": len(self.task_queue),
            "total_cycles": len(self.cycle_history),
            "last_activity": self.last_activity.isoformat(),
            "overnight_results": len(self._overnight_results),
            "morning_report_sent_today": self._morning_report_sent == datetime.now().date(),
            "last_suggest": self._last_suggest_time.isoformat() if self._last_suggest_time else None,
            "pending_suggestions": len(self._pending_suggestions),
            "all_providers_down": self._all_providers_down(),
        }
        return status
