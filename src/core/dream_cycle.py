"""
Dream Cycle Engine — Proactive background processing orchestrator.

Archi runs "dream cycles" when idle. The new flow (session 31):
- If there are active goals with pending tasks → execute them
- If no work to do → brainstorm suggestions and ask the user via Discord
- Synthesis is informational only (no goal creation)
- Follow-up extraction adds tasks to existing goals (not new goals)

This is the slim orchestrator that delegates to:
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
from pathlib import Path
from typing import Optional, Dict, Any, List

import yaml

from src.core.goal_manager import GoalManager
from src.core.goal_worker_pool import GoalWorkerPool
from src.core.learning_system import LearningSystem
from src.core import autonomous_executor
from src.core import idea_generator
from src.core import reporting
from src.memory.memory_manager import MemoryManager
from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)


class DreamCycle:
    """
    Manages Archi's proactive background processing.

    When idle, Archi:
    - Executes pending tasks from user-created goals
    - Suggests work to the user if nothing to do
    - Reviews and learns from past actions
    - Runs periodic synthesis (informational only)
    """

    def __init__(
        self,
        idle_threshold_seconds: int = 300,
        check_interval_seconds: int = 30,
    ):
        self.idle_threshold = idle_threshold_seconds
        self.check_interval = check_interval_seconds
        self.last_activity = datetime.now()
        self.is_dreaming = False
        self.dream_thread: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()
        self.task_queue: List[Dict[str, Any]] = []
        self.dream_history: List[Dict[str, Any]] = []

        # Autonomous execution components
        self.goal_manager: Optional[GoalManager] = None
        self.autonomous_mode = False
        self._router: Optional[Any] = None
        self.learning_system = LearningSystem()
        self.goal_worker_pool: Optional[GoalWorkerPool] = None

        # Long-term semantic memory (LanceDB) for research recall
        self.memory: Optional[MemoryManager] = None
        try:
            self.memory = MemoryManager()
            _mem_count = self.memory.get_stats().get("long_term_count", 0)
            logger.info("Long-term memory initialized (%d entries)", _mem_count)
        except Exception as e:
            logger.warning("Long-term memory unavailable: %s", e)

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

        self.identity = self._load_identity()
        self.prime_directive = self._load_prime_directive()
        role = self.identity.get("identity", {}).get("role", "Archi")
        logger.info(
            "Dream cycle initialized (idle threshold: %ds) — identity: %s",
            idle_threshold_seconds, role,
        )

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

        In the API-only world, we do NOT interrupt an active dream cycle
        when the user sends a message — background work and chat can
        coexist since we're not competing for a local GPU anymore.
        """
        self.last_activity = datetime.now()

    def is_idle(self) -> bool:
        """Check if system has been idle long enough to start dreaming."""
        idle_time = (datetime.now() - self.last_activity).total_seconds()
        return idle_time >= self.idle_threshold

    def set_idle_threshold(self, seconds: int) -> str:
        """Change the idle threshold at runtime. Returns a confirmation message."""
        old = self.idle_threshold
        self.idle_threshold = max(60, seconds)
        logger.info(
            "Dream cycle idle threshold changed: %ds → %ds",
            old, self.idle_threshold,
        )
        mins = self.idle_threshold / 60
        if mins == int(mins):
            return f"Dream cycle interval set to {int(mins)} minute{'s' if mins != 1 else ''}."
        return f"Dream cycle interval set to {mins:.1f} minutes."

    def get_idle_threshold(self) -> int:
        """Return the current idle threshold in seconds."""
        return self.idle_threshold

    # -- Autonomous mode setup ---

    def enable_autonomous_mode(self, goal_manager: GoalManager) -> None:
        """Enable autonomous task execution during dream cycles.

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
                memory=self.memory,
            )
            logger.info("Autonomous execution mode ENABLED (with worker pool)")
        else:
            logger.info("Autonomous execution mode ENABLED (pool deferred until router available)")

    def kick(self, goal_id: Optional[str] = None) -> None:
        """Signal that new work is available — start immediately.

        If a goal_id is provided and the worker pool is available, the goal
        is submitted directly to the pool for zero-latency start.  Otherwise
        falls back to back-dating last_activity so the monitor loop picks
        it up on the next tick.
        """
        if goal_id and self.goal_worker_pool:
            self.goal_worker_pool.submit_goal(goal_id)
            logger.info("Goal %s submitted directly to worker pool", goal_id)
        else:
            from datetime import timedelta
            self.last_activity = datetime.now() - timedelta(seconds=self.idle_threshold + 1)
            logger.info("Dream cycle kicked — will start on next check")

    def queue_task(self, task: Dict[str, Any]):
        """Add a task to the dream queue."""
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
                memory=self.memory,
            )
            logger.info("GoalWorkerPool created (late-init via set_router)")

    def _get_router(self) -> Any:
        """Return shared or lazy-load ModelRouter for task execution."""
        if not hasattr(self, "_router") or self._router is None:
            try:
                from src.models.router import ModelRouter
                self._router = ModelRouter()
                logger.info("Dream cycle: model router initialized (lazy)")
            except Exception as e:
                logger.warning("Dream cycle: router not available: %s", e)
                self._router = None
        return self._router

    # -- Monitoring loop ---

    def start_monitoring(self):
        """Start background thread that watches for idle periods."""
        if self.dream_thread and self.dream_thread.is_alive():
            logger.warning("Dream monitoring already running")
            return

        self.stop_flag.clear()
        self.dream_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.dream_thread.start()
        logger.info("Dream cycle monitoring started")

    def stop_monitoring(self):
        """Stop dream cycle monitoring, worker pool, and flush pending data."""
        self.stop_flag.set()
        # Shut down worker pool first (let current tasks finish)
        if self.goal_worker_pool:
            try:
                self.goal_worker_pool.shutdown(timeout=30)
            except Exception as e:
                logger.debug("Worker pool shutdown error: %s", e)
        if self.dream_thread:
            self.dream_thread.join(timeout=5)
        if self.learning_system:
            try:
                self.learning_system.flush()
            except Exception as e:
                logger.debug("Learning system flush failed: %s", e)
        logger.info("Dream cycle monitoring stopped")

    def _should_run_cycle(self) -> bool:
        """Decide whether a dream cycle would accomplish anything.

        Returns False (skip) when there's no pending work AND the
        suggest-work cooldown hasn't expired yet — avoids the pattern of
        waking up every 30 s just to discover there's nothing to do.
        """
        if self._has_pending_work():
            return True  # Always run if there are goals/tasks to execute

        # No work — only worth running if suggest_work cooldown has expired
        if self._last_suggest_time and (
            datetime.now() - self._last_suggest_time
        ).total_seconds() < idea_generator.SUGGEST_COOLDOWN_SECS:
            return False  # Cooldown active, nothing useful to do

        return True  # Cooldown expired or never suggested — run to ask user

    def _monitor_loop(self):
        """Background thread that monitors for idle periods."""
        while not self.stop_flag.is_set():
            if self.is_idle() and not self.is_dreaming:
                # Don't start a dream cycle while image generation owns the GPU
                try:
                    from src.tools.image_gen import generating_in_progress as img_gen
                    if img_gen:
                        logger.debug("Idle but image generation in progress — skipping")
                        time.sleep(self.check_interval)
                        continue
                except ImportError:
                    pass

                if not self._should_run_cycle():
                    # Nothing to do and suggest cooldown active — sleep longer
                    # instead of churning through empty dream cycles.
                    # Use chunked sleep so kick() / stop are noticed quickly.
                    _remaining = 0.0
                    if self._last_suggest_time:
                        _elapsed = (datetime.now() - self._last_suggest_time).total_seconds()
                        _remaining = idea_generator.SUGGEST_COOLDOWN_SECS - _elapsed
                    _sleep_for = max(self.check_interval, min(_remaining, 300))
                    logger.info(
                        "Idle — no work, suggest cooldown has %.0f min left. "
                        "Sleeping %.0fs.",
                        _remaining / 60, _sleep_for,
                    )
                    _slept = 0.0
                    while _slept < _sleep_for and not self.stop_flag.is_set():
                        # Wake early if work appeared (kick sets last_activity back)
                        if self._has_pending_work():
                            break
                        time.sleep(min(5.0, _sleep_for - _slept))
                        _slept += 5.0
                    continue

                logger.info("Idle detected, starting dream cycle")
                self._run_dream_cycle()

            time.sleep(self.check_interval)

    # -- Sleep gap detection ---

    def _check_sleep_gap(self, phase_name: str, phase_start: float,
                         max_expected_seconds: float = 600) -> bool:
        """Detect if the system likely slept during a dream cycle phase.

        Returns True if a sleep gap was detected (caller should abort).
        """
        elapsed = time.monotonic() - phase_start
        if elapsed > max_expected_seconds:
            logger.warning(
                "SLEEP GAP DETECTED in phase '%s': took %.0fs (max expected %.0fs). "
                "Aborting dream cycle to avoid stale state.",
                phase_name, elapsed, max_expected_seconds,
            )
            return True
        return False

    # -- Main dream cycle orchestration ---

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
        # Check for goals with ready tasks
        for goal in self.goal_manager.goals.values():
            if not goal.is_complete() and goal.get_ready_tasks():
                return True
        # Check for undecomposed goals (they'll produce tasks once decomposed)
        for goal in self.goal_manager.goals.values():
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
            identity=self.identity,
            last_suggest=self._last_suggest_time,
            stop_flag=self.stop_flag,
            memory=self.memory,
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

        # Generate a brief rationale
        why = f"Relates to your {category} work — I thought this could help."

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
            send_notification(
                f"\U0001f4a1 I decided to work on something:\n\n"
                f"**{title}**\n"
                f"_{why}_\n\n"
                f"Est. cost: ${est_cost:.2f} "
                f"(${tracker.spend_today:.2f}/${tracker.daily_budget:.2f} initiative budget today)"
            )

        logger.info(
            "Proactive initiative created: %s (goal %s, est $%.2f)",
            title[:60], goal.goal_id, est_cost,
        )
        return True

    def _ask_user_for_work(self) -> None:
        """Brainstorm suggestions and ask the user what to work on via Discord.

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
            identity=self.identity,
            last_suggest=self._last_suggest_time,
            stop_flag=self.stop_flag,
            memory=self.memory,
        )

        if not suggestions:
            # Cooldown not met or no good ideas — just send a simple prompt
            # But only if we haven't asked recently (respect the cooldown)
            if self._last_suggest_time and (
                datetime.now() - self._last_suggest_time
            ).total_seconds() < idea_generator.SUGGEST_COOLDOWN_SECS:
                logger.info("No suggestions and cooldown active — staying quiet")
                return

            send_notification(
                "\U0001f4ad All caught up! What should I work on next?"
            )
            self._pending_suggestions = []
            return

        # Store for discord_bot to reference when user replies with a number
        self._pending_suggestions = suggestions

        # Build numbered list
        lines = ["\U0001f4ad **I'm free.** Here are some things I think could help:"]
        for i, idea in enumerate(suggestions[:5], 1):
            desc = idea.get("description", "?")[:200]
            category = idea.get("category", "")
            lines.append(f"**{i}.** [{category}] {desc}")

        lines.append(
            "\nReply with a **number** to start one, or tell me what you'd like!"
        )

        send_notification("\n".join(lines))
        logger.info("Sent %d work suggestions to user", len(suggestions))

    def _run_dream_cycle(self):
        """Execute a dream cycle (background processing).

        Flow:
          1. Morning report (if morning and not sent today)
          2. If pending work exists → execute tasks
          3. If no work → ask user for work (with suggestions)
          4. Learning review
          5. Periodic synthesis (informational only, every 10 cycles)
          6. Periodic file cleanup (every 10 cycles, offset by 5)
        """
        self.is_dreaming = True
        dream_start = datetime.now()

        try:
            logger.info("=== DREAM CYCLE START ===")

            # Phase 0: Morning report (send once per morning, 6-9 AM)
            current_hour = dream_start.hour
            if 6 <= current_hour <= 9 and self._morning_report_sent != dream_start.date():
                reporting.send_morning_report(
                    self._overnight_results, self._overnight_results_path,
                )
                self._morning_report_sent = dream_start.date()

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
                # Nothing to do — try proactive initiative, then ask user
                if not self.stop_flag.is_set():
                    initiative_started = self._try_proactive_initiative()
                    if not initiative_started:
                        self._ask_user_for_work()

            _results_after = len(self._overnight_results)
            _this_cycle_results = self._overnight_results[_results_before:_results_after]

            if self._check_sleep_gap("work_phase", _phase_t0, max_expected_seconds=900):
                logger.info("=== DREAM CYCLE ABORTED (sleep gap) ===")
                self.dream_history.append({
                    "started_at": dream_start.isoformat(),
                    "duration_seconds": (datetime.now() - dream_start).total_seconds(),
                    "tasks_processed": tasks_processed,
                    "insights": 0,
                    "interrupted": True,
                    "sleep_gap": True,
                })
                return

            # Phase 2: Review recent history (learning)
            insights = self._review_history()

            # Phase 3: Periodic synthesis (every 10 cycles, informational only)
            if not self.stop_flag.is_set() and len(self.dream_history) % 10 == 0 and len(self.dream_history) > 0:
                try:
                    self._run_synthesis()
                except Exception as se:
                    logger.debug("Synthesis skipped: %s", se)

            # Phase 4: Periodic stale file cleanup (every 10 cycles, offset by 5)
            if not self.stop_flag.is_set() and len(self.dream_history) % 10 == 5:
                try:
                    self._run_file_cleanup()
                except Exception as fce:
                    logger.debug("File cleanup skipped: %s", fce)

            dream_duration = (datetime.now() - dream_start).total_seconds()

            # Record dream cycle
            self.dream_history.append({
                "started_at": dream_start.isoformat(),
                "duration_seconds": dream_duration,
                "tasks_processed": tasks_processed,
                "insights": insights,
                "interrupted": self.stop_flag.is_set(),
            })

            logger.info("=== DREAM CYCLE END (duration: %.1fs) ===", dream_duration)

            # Persist dream cycle summary to JSONL
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
                    "ts": dream_start.isoformat(),
                    "duration_s": round(dream_duration, 1),
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
                reporting.send_hourly_summary(self._hourly_task_results)
                self._last_hourly_notify = time.monotonic()
            elif tasks_processed > 0:
                logger.info(
                    "Dream cycle: %d tasks done (hourly summary in %.0f min)",
                    tasks_processed,
                    max(0, (_HOURLY_INTERVAL - _since_last) / 60),
                )

        except Exception as e:
            logger.error("Dream cycle error: %s", e, exc_info=True)
        finally:
            self.is_dreaming = False
            self.stop_flag.clear()
            self.last_activity = datetime.now()

    # -- Learning & synthesis (kept inline — small enough) ---

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

        Runs every 10 dream cycles. Informational only — identifies themes
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

        except Exception as e:
            logger.debug("Synthesis failed: %s", e)

    def _run_file_cleanup(self) -> None:
        """Check for stale workspace files and propose cleanup via Discord.

        Runs every ~10 dream cycles.  Asks the user for approval before
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
        """Get current dream cycle status."""
        idle_time = (datetime.now() - self.last_activity).total_seconds()
        return {
            "is_dreaming": self.is_dreaming,
            "is_idle": self.is_idle(),
            "idle_seconds": idle_time,
            "queued_tasks": len(self.task_queue),
            "total_dreams": len(self.dream_history),
            "last_activity": self.last_activity.isoformat(),
            "overnight_results": len(self._overnight_results),
            "morning_report_sent_today": self._morning_report_sent == datetime.now().date(),
            "last_suggest": self._last_suggest_time.isoformat() if self._last_suggest_time else None,
            "pending_suggestions": len(self._pending_suggestions),
        }
