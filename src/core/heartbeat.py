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
from src.utils.config import get_user_name
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

    def __init__(self, interval_seconds: int = 300,
                 min_interval: int = 300, max_interval: int = 7200):
        self.interval = interval_seconds
        self._base_interval = interval_seconds  # Configured default (reset target)
        self._min_interval = min_interval        # Adaptive floor (session 115)
        self._max_interval = max_interval        # Adaptive ceiling (session 115)
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
        # Recent conversation starters for dedup (session 181, enhanced session 183)
        self._recent_starters: List[str] = []
        self._recent_starter_topics: List[str] = []  # Extracted topic keywords
        # Forced category rotation for starter diversity (session 189).
        # Cycles through categories so no two consecutive starters share a topic.
        self._starter_category_index: int = 0

        # Engagement acknowledgment window for scheduled tasks (session 198).
        # Each entry: {"task_id": str, "fired_at": float (time.time())}
        # After 30 min with no user response, mark as ignored.
        self._pending_ack_tasks: List[Dict[str, Any]] = []
        self._ACK_WINDOW_SECONDS: int = 1800  # 30 minutes

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
        """Mark that user activity occurred (resets idle timer + interval).

        In the API-only world, we do NOT interrupt an active cycle
        when the user sends a message — background work and chat can
        coexist since we're not competing for a local GPU anymore.

        Also resets the adaptive interval to base — user is active, so
        Archi should be responsive when they go idle again.
        """
        self.last_activity = datetime.now()
        if self.interval != self._base_interval:
            logger.info(
                "Adaptive interval reset: %ds → %ds (user active)",
                self.interval, self._base_interval,
            )
            self.interval = self._base_interval

    def is_idle(self) -> bool:
        """Check if enough time has passed since last activity/cycle."""
        idle_time = (datetime.now() - self.last_activity).total_seconds()
        return idle_time >= self.interval

    def _is_user_recently_active(self, window_seconds: int = 300) -> bool:
        """Check if the user sent a message within the last *window_seconds*.

        Session 230: Used to suppress non-essential dream cycle notifications
        (explorations, opinion revisions, project updates, conversation
        starters) while the user is actively chatting — they're distracting
        mid-conversation.  Task execution and morning reports still proceed;
        only the unsolicited outbound chatter is deferred.
        """
        idle_time = (datetime.now() - self.last_activity).total_seconds()
        return idle_time < window_seconds

    def set_interval(self, seconds: int) -> str:
        """Change the heartbeat interval at runtime. Returns confirmation.

        Also updates the base interval so adaptive scheduling respects
        user-configured values.
        """
        old = self.interval
        self.interval = max(60, seconds)
        self._base_interval = self.interval
        logger.info("Heartbeat interval changed: %ds → %ds", old, self.interval)
        mins = self.interval / 60
        if mins == int(mins):
            return f"Heartbeat interval set to {int(mins)} minute{'s' if mins != 1 else ''}."
        return f"Heartbeat interval set to {mins:.1f} minutes."

    def get_interval(self) -> int:
        """Return the current heartbeat interval in seconds."""
        return self.interval

    def _adapt_interval(self, was_productive: bool) -> None:
        """Adjust the idle interval based on cycle outcome (session 115).

        Productive cycles (tasks executed) reset to the base interval.
        Idle cycles (no work found) double the interval up to _MAX_INTERVAL.
        This avoids waking every 15 min just to discover there's nothing to do.
        """
        old = self.interval
        if was_productive:
            self.interval = self._base_interval
        else:
            self.interval = min(self.interval * 2, self._max_interval)
        if old != self.interval:
            logger.info(
                "Adaptive interval: %ds → %ds (%s)",
                old, self.interval,
                "productive — reset" if was_productive else "idle — extended",
            )

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
        _last_watchdog = time.monotonic()
        _consecutive_errors = 0
        logger.info("Monitor loop thread started (tid=%s)", threading.current_thread().name)
        try:
            while not self.stop_flag.is_set():
                sleep_chunk = self._POLL_CHUNK  # Default; may be adjusted below
                try:
                    # Watchdog: periodic "still alive" log
                    _now_mono = time.monotonic()
                    if _now_mono - _last_watchdog >= self.interval:
                        _idle_secs = (datetime.now() - self.last_activity).total_seconds()
                        _cooldown_left = 0
                        if self._last_suggest_time:
                            _cooldown_left = max(0, self._suggest_cooldown - (
                                datetime.now() - self._last_suggest_time
                            ).total_seconds())
                        logger.info(
                            "WATCHDOG: alive, idle=%.0fs, running_cycle=%s, "
                            "cooldown_left=%.0fs, pending_work=%s",
                            _idle_secs, self.is_running_cycle,
                            _cooldown_left, self._has_pending_work(),
                        )
                        _last_watchdog = _now_mono

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

                    # 2.5. Check scheduled tasks (every tick, independent of cycles)
                    self._check_scheduled_tasks()

                    # 2.6. Check engagement timeouts (session 198)
                    self._check_engagement_timeouts()

                    # 3. Run a cycle if idle and not already running
                    if self.is_idle() and not self.is_running_cycle:
                        if self._should_run_cycle():
                            logger.info("Starting cycle")
                            self._run_cycle()

                    _consecutive_errors = 0  # Reset on successful tick

                except Exception as tick_err:
                    _consecutive_errors += 1
                    logger.error(
                        "Monitor loop tick error (#%d): %s",
                        _consecutive_errors, tick_err, exc_info=True,
                    )
                    if _consecutive_errors >= 5:
                        logger.critical(
                            "Monitor loop: %d consecutive errors, backing off 60s",
                            _consecutive_errors,
                        )
                        self.stop_flag.wait(timeout=60)

                # Chunked sleep for stop_flag responsiveness
                self._chunked_sleep(sleep_chunk)
        except Exception as fatal:
            logger.critical(
                "Monitor loop FATAL — thread dying: %s", fatal, exc_info=True,
            )
        finally:
            logger.warning("Monitor loop thread exiting (stop_flag=%s)", self.stop_flag.is_set())

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

    # -- Budget trajectory ---

    def _check_budget_trajectory(self) -> str:
        """Check projected budget trajectory before starting work.

        Returns the throttle level: "none", "warn", "throttle", or "stop".
        On "throttle", logs a warning and reduces effective parallelism.
        On "stop", logs an error.
        """
        try:
            from src.monitoring.cost_tracker import get_cost_tracker
            tracker = get_cost_tracker()
            proj = tracker.get_budget_projection()
        except Exception as e:
            logger.debug("Budget projection unavailable: %s", e)
            return "none"

        throttle = proj.get("throttle", "none")

        if throttle == "stop":
            logger.warning(
                "BUDGET TRAJECTORY: STOP — daily $%.2f/$%.2f (projected $%.2f), "
                "monthly $%.2f/$%.2f (projected $%.2f). Skipping non-essential work.",
                proj["daily_spent"], proj["daily_budget"], proj["daily_projected"],
                proj["monthly_spent"], proj["monthly_budget"], proj["monthly_projected"],
            )
            self._notify_budget_trajectory(proj)
        elif throttle == "throttle":
            logger.warning(
                "BUDGET TRAJECTORY: THROTTLE — daily $%.2f/$%.2f (projected $%.2f, %.0f%%), "
                "monthly $%.2f/$%.2f (projected $%.2f, %.0f%%). Reducing parallelism.",
                proj["daily_spent"], proj["daily_budget"],
                proj["daily_projected"], proj["daily_projected_pct"],
                proj["monthly_spent"], proj["monthly_budget"],
                proj["monthly_projected"], proj["monthly_projected_pct"],
            )
            self._notify_budget_trajectory(proj)
        elif throttle == "warn":
            logger.info(
                "Budget trajectory: daily projected $%.2f/%.2f (%.0f%%), "
                "monthly projected $%.2f/$%.2f (%.0f%%)",
                proj["daily_projected"], proj["daily_budget"], proj["daily_projected_pct"],
                proj["monthly_projected"], proj["monthly_budget"], proj["monthly_projected_pct"],
            )

        return throttle

    def _notify_budget_trajectory(self, proj: Dict[str, Any]) -> None:
        """Send a one-time Discord DM when budget trajectory is concerning.

        Rate-limited to once per 2 hours to avoid spamming.
        """
        now = time.monotonic()
        last = getattr(self, "_last_budget_notify", 0)
        if now - last < 7200:  # 2-hour cooldown
            return
        self._last_budget_notify = now
        try:
            from src.interfaces.discord_bot import send_notification, is_outbound_ready
            if not is_outbound_ready():
                return
            throttle = proj["throttle"]
            if throttle == "stop":
                msg = (
                    f"Heads up — I'm projected to hit the daily budget "
                    f"(${proj['daily_spent']:.2f}/${proj['daily_budget']:.2f} so far, "
                    f"projected ${proj['daily_projected']:.2f}). "
                    f"I'm pausing background work to stay within limits."
                )
            else:
                msg = (
                    f"Budget check: I'm spending at ${proj['hourly_rate']:.3f}/hr, "
                    f"projected ${proj['daily_projected']:.2f}/${proj['daily_budget']:.2f} today. "
                    f"I've slowed down my background work to stay on track."
                )
            send_notification(msg)
        except Exception as e:
            logger.debug("Budget notification failed: %s", e)

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
            # Fallback: generate a small research task from worldview interests.
            # This prevents 0-task cycles when the opportunity scanner/brainstorm
            # can't produce ideas above the quality threshold (session 225).
            suggestions = self._interest_based_fallback()
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

        # Create the goal (returns None if duplicate detected)
        goal = self.goal_manager.create_goal(
            description=title,
            user_intent=f"Self-initiated: {why}",
            priority=4,  # Lower than user-requested work (priority 5)
        )
        if goal is None:
            logger.info("Proactive initiative skipped (duplicate): %s", title[:60])
            return False

        # Log the initiative
        tracker.record(
            title=title,
            why_jesse_cares=why,
            estimated_cost=est_cost,
            goal_id=goal.goal_id,
        )

        # Submit to worker pool
        self.goal_worker_pool.submit_goal(goal.goal_id)

        # Notify the user (after starting, not asking permission)
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

    def _interest_based_fallback(self) -> List[Dict]:
        """Generate a small research task from worldview interests.

        Called when suggest_work() returns empty. Picks the highest-curiosity
        interest that hasn't been explored recently and creates a lightweight
        research task description for it. Returns 0 or 1 suggestion dicts.

        Session 225: prevents 0-task cycles when filters reject everything.
        """
        try:
            from src.core.worldview import get_interests
            interests = get_interests()
        except Exception:
            return []

        if not interests:
            return []

        # Pick the highest-curiosity interest not explored in the last 24h
        import time as _time
        now_ts = _time.time()
        best = None
        for interest in sorted(interests, key=lambda i: i.get("curiosity_level", 0), reverse=True):
            last_exp = interest.get("last_explored", "")
            if last_exp:
                try:
                    from datetime import datetime as _dt
                    exp_time = _dt.fromisoformat(last_exp).timestamp()
                    if now_ts - exp_time < 86400:  # 24 hours
                        continue
                except (ValueError, TypeError):
                    pass
            best = interest
            break

        if not best:
            return []

        topic = best.get("topic", "").strip()
        if not topic:
            return []

        # Check for duplicate goal before suggesting
        if self.goal_manager and idea_generator.is_duplicate_goal(
            f"Research: {topic}", self.goal_manager,
        ):
            return []

        return [{
            "description": f"Research: {topic} — find recent developments, practical insights, or interesting angles",
            "category": "research",
            "reasoning": f"High curiosity ({best.get('curiosity_level', 0):.1f}) interest from worldview. Exploring this builds Archi's knowledge.",
            "score": 0.6,  # Above MIN_SUGGEST_SCORE threshold
            "user_value": f"Following up on your interest in {topic}.",
        }]

    @staticmethod
    def _extract_topic_keywords(text: str) -> List[str]:
        """Extract 2-4 significant topic words from a conversation starter.

        Filters out stop words and common verbs to keep distinctive nouns/topics.
        Used to build a banned-topics list for semantic dedup (session 183).
        """
        _STOP = {
            "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "can", "shall", "about", "up",
            "out", "if", "not", "no", "so", "as", "it", "its", "that", "this",
            "they", "them", "their", "there", "what", "when", "where", "which",
            "who", "how", "all", "each", "every", "both", "few", "more", "most",
            "some", "any", "such", "than", "too", "very", "just", "also", "into",
            "over", "after", "before", "between", "under", "again", "then",
            "here", "why", "way", "because", "through", "during", "while",
            # Common verbs/adjectives that aren't topical
            "hey", "know", "think", "said", "tell", "like", "get", "got", "make",
            "made", "going", "went", "come", "came", "take", "took", "give",
            "gave", "see", "saw", "seem", "feel", "felt", "look", "looked",
            "still", "really", "actually", "pretty", "quite", "ever", "never",
            "something", "anything", "everything", "nothing", "thinking", "heard",
            "mentioned", "remember", "wondering", "noticed", "since", "you",
            "your", "you're", "i'm", "i've", "i'd", "we", "our", "my", "me",
        }
        import re as _re
        words = _re.findall(r"[a-z][a-z'-]+", text.lower())
        keywords = [w for w in words if len(w) > 3 and w not in _STOP]
        # Return up to 4 most distinctive (longest) keywords
        keywords.sort(key=len, reverse=True)
        return keywords[:4]

    # Interest categories for forced rotation (session 189).
    # Each starter MUST be about a different category from the last one.
    # Categories are broad — the model picks a specific angle within the assigned
    # category using user facts and conversation memories.
    _STARTER_CATEGORIES = [
        "puppy / dog training / Border Collie",
        "fitness / exercise / getting active",
        "philosophy / deep thoughts / life questions",
        "cooking / meal prep / nutrition",
        "outdoors / hiking / nature / walking routes",
        "finance / investing / career goals",
        "tech / programming / side projects",
        "health / wellness / self-improvement",
        "hobbies / creative pursuits / woodworking",
        "entertainment / movies / games / music",
    ]

    def _get_next_starter_category(self) -> str:
        """Return the next category in rotation and advance the index.

        Ensures every consecutive starter is about a different category.
        The categories list covers a broad range of Jesse's interests;
        the model picks a specific angle within the assigned category.
        """
        categories = self._STARTER_CATEGORIES
        cat = categories[self._starter_category_index % len(categories)]
        self._starter_category_index = (self._starter_category_index + 1) % len(categories)
        return cat

    def _try_conversation_starter(self) -> bool:
        """Attempt to start a social conversation with the user.

        Uses user facts from UserModel and conversation memories from LanceDB
        to generate a natural, non-work callback or follow-up. Returns True
        if a message was sent, False if nothing felt organic.

        Session 189: forced category rotation — each starter must be about
        a pre-assigned category that cycles through a list of diverse topics.
        """
        try:
            from src.interfaces.discord_bot import send_notification, is_outbound_ready
        except ImportError:
            return False

        if not is_outbound_ready():
            return False

        # Gather user facts
        user_facts: list = []
        try:
            from src.core.user_model import get_user_model
            model = get_user_model()
            user_facts = [f["text"] for f in model.facts[-8:]]
        except Exception:
            pass

        # Gather conversation memories (random-ish query from recent facts)
        conversation_memories: list = []
        if self.memory and user_facts:
            try:
                # Use a random fact as the query seed for variety
                import random
                query = random.choice(user_facts) if user_facts else get_user_name()
                conversation_memories = self.memory.get_conversation_context(query, n_results=3)
            except Exception:
                pass

        if not user_facts and not conversation_memories:
            return False

        # Pick the next category in rotation (session 189)
        required_category = self._get_next_starter_category()

        from src.core.notification_formatter import format_conversation_starter
        fmt = format_conversation_starter(
            user_facts=user_facts,
            conversation_memories=conversation_memories,
            router=self._get_router(),
            recent_starters=self._recent_starters,
            banned_topics=self._recent_starter_topics,
            required_category=required_category,
        )
        if not fmt["message"]:
            return False

        send_notification(fmt["message"])
        # Track for dedup: both full messages and extracted topic keywords (session 183)
        self._recent_starters.append(fmt["message"])
        if len(self._recent_starters) > 10:
            self._recent_starters = self._recent_starters[-10:]
        new_topics = self._extract_topic_keywords(fmt["message"])
        self._recent_starter_topics.extend(new_topics)
        if len(self._recent_starter_topics) > 30:
            self._recent_starter_topics = self._recent_starter_topics[-30:]
        # Use the suggest cooldown so we don't spam conversation starters
        self._last_suggest_time = datetime.now()
        logger.info(
            "Sent conversation starter (category: %s): %s",
            required_category, fmt["message"][:80],
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

    def _check_scheduled_tasks(self) -> None:
        """Check for due scheduled tasks and fire them.

        Called every heartbeat tick (~5s). Fires notify actions via Discord,
        create_goal actions via goal_manager. Respects quiet hours and rate limits.
        Session 196.
        """
        try:
            from src.core import scheduler
            due = scheduler.check_due_tasks()
            if not due:
                return

            all_tasks = scheduler.load_schedule()
            if not scheduler.check_fire_rate(all_tasks):
                logger.warning("Scheduled task rate limit reached — deferring")
                return

            for task in due:
                if self.stop_flag.is_set():
                    break
                try:
                    self._fire_scheduled_task(task)
                except Exception as e:
                    logger.error("Failed to fire scheduled task '%s': %s", task.id, e)
        except ImportError:
            pass  # croniter not installed — skip silently
        except Exception as e:
            logger.debug("Scheduled task check failed: %s", e)

    def _fire_scheduled_task(self, task) -> None:
        """Execute a single scheduled task's action."""
        from src.core import scheduler

        if task.action == "notify":
            if scheduler.is_quiet_hours():
                logger.info("Scheduled notify '%s' deferred — quiet hours", task.id)
                return  # Will fire next tick after quiet hours end
            try:
                from src.interfaces.discord_bot import send_notification, is_outbound_ready
                if not is_outbound_ready():
                    return
                payload = task.payload if isinstance(task.payload, str) else str(task.payload)
                # Wrap short payloads so they pass the garbage guard
                # (session 213: "Drink water" was blocked as <15 chars)
                msg = f"Reminder: {payload}" if len(payload.strip()) < 20 else payload
                send_notification(msg)
                logger.info("Fired scheduled notify '%s': %s", task.id, payload[:80])
                # Track for engagement acknowledgment (session 198)
                self._pending_ack_tasks.append({
                    "task_id": task.id,
                    "fired_at": time.time(),
                })
            except Exception as e:
                logger.error("Scheduled notify '%s' failed: %s", task.id, e)

        elif task.action == "create_goal":
            if self.goal_manager:
                desc = task.payload if isinstance(task.payload, str) else (
                    task.payload.get("goal_description", task.description)
                    if isinstance(task.payload, dict) else task.description
                )
                try:
                    goal = self.goal_manager.create_goal(
                        description=desc,
                        user_intent=f"Scheduled task: {task.id}",
                        priority=5,
                    )
                    if goal:
                        logger.info("Fired scheduled goal '%s': %s", task.id, desc[:80])
                except Exception as e:
                    logger.error("Scheduled goal '%s' failed: %s", task.id, e)

        elif task.action == "run_command":
            # Future: shell command execution (with safety controller)
            logger.info("Scheduled run_command '%s' — not yet implemented", task.id)

        elif task.action == "run_skill":
            # Future: skill invocation
            logger.info("Scheduled run_skill '%s' — not yet implemented", task.id)

        else:
            logger.warning("Unknown scheduled action '%s' for task '%s'", task.action, task.id)
            return

        # Advance the task (update next_run_at, increment times_fired)
        scheduler.advance_task(task.id)

    def acknowledge_recent_tasks(self) -> int:
        """Mark all pending notify tasks as acknowledged (user responded).

        Called from discord_bot.on_message() when the user sends any message
        within the acknowledgment window. Returns count of tasks acknowledged.
        Session 198.
        """
        if not self._pending_ack_tasks:
            return 0
        now = time.time()
        acknowledged = 0
        for entry in self._pending_ack_tasks:
            elapsed = now - entry["fired_at"]
            if elapsed <= self._ACK_WINDOW_SECONDS:
                try:
                    from src.core import scheduler
                    scheduler.record_engagement(entry["task_id"], acknowledged=True)
                    acknowledged += 1
                    logger.debug("Engagement ack for '%s' (%ds after fire)",
                                 entry["task_id"], int(elapsed))
                except Exception as e:
                    logger.debug("Engagement ack failed for '%s': %s", entry["task_id"], e)
            # Expired entries are dropped (already timed out by _check_engagement_timeouts)
        self._pending_ack_tasks.clear()  # All processed — either acked or expired
        return acknowledged

    def _check_engagement_timeouts(self) -> None:
        """Check for expired engagement windows and mark as ignored.

        Called every heartbeat tick. Tasks older than _ACK_WINDOW_SECONDS
        without user response are marked ignored. Session 198.
        """
        if not self._pending_ack_tasks:
            return
        now = time.time()
        remaining = []
        for entry in self._pending_ack_tasks:
            elapsed = now - entry["fired_at"]
            if elapsed > self._ACK_WINDOW_SECONDS:
                try:
                    from src.core import scheduler
                    scheduler.record_engagement(entry["task_id"], acknowledged=False)
                    logger.debug("Engagement timeout for '%s' (%ds elapsed)",
                                 entry["task_id"], int(elapsed))
                except Exception as e:
                    logger.debug("Engagement timeout failed for '%s': %s", entry["task_id"], e)
            else:
                remaining.append(entry)
        self._pending_ack_tasks = remaining

    def _run_supplement_reminder(self) -> None:
        """Phase 0.995: Send a supplement reminder if any are untaken today.

        Only fires in the afternoon (after 14:00) and not when the user is
        actively chatting. Keeps it to one reminder per cycle at most.
        """
        try:
            now = datetime.now()
            if now.hour < 14:
                return  # Too early — wait until afternoon

            if self._is_user_recently_active():
                return  # Don't interrupt active conversations

            from src.tools.supplement_tracker import get_tracker
            tracker = get_tracker()

            reminder = tracker.format_reminder()
            if not reminder:
                return  # All taken — nothing to remind

            from src.interfaces.discord_bot import send_notification, is_outbound_ready
            if is_outbound_ready():
                send_notification(reminder)
                logger.debug("Sent supplement reminder")
        except Exception as e:
            logger.debug("Supplement reminder skipped: %s", e)

    def _run_finance_alert(self) -> None:
        """Phase 0.996: Check budgets and alert if spending is high.

        Runs every 8 cycles (roughly once per day at typical intervals).
        Suppressed when user is active. Only alerts if budgets are set.
        """
        try:
            if self._is_user_recently_active():
                return

            from src.tools.finance_tracker import get_tracker
            tracker = get_tracker()

            alert = tracker.format_budget_alert()
            if not alert:
                return

            from src.interfaces.discord_bot import send_notification, is_outbound_ready
            if is_outbound_ready():
                send_notification(alert)
                logger.debug("Sent finance budget alert")
        except Exception as e:
            logger.debug("Finance alert skipped: %s", e)

    def _run_content_calendar_phase(self) -> None:
        """Phase 0.99: Autonomous content calendar management.

        Three sub-steps:
        1. Auto-plan: if queue depth < 3 days, generate a week plan.
        2. Auto-generate: for slots due within 24h that need content, call
           generate_content() and mark them as generated.
        3. Auto-publish: for generated slots past their scheduled time, publish
           them (suppress publishing when user is mid-conversation).

        Cost-conscious: generation uses the default model (~$0.01/call),
        publishing is free (API calls only).  Limits to 3 generations and
        2 publishes per cycle to avoid spiking budget.
        """
        try:
            from src.tools.content_calendar import ContentCalendar
            cal = ContentCalendar()

            # ── Sub-step 1: Auto-plan ────────────────────────────────
            if cal.needs_planning():
                new_slots = cal.plan_week()
                logger.info("Content calendar auto-planned %d slots", len(new_slots))

            # ── Sub-step 2: Auto-generate content ────────────────────
            pending = cal.get_pending_generation(limit=3)
            if pending:
                router = self._get_router()
                from src.tools.content_creator import generate_content
                for slot in pending:
                    if self.stop_flag.is_set():
                        break
                    try:
                        result = generate_content(
                            router,
                            topic=slot.topic,
                            content_format=slot.content_format,
                        )
                        if result and result.get("content"):
                            cal.mark_generated(slot.slot_id, result["content"])
                            logger.info("Content generated for slot %s: %s",
                                        slot.slot_id, slot.topic[:40])
                        else:
                            cal.mark_failed(slot.slot_id, "generation returned empty")
                    except Exception as gen_err:
                        logger.warning("Content generation failed for %s: %s",
                                       slot.slot_id, gen_err)
                        cal.mark_failed(slot.slot_id, str(gen_err)[:200])

            # ── Sub-step 2.25: Generate companion images ─────────────
            # For visual platforms, try generating an SDXL image alongside
            # the text content.  Gracefully skips if SDXL is unavailable.
            self._generate_content_images(cal)

            # ── Sub-step 2.5: Auto-adapt long-form content ───────────
            # When a blog/long-form piece was just generated, adapt it
            # for other platforms and queue the results.
            self._auto_adapt_generated_content(cal)

            # ── Sub-step 3: Auto-publish due content ─────────────────
            # Suppress publishing when user is actively chatting.
            if self._is_user_recently_active():
                return

            due = cal.get_due_slots()
            if not due:
                return

            from src.tools.content_creator import (
                publish_to_github_blog,
                publish_tweet, publish_tweet_thread,
                publish_reddit_post,
                publish_to_facebook, publish_to_facebook_photo,
                publish_to_instagram,
            )

            _PUBLISH_MAP = {
                "blog": lambda c, **_kw: publish_to_github_blog(c),
                "tweet": lambda c, **_kw: publish_tweet(c),
                "tweet_thread": lambda c, **_kw: publish_tweet_thread(c),
                "reddit": lambda c, topic="", **_kw: publish_reddit_post(
                    c, title=topic),
                "facebook_post": lambda c, **_kw: publish_to_facebook(c),
                "instagram_post": None,  # Handled separately (needs image URL)
            }

            published_count = 0
            for slot in due:
                if self.stop_flag.is_set() or published_count >= 2:
                    break

                # Image-based platforms need a hosted public URL
                pub_result = None
                if slot.content_format in ("instagram_post", "facebook_post") and slot.image_path:
                    pub_result = self._publish_with_image(
                        slot, publish_to_instagram, publish_to_facebook_photo,
                    )
                else:
                    pub_fn = _PUBLISH_MAP.get(slot.content_format)
                    if not pub_fn:
                        logger.debug("No publisher for format: %s", slot.content_format)
                        continue

                try:
                    if pub_result is None:
                        pub_result = pub_fn(
                            slot.generated_content,
                            topic=slot.topic,
                        )
                    url = ""
                    if isinstance(pub_result, dict):
                        url = pub_result.get("url", "") or pub_result.get("id", "")
                    elif isinstance(pub_result, str):
                        url = pub_result
                    cal.mark_published(slot.slot_id, str(url)[:500])
                    published_count += 1
                    logger.info("Content published: %s on %s → %s",
                                slot.topic[:40], slot.platform, url or "ok")
                    # Notify Jesse about the publish
                    try:
                        from src.interfaces.discord_bot import send_notification, is_outbound_ready
                        if is_outbound_ready() and not self._is_user_recently_active():
                            send_notification(
                                f"**Published:** {slot.topic[:60]} → {slot.platform}"
                                + (f" ({url})" if url else ""),
                            )
                    except Exception:
                        pass
                except Exception as pub_err:
                    logger.warning("Content publish failed for %s: %s",
                                   slot.slot_id, pub_err)
                    cal.mark_failed(slot.slot_id, str(pub_err)[:200])

        except Exception as cal_err:
            logger.debug("Content calendar phase skipped: %s", cal_err)

    def _publish_with_image(self, slot, ig_publisher, fb_photo_publisher):
        """Upload image to GitHub hosting and publish to image-based platforms.

        For Instagram and Facebook photo posts, the API requires a public URL.
        This method uploads the local image via image_host, then calls the
        appropriate publisher with the hosted URL + caption.

        Returns publisher result dict, or None if hosting fails.
        """
        try:
            from src.tools.image_host import upload_for_platform, is_configured
        except ImportError:
            logger.debug("image_host module not available")
            return None

        if not is_configured():
            logger.debug("Image hosting not configured (no GITHUB_PAT/GITHUB_BLOG_REPO)")
            return None

        if not slot.image_path or not os.path.exists(slot.image_path):
            logger.debug("No local image for slot %s", slot.slot_id)
            return None

        # Upload to GitHub
        upload_result = upload_for_platform(
            slot.image_path,
            platform=slot.content_format,
            topic=slot.topic,
        )
        if not upload_result.get("success"):
            logger.warning("Image upload failed for %s: %s",
                           slot.slot_id, upload_result.get("error"))
            return None

        public_url = upload_result["url"]
        caption = slot.generated_content or slot.topic

        if slot.content_format == "instagram_post":
            return ig_publisher(public_url, caption=caption)
        elif slot.content_format == "facebook_post":
            return fb_photo_publisher(public_url, caption=caption)

        return None

    def _generate_content_images(self, cal) -> None:
        """Generate companion images for recently generated visual-platform content.

        For platforms like Instagram, Facebook, and blog, tries to generate
        an SDXL image and attach it to the content slot.  Gracefully skips
        if SDXL is unavailable.  Limit: 1 image per cycle (~20-40s each).
        """
        try:
            from src.tools.image_generator import generate_content_image, is_available
            if not is_available():
                return  # No SDXL model — skip silently

            # Visual platforms that benefit from companion images
            _VISUAL_FORMATS = {"instagram_post", "facebook_post", "blog", "tweet"}

            from src.tools.content_calendar import _load_calendar
            data = _load_calendar()

            generated_count = 0
            for slot_data in data.get("slots", []):
                if generated_count >= 1:  # 1 image per cycle
                    break
                if self.stop_flag.is_set():
                    break
                if slot_data.get("status") != "generated":
                    continue
                if slot_data.get("image_path"):
                    continue  # Already has an image
                fmt = slot_data.get("content_format", "")
                if fmt not in _VISUAL_FORMATS:
                    continue
                topic = slot_data.get("topic", "")
                if not topic:
                    continue

                # Map content format to image platform key
                img_platform = fmt if fmt != "tweet" else "twitter"
                pillar = slot_data.get("pillar", "")

                result = generate_content_image(
                    topic=topic,
                    platform=img_platform,
                    pillar=pillar,
                )
                if result.get("success") and result.get("image_path"):
                    slot_id = slot_data.get("slot_id", "")
                    cal._update_slot(slot_id, image_path=result["image_path"])
                    logger.info(
                        "Content image generated for %s '%s': %s",
                        fmt, topic[:40], result["image_path"],
                    )
                    generated_count += 1
                else:
                    logger.debug(
                        "Image generation skipped for %s: %s",
                        slot_data.get("slot_id", ""), result.get("error", ""),
                    )

        except Exception as img_err:
            logger.debug("Content image generation skipped: %s", img_err)

    def _auto_adapt_generated_content(self, cal) -> None:
        """Auto-adapt freshly generated blog/long-form content for other platforms.

        After a blog post is generated, call adapt_content() to create
        platform-specific versions (tweet, instagram, facebook, reddit)
        and queue them in the content calendar.  Limit: 1 adaptation
        per cycle to keep costs down (~$0.05 per adapt call).
        """
        try:
            from src.tools.content_creator import adapt_content

            # Find recently generated blog/long-form slots that haven't
            # been adapted yet.  We check for a marker in publish_result
            # to avoid re-adapting.
            data = cal._load_slots_raw() if hasattr(cal, '_load_slots_raw') else None
            if data is None:
                # Fallback: load directly
                from src.tools.content_calendar import _load_calendar
                data = _load_calendar()

            adapted_count = 0
            for slot_data in data.get("slots", []):
                if adapted_count >= 1:  # 1 adaptation per cycle
                    break
                if self.stop_flag.is_set():
                    break
                if slot_data.get("status") != "generated":
                    continue
                fmt = slot_data.get("content_format", "")
                if fmt not in ("blog", "video_script"):
                    continue
                # Skip if already adapted (marker in publish_result)
                if "adapted" in slot_data.get("publish_result", ""):
                    continue
                content = slot_data.get("generated_content", "")
                if not content or len(content) < 100:
                    continue
                topic = slot_data.get("topic", "")

                # Adapt to cross-platform formats
                router = self._get_router()
                target_platforms = ["tweet", "instagram_post", "facebook_post"]
                results = adapt_content(
                    router,
                    source_content=content,
                    source_format=fmt,
                    target_platforms=target_platforms,
                    topic=topic,
                )

                # Queue successful adaptations
                queued = 0
                for platform, result in results.items():
                    if not result or not result.get("content"):
                        continue
                    # Map adaptation platform → calendar platform
                    plat_map = {
                        "tweet": "twitter",
                        "instagram_post": "instagram",
                        "facebook_post": "facebook",
                    }
                    cal_platform = plat_map.get(platform, platform)
                    # Queue as already-generated (skip the generation step)
                    from src.tools.content_calendar import ContentSlot, _generate_slot_id
                    from datetime import datetime, timedelta
                    # Schedule 30-60 min after the source post
                    offset_min = 30 + queued * 15
                    sched_time = datetime.now() + timedelta(minutes=offset_min)
                    new_slot = cal.queue_content(
                        topic=f"[Adapted] {topic[:50]}",
                        platform=cal_platform,
                        content_format=platform,
                        publish_at=sched_time,
                        pillar=slot_data.get("pillar", ""),
                    )
                    if new_slot:
                        # Immediately mark as generated with the adapted content
                        cal.mark_generated(new_slot.slot_id, result["content"])
                        queued += 1

                # Mark source as adapted so we don't re-process
                if queued > 0:
                    slot_id = slot_data.get("slot_id", "")
                    cal._update_slot(slot_id, publish_result="adapted")
                    logger.info(
                        "Auto-adapted %s '%s' → %d platform versions queued",
                        fmt, topic[:40], queued,
                    )
                    adapted_count += 1

        except Exception as adapt_err:
            logger.debug("Auto-adapt skipped: %s", adapt_err)

    def _dispatch_work(self, budget_throttle: str) -> int:
        """Dispatch pending work to the pool, or ask the user for work.

        Args:
            budget_throttle: Throttle level from _check_budget_trajectory().
                "stop" skips all work; other levels proceed normally.

        Returns:
            Number of tasks/goals dispatched (approximate).
        """
        if budget_throttle == "stop":
            return 0  # Skip work phase — budget trajectory too hot

        if self._has_pending_work() and self.goal_worker_pool:
            # Submit only goals that have actionable work (ready tasks or
            # need decomposition).  Goals with all tasks done/failed but no
            # ready tasks are skipped — prevents re-notification spam.
            submitted = 0
            for goal in list(self.goal_manager.goals.values()):
                if self.stop_flag.is_set():
                    break
                if goal.is_complete():
                    continue
                if goal.get_ready_tasks() or not goal.is_decomposed:
                    if self.goal_worker_pool.submit_goal(goal.goal_id):
                        submitted += 1
            if submitted:
                logger.info("Dispatched %d goals to worker pool", submitted)

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
                        submitted += 1
                except Exception as e:
                    logger.error("Queued task error: %s", e)
            return submitted

        if self._has_pending_work():
            # Fallback: no pool available, use old sequential executor
            return autonomous_executor.process_task_queue(
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

        # Nothing to do — ask user first; only go proactive if
        # suggestions have gone unanswered (user isn't engaging).
        # But skip suggestions if a goal just completed — prevents duplicate
        # notifications about the same topic (session 194).
        # Session 230: Also skip if user is mid-conversation — these are
        # distracting while the user is actively chatting.
        if self._is_user_recently_active():
            logger.info("Skipping work suggestion — user active within 5 min")
            return 0
        if not self.stop_flag.is_set() and self.goal_worker_pool:
            _last_notify = getattr(
                self.goal_worker_pool, 'last_goal_notification_time', 0,
            )
            _since_goal = time.monotonic() - (
                _last_notify if isinstance(_last_notify, (int, float)) else 0
            )
            if _since_goal < 60:
                logger.info(
                    "Skipping work suggestion — goal completed %.0fs ago",
                    _since_goal,
                )
                return 0
        if not self.stop_flag.is_set():
            if self._unanswered_suggest_count > 0:
                # User ignored previous suggestions — try doing
                # something useful on our own instead of nagging.
                initiative_started = self._try_proactive_initiative()
                if not initiative_started:
                    self._ask_user_for_work()
            else:
                # Alternate between work suggestions and conversation
                # starters. Every 3rd idle cycle, try a social message
                # instead of always pushing work.
                _cycle_num = len(self.cycle_history)
                if _cycle_num % 3 == 2:
                    started = self._try_conversation_starter()
                    if not started:
                        self._ask_user_for_work()
                else:
                    self._ask_user_for_work()
        return 0

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

            # Phase 0.5: Budget trajectory check — throttle or skip if overspending
            _budget_throttle = self._check_budget_trajectory()
            _saved_max_workers = None
            if _budget_throttle == "stop":
                logger.warning("Budget trajectory: STOP — skipping work phase entirely")
                # Fall through to suggest/learn/synthesis phases; work is skipped
            if _budget_throttle == "throttle" and self.goal_worker_pool:
                _saved_max_workers = self.goal_worker_pool._max_workers
                self.goal_worker_pool._max_workers = max(1, _saved_max_workers // 2)
                logger.info(
                    "Budget throttle: reduced pool workers %d → %d for this cycle",
                    _saved_max_workers, self.goal_worker_pool._max_workers,
                )

            # Phase 0.9: Prune stale goals (all-terminal, zombies, etc.)
            # Called here so dead goals are cleaned even when suggest_work() doesn't
            # run (session 181 — was only called inside suggest_work).
            try:
                from src.core.idea_generator import prune_stale_goals
                prune_stale_goals(self.goal_manager)
            except Exception as e:
                logger.debug("Goal pruning failed: %s", e)

            # Phase 0.95: Check for ignored scheduled tasks (every 10 cycles, session 199)
            if not self.stop_flag.is_set() and len(self.cycle_history) % 10 == 3:
                try:
                    from src.core.idea_generator import (
                        check_retirement_candidates,
                        format_retirement_message,
                    )
                    candidates = check_retirement_candidates()
                    if candidates:
                        msg = format_retirement_message(candidates)
                        from src.interfaces.discord_bot import send_notification, is_outbound_ready
                        if is_outbound_ready():
                            send_notification(msg)
                except Exception as rte:
                    logger.debug("Retirement check skipped: %s", rte)

            # Phase 0.97: Capability assessment (every 20 cycles, session 236)
            # Identifies what Archi can't do but wants to. Proposes projects to Jesse.
            if not self.stop_flag.is_set() and len(self.cycle_history) % 20 == 10:
                try:
                    from src.core.capability_assessor import assess, is_assessment_due, propose_project, format_gap_message, set_pending_proposal
                    if is_assessment_due():
                        import asyncio
                        router = self._get_router()
                        gaps = asyncio.get_event_loop().run_until_complete(
                            assess(router, self.learning_system)
                        ) if asyncio.get_event_loop().is_running() else []
                        # Fallback for non-async context
                        if not gaps:
                            try:
                                loop = asyncio.new_event_loop()
                                gaps = loop.run_until_complete(
                                    assess(router, self.learning_system)
                                )
                                loop.close()
                            except Exception:
                                pass
                        if gaps and not self._is_user_recently_active():
                            top_gap = gaps[0]
                            if top_gap.impact >= 0.5:
                                proposal = None
                                try:
                                    loop = asyncio.new_event_loop()
                                    proposal = loop.run_until_complete(
                                        propose_project(top_gap, router)
                                    )
                                    loop.close()
                                except Exception:
                                    pass
                                msg = format_gap_message(top_gap, proposal)
                                from src.interfaces.discord_bot import send_notification, is_outbound_ready
                                if is_outbound_ready():
                                    send_notification(msg)
                                    # Store for approval flow (session 238)
                                    if proposal:
                                        set_pending_proposal(top_gap, proposal)
                                    logger.info("Capability gap proposed: %s (impact %.1f)", top_gap.name, top_gap.impact)
                except Exception as ca_err:
                    logger.debug("Capability assessment skipped: %s", ca_err)

            # Phase 0.98: Strategic planner — advance active self-extension projects (session 237)
            # Checks every 5 cycles. If an active project exists, tries to advance phases.
            # Creates goals for new phase tasks so they're picked up by Phase 1 dispatch.
            if not self.stop_flag.is_set() and len(self.cycle_history) % 5 == 3:
                try:
                    from src.core.strategic_planner import get_active_project, StrategicPlanner
                    active = get_active_project()
                    if active and active.get("status") == "active":
                        router = self._get_router()
                        sp = StrategicPlanner(router)
                        result = sp.advance_plan(active["project_id"])
                        if result.action == "advanced":
                            logger.info("Self-extension: advanced to phase %d — %s",
                                        result.phase_number, result.message)
                            # Create goals for the new phase's tasks, tagged with project metadata
                            # so the goal completion flow can auto-mark phase tasks done (Phase 4)
                            if result.goal_descriptions and hasattr(self, 'goal_manager') and self.goal_manager:
                                _proj_id = active.get("project_id", "")
                                for desc in result.goal_descriptions[:3]:
                                    self.goal_manager.create_goal(
                                        description=desc,
                                        user_intent=f"Self-extension: {active.get('title', 'project')}",
                                        priority=4,
                                        project_id=_proj_id,
                                        project_phase=result.phase_number,
                                    )
                        elif result.action == "completed":
                            logger.info("Self-extension project completed: %s", result.message)
                            from src.interfaces.discord_bot import send_notification, is_outbound_ready
                            if is_outbound_ready() and not self._is_user_recently_active():
                                send_notification(f"**Self-extension complete!** {result.message}")
                except Exception as sp_err:
                    logger.debug("Strategic planner check skipped: %s", sp_err)

            # Phase 0.99: Content calendar — auto-plan, generate, and publish (session 241)
            # Runs every 3 cycles. Checks queue depth, generates pending content,
            # publishes due slots. Keeps content flowing without manual intervention.
            if not self.stop_flag.is_set() and len(self.cycle_history) % 3 == 1:
                self._run_content_calendar_phase()

            # Phase 0.995: Supplement reminder — nudge if supplements not taken (session 245)
            # Runs every 4 cycles, afternoon only (after 14:00). Suppressed if user active.
            if not self.stop_flag.is_set() and len(self.cycle_history) % 4 == 2:
                self._run_supplement_reminder()

            # Phase 0.996: Finance budget alert — warn when spending approaches limits (session 245)
            # Runs every 8 cycles. Only fires if budgets are configured.
            if not self.stop_flag.is_set() and len(self.cycle_history) % 8 == 5:
                self._run_finance_alert()

            # Phase 1: Dispatch work to pool OR ask user for work
            _phase_t0 = time.monotonic()
            _results_before = len(self._overnight_results)
            tasks_processed = self._dispatch_work(_budget_throttle)

            # Restore worker pool parallelism if we throttled it
            if _saved_max_workers is not None and self.goal_worker_pool:
                self.goal_worker_pool._max_workers = _saved_max_workers

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

            # Phase 1.5: Archive old chat messages to long-term conversation memory
            if not self.stop_flag.is_set():
                self._archive_conversations()

            # Phase 2: Review recent history (learning)
            insights = self._review_history()

            # Phase 2.1: Extract behavioral rules from experience patterns (session 200)
            if not self.stop_flag.is_set():
                try:
                    from src.core.behavioral_rules import extract_rules_from_experiences, add_avoidance_rule, add_preference_rule
                    exp_dicts = [e.to_dict() for e in self.learning_system.experiences[-100:]]
                    proposals = extract_rules_from_experiences(exp_dicts)
                    for p in proposals:
                        if p["type"] == "avoidance":
                            add_avoidance_rule(p["pattern"], p["reason"], p["keywords"], evidence_count=p["evidence_count"])
                        else:
                            add_preference_rule(p["pattern"], p["reason"], p["keywords"], evidence_count=p["evidence_count"])
                except Exception as bre:
                    logger.debug("Behavioral rule extraction skipped: %s", bre)

            # Phase 2.5: Skill suggestion scan (every 5 cycles)
            if not self.stop_flag.is_set() and len(self.cycle_history) % 5 == 0:
                try:
                    from src.core.skill_suggestions import SkillSuggestions
                    from src.core.skill_system import get_shared_skill_registry
                    suggester = SkillSuggestions()
                    skill_registry = get_shared_skill_registry()
                    proposals = suggester.scan_for_suggestions(
                        self.learning_system, skill_registry,
                    )
                    if proposals:
                        text = suggester.format_suggestions_for_user(proposals)
                        logger.info("Skill suggestions:\n%s", text)
                        for p in proposals:
                            suggester.record_suggestion(p)
                            self.learning_system.record_skill_suggested(p.name)
                except Exception as sse:
                    logger.debug("Skill suggestion scan skipped: %s", sse)

            # Phase 2.7: Autonomous schedule proposals (every 10 cycles, offset 7, session 199)
            if not self.stop_flag.is_set() and len(self.cycle_history) % 10 == 7:
                try:
                    from src.core.idea_generator import (
                        suggest_scheduled_tasks,
                        create_proposed_schedules,
                        format_schedule_proposal_message,
                    )
                    proposals = suggest_scheduled_tasks(self._get_router())
                    if proposals:
                        created, user_proposals = create_proposed_schedules(proposals)
                        if user_proposals:
                            msg = format_schedule_proposal_message(user_proposals)
                            if msg:
                                from src.interfaces.discord_bot import send_notification, is_outbound_ready
                                if is_outbound_ready():
                                    send_notification(msg)
                except Exception as ase:
                    logger.debug("Autonomous schedule proposal skipped: %s", ase)

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

                # Prune old journal files alongside file cleanup (session 197)
                try:
                    from src.core.journal import prune_old_journals
                    prune_old_journals()
                except Exception:
                    pass

                # Decay stale worldview entries (session 199)
                try:
                    from src.core.worldview import load as _wv_load, save as _wv_save
                    _wv_data = _wv_load()
                    if _wv_data.get("opinions") or _wv_data.get("interests"):
                        _wv_save(_wv_data)  # save triggers _prune() internally
                except Exception:
                    pass

                # Prune stale behavioral rules (session 200)
                try:
                    from src.core.behavioral_rules import load as _br_load, save as _br_save
                    _br_data = _br_load()
                    if _br_data.get("avoidance") or _br_data.get("preference"):
                        _br_save(_br_data)  # save triggers _prune() internally
                except Exception:
                    pass

            # Phase 5: Weekly self-reflection + meta-cognition (every 50 cycles, session 199/203)
            if not self.stop_flag.is_set() and len(self.cycle_history) % 50 == 25:
                try:
                    from src.core.journal import generate_self_reflection
                    router = self._get_router()
                    reflection = generate_self_reflection(router=router, days=7)
                    if reflection:
                        logger.info("Weekly self-reflection completed")

                    # Meta-cognition: analyze own behavioral patterns (session 203)
                    from src.core.idea_generator import generate_meta_cognition
                    meta_obs = generate_meta_cognition(router)
                    if meta_obs:
                        logger.info("Meta-cognition: %d observations", len(meta_obs))
                except Exception as ref_err:
                    logger.debug("Self-reflection/meta-cognition skipped: %s", ref_err)

            # Phase 5.5: Deliver "I changed my mind" notifications (session 201)
            # Session 230: Suppress when user is mid-conversation
            if not self.stop_flag.is_set() and not self._is_user_recently_active():
                try:
                    from src.core.worldview import get_pending_revisions, clear_revision
                    revisions = get_pending_revisions()
                    if revisions:
                        from src.core.notification_formatter import format_opinion_revision
                        from src.interfaces.discord_bot import send_notification, is_outbound_ready
                        router = self._get_router()
                        for rev in revisions[:2]:  # Max 2 per cycle
                            if not is_outbound_ready():
                                break
                            result = format_opinion_revision(
                                topic=rev["topic"],
                                old_position=rev.get("old_position", ""),
                                new_position=rev.get("new_position", ""),
                                old_confidence=rev.get("old_confidence", 0.5),
                                new_confidence=rev.get("new_confidence", 0.5),
                                router=router,
                            )
                            if result.get("message"):
                                send_notification(result["message"])
                                clear_revision(rev["topic"])
                                logger.info("Opinion revision delivered: %s", rev["topic"])
                except Exception as orev_err:
                    logger.debug("Opinion revision delivery skipped: %s", orev_err)

            # Phase 6: Interest-driven exploration (~20% of cycles, session 202)
            # Session 230: Suppress sharing when user is mid-conversation
            if not self.stop_flag.is_set() and len(self.cycle_history) % 5 == 2 and not self._is_user_recently_active():
                try:
                    from src.core.idea_generator import explore_interest
                    exploration = explore_interest(self._get_router())
                    if exploration and exploration.get("summary"):
                        from src.core.notification_formatter import format_exploration_sharing
                        from src.interfaces.discord_bot import send_notification, is_outbound_ready
                        if is_outbound_ready():
                            result = format_exploration_sharing(
                                topic=exploration["topic"],
                                summary=exploration["summary"],
                                commentary=exploration.get("commentary", ""),
                                router=self._get_router(),
                            )
                            if result.get("message"):
                                send_notification(result["message"])
                                logger.info("Shared exploration finding: %s", exploration["topic"])
                except Exception as exp_err:
                    logger.debug("Interest exploration skipped: %s", exp_err)

            # Phase 6.5: Personal projects (~10% of cycles, session 203)
            # Session 230: Suppress sharing when user is mid-conversation
            if not self.stop_flag.is_set() and len(self.cycle_history) % 10 == 4 and not self._is_user_recently_active():
                try:
                    from src.core.idea_generator import (
                        propose_personal_project,
                        work_on_personal_project,
                    )
                    from src.core.worldview import get_personal_projects

                    router = self._get_router()
                    active_projects = get_personal_projects(status="active")

                    if not active_projects:
                        # Try to propose a new project from interests
                        propose_personal_project(router)
                    else:
                        # Work on existing project
                        result = work_on_personal_project(router)
                        if result and result.get("share_worthy"):
                            from src.core.notification_formatter import format_project_sharing
                            from src.interfaces.discord_bot import send_notification, is_outbound_ready
                            if is_outbound_ready():
                                fmt = format_project_sharing(
                                    title=result["title"],
                                    progress=result.get("progress", ""),
                                    share_message=result.get("share_message", ""),
                                    router=router,
                                )
                                if fmt.get("message"):
                                    send_notification(fmt["message"])
                                    logger.info("Shared project progress: %s", result["title"])
                except Exception as proj_err:
                    logger.debug("Personal project phase skipped: %s", proj_err)

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

            # Adaptive scheduling: extend interval on idle cycles,
            # reset on productive ones (session 115).
            self._adapt_interval(was_productive=(tasks_processed > 0))

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
            except Exception as e:
                logger.warning("Failed to persist cycle log to dream_log.jsonl: %s", e)

            # Journal entry for dream cycle (session 197)
            try:
                from src.core.journal import add_entry
                task_names = [r.get("task", "?")[:60] for r in _this_cycle_results[:5]]
                summary = (f"Dream cycle: {tasks_processed} tasks in {cycle_duration:.0f}s"
                           + (f" — {', '.join(task_names)}" if task_names else ""))
                add_entry("dream_cycle", summary,
                          metadata={"tasks": tasks_processed, "duration": round(cycle_duration, 1)})
            except Exception:
                pass  # journal non-critical

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

    # -- Conversation archival ---

    def _archive_conversations(self) -> None:
        """Summarize and archive old chat messages to long-term memory.

        Pulls messages beyond the most recent 8 from chat_history, uses the
        Router to generate a concise summary, and stores it in LanceDB as
        type="conversation". This gives Archi persistent conversational memory
        that survives the 20-message chat_history cap.
        """
        if not self.memory:
            return
        try:
            from src.interfaces.chat_history import pop_archivable
            old_messages = pop_archivable(keep=8)
            if not old_messages:
                return

            # Format messages into a readable block for summarization
            lines = []
            for m in old_messages:
                role = m.get("role", "user")
                content = (m.get("content") or "").strip()[:300]
                if content:
                    prefix = f"{get_user_name()}:" if role == "user" else "Archi:"
                    lines.append(f"{prefix} {content}")
            if not lines:
                return

            conversation_block = "\n".join(lines)
            router = self._get_router()
            if not router:
                return

            resp = router.generate(
                prompt=(
                    f"Summarize this conversation between {get_user_name()} and Archi in 2-3 sentences. "
                    f"Focus on topics discussed, decisions made, and any personal details {get_user_name()} shared. "
                    f"Be specific — names, facts, and preferences matter.\n\n"
                    f"{conversation_block}\n\nSummary:"
                ),
                max_tokens=150,
                temperature=0.2,
            )
            summary = (resp.get("text") or "").strip()
            if summary and len(summary) > 20:
                self.memory.store_conversation(
                    summary,
                    metadata={"message_count": len(old_messages)},
                )
                logger.info("Archived %d chat messages → conversation memory: %s",
                            len(old_messages), summary[:80])
        except Exception as e:
            logger.debug("Conversation archival skipped: %s", e)

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

        prompt = f"""You are Archi, reviewing completed research and tasks for {get_user_name()}.

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
