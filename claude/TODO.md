# Archi — Todo List

Last updated: 2026-02-17 (session 37)

---

## Open Items

- [ ] **Startup on boot (visible terminal)** — Get Archi auto-starting on laptop reboot again. Must launch in a visible terminal window, not as a background service — if Jesse logs in he needs to see it running.
- [ ] **Test concurrent goals** — Start Archi, create 2 goals via Discord. Verify logs show two workers running concurrently, chat still works, goals_state.json isn't corrupted, and shutdown is clean.
- [ ] **Test wave-based parallelism** — Create a goal with independent tasks. Verify logs show "WAVE 1: N tasks in PARALLEL". Test with `max_parallel_tasks_per_goal: 1` for regression (sequential behavior).
- [ ] **Test ask_user tool** — Create a goal with ambiguous requirements. Verify Archi sends a Discord question, blocks, accepts reply, and continues. Test quiet hours behavior.
- [ ] **Test proactive initiative** — Leave Archi idle during waking hours. Verify it self-initiates work, sends notification, stays under $0.50/day budget.

## Future Ideas

- [ ] Add more direct provider tests (Anthropic, DeepSeek, etc.)
- [ ] Provider health monitoring — auto-fallback if a direct provider is down

---

## Completed Work

<details>
<summary>Session 37 (Cowork) — Reliability & quality-of-life fixes from first real-world run</summary>

- [x] **Fixed `project_root` ImportError** — `initiative_tracker.py` and `time_awareness.py` imported `project_root` from `src.utils.paths` but it didn't exist. Added `project_root = base_path_as_path` alias in `paths.py`. Was crashing every dream cycle.
- [x] **Fixed verbose Discord notifications** — Added `_humanize_task()` helper to `reporting.py` that parses raw PlanExecutor commands into readable summaries (e.g., `web_search('NMN efficacy')` → `Researched NMN efficacy`). Applied to morning reports, hourly summaries, and goal completions. Report size dropped from ~5,500 to ~720 chars.
- [x] **Fixed false task success marking** — PlanExecutor now sets `_force_aborted = True` when JSON retry fails, so the existing success-determination logic correctly marks the task as failed instead of silently succeeding.
- [x] **Fixed orchestrator ignoring task success status** — `task_orchestrator.py` now checks `result.get("executed", False)` to distinguish real success from force-aborted tasks. Softened wave failure logic to only stop if ALL tasks in a wave fail.
- [x] **Goal completion notifications surface actual findings** — `send_user_goal_completion()` now extracts PlanExecutor's `Done:` summary and includes the actual answer (e.g., "Embrace Pet Insurance recommended for Rat Terrier") in the notification instead of just listing filenames.
- [x] **Search rate limiting** — Added thread-safe 1.5s minimum interval between web searches across all threads in `web_search_tool.py` to prevent 429s/403s from parallel tasks.
- [x] **PlanExecutor efficiency rules** — Updated system prompt with "EFFICIENCY RULES" section: 2-4 searches max before writing, synthesize into one `create_file`, move on from failed fetches. Changed `append_file` description to discourage incremental appending.
- [x] **Discord reply context extraction** — `discord_bot.py` now extracts `message.reference` content when users reply to a specific notification, prepending `[Replying to Archi's message: "..."]` so the model knows which topic the user is responding to.
- [x] **Keyword-inferred reply topic** — When users type without using Discord's reply feature, `_infer_reply_topic()` does keyword overlap matching against recent back-to-back notifications. Only triggers when one notification clearly matches better (≥2 keywords, ≥2 advantage over runner-up).
- [x] **Suggest cooldown reset on initiative failure** — When a self-initiated goal fails, `_maybe_clear_suggest_cooldown()` in `goal_worker_pool.py` resets the dream cycle's `_last_suggest_time` so Archi can try something else instead of sitting idle for an hour.
- [x] **Idle-state visibility logging** — Changed the dream cycle's cooldown sleep log from DEBUG to INFO level, now shows "Idle — no work, suggest cooldown has Xmin left. Sleeping Ys."

**Files modified:** `src/utils/paths.py`, `src/core/reporting.py`, `src/core/plan_executor.py`, `src/core/task_orchestrator.py`, `src/tools/web_search_tool.py`, `src/interfaces/discord_bot.py`, `src/core/goal_worker_pool.py`, `src/core/dream_cycle.py`

</details>

<details>
<summary>Session 36 (Cowork) — Companion personality, ask-user, proactive initiative</summary>

- [x] **Companion personality** — Replaced "Professional digital symbiont" with "Helpful AI teammate and companion" across identity config, prime directive, and system prompt. Warmer notification messages. Removed "Companion personality" from open items.
- [x] **Time awareness utility** (`src/utils/time_awareness.py`) — Reads timezone and working_hours from `archi_identity.yaml`. Functions: `is_quiet_hours()`, `is_user_awake()`, `time_until_awake()`, `get_user_hour()`. Foundation for ask-user and initiative.
- [x] **Ask-user tool** — New `ask_user()` function in `discord_bot.py` following the existing `request_source_approval()` blocking pattern. Sends a Discord DM, blocks the worker thread until Jesse replies or timeout (5 min). Time-aware: returns None during quiet hours. New `_do_ask_user()` tool in PlanExecutor so tasks can call `{"action": "ask_user", "question": "..."}` mid-execution.
- [x] **Proactive initiative** — New `InitiativeTracker` (`src/core/initiative_tracker.py`) with $0.50/day budget (configurable in `rules.yaml`). Dream cycle now tries self-initiating work before asking the user. Picks top suggestion from idea generator, creates goal at priority 4 (below user work), notifies Jesse what it's working on and why. Capped at 2 initiatives/day, respects quiet hours.
- [x] **Builder mindset nudges** — Strengthened code-writing emphasis in PlanExecutor MINDSET block ("CODE IS YOUR SUPERPOWER"), goal decomposition prompt ("BUILD THINGS, DON'T DESCRIBE THEM"), and prime directive ("Builder Mindset" principle).

</details>

<details>
<summary>Session 35 (Cowork) — Layered orchestration / parallel task execution</summary>

- [x] **Created TaskOrchestrator** (`src/core/task_orchestrator.py`) — Wave-based parallel task execution within a single goal. Identifies independent tasks (no mutual dependencies) and fans them out to `ThreadPoolExecutor` threads. Tasks are grouped into "waves": Wave 1 runs all tasks with no unmet deps in parallel, Wave 2 runs tasks whose deps were all in Wave 1, etc. Same API cost, faster wall-clock time. Configurable via `rules.yaml` (`task_orchestrator.max_parallel_tasks_per_goal`, default 2, hard cap 4).
- [x] **Parallelism-aware decomposition** — Updated the goal decomposition prompt in `goal_manager.py` to teach the LLM about parallelism: prefer empty dependency arrays for independent tasks, use dependencies only when one task truly needs another's output. Added good/bad examples.
- [x] **Added `Goal.get_execution_waves()`** — Helper method on `Goal` class that returns the wave structure as a list of lists for logging/debugging. Uses the same dependency logic as `get_ready_tasks()`.
- [x] **Integrated orchestrator into GoalWorkerPool** — Replaced the sequential main task loop in `_execute_goal()` with a single `TaskOrchestrator.execute_goal_tasks()` call. Kept crash-recovery resume loop and goal completion notifications.
- [x] **Orchestrator config** — Added `task_orchestrator` section to `rules.yaml` with `enabled: true` and `max_parallel_tasks_per_goal: 2`.

</details>

<details>
<summary>Session 34 (Cowork) — Concurrent worker pool architecture</summary>

- [x] **Thread safety for GoalManager** — Added `threading.RLock()` protecting all public methods (`create_goal`, `decompose_goal`, `add_follow_up_tasks`, `get_next_task`, `start_task`, `complete_task`, `fail_task`, `get_status`, `save_state`). Lock released during `model.generate()` calls in `decompose_goal` to avoid blocking. Added `get_next_task_for_goal(goal_id)` so workers only pick tasks from their own goal.
- [x] **Thread safety for ModelRouter** — Added `threading.Lock()` protecting `_stats` dict updates in `_use_api()`, `chat_with_image()`, and `get_stats()`.
- [x] **Thread safety for LearningSystem** — Added `threading.Lock()` protecting all mutable state (experiences, patterns, metrics, action_stats). Lock released during `model.generate()` calls in `extract_patterns()` and `get_improvement_suggestions()`.
- [x] **Created GoalWorkerPool** (`src/core/goal_worker_pool.py`) — `ThreadPoolExecutor`-backed pool (default 2 workers, configurable, hard cap 4). Each worker independently decomposes and executes one goal. Per-goal budget cap ($1.00 default). Tracks `GoalWorkerState` per worker for monitoring. Discord notifications on completion/failure. Graceful shutdown with timeout.
- [x] **Refactored DreamCycle to dispatcher** — `_run_dream_cycle()` now submits goals to the worker pool instead of calling `process_task_queue()`. Falls back to old sequential executor if pool unavailable. Pool created in `enable_autonomous_mode()` or lazily via `set_router()`.
- [x] **Direct pool submission** — `kick(goal_id)` now submits directly to the worker pool for zero-latency start. Updated all 4 call sites: `action_dispatcher.py`, `discord_bot.py` (suggestion picks), `message_handler.py` (deferred requests + auto-escalation).
- [x] **Worker pool config** — Added `worker_pool` section to `config/rules.yaml` with `max_workers: 2` and `per_goal_budget: 1.00`.
- [x] **Removed architecture review from open items** — Completed by this session's concurrent architecture overhaul.

</details>

<details>
<summary>Session 33 (Cowork) — Unthrottle & make Archi do real work</summary>

- [x] **Rewrote task decomposition prompt** — Replaced "Focus on research + file creation" with explicit instructions to produce real deliverables, not gap reports or summaries of what needs to be done. Added good/bad task examples. In `goal_manager.py`.
- [x] **Raised dream cycle time cap to 120 min** — Old 10-minute cap was from local model era and forced goals to be spread across multiple cycles with idle gaps. Budget cap ($0.50/cycle) is the real safety net. In `autonomous_executor.py`.
- [x] **Eliminated idle dream cycle churn** — Added `_should_run_cycle()` check in `dream_cycle.py`. When no goals exist and suggest cooldown is active, the monitor loop now sleeps until the cooldown expires instead of spinning up empty dream cycles every 5 minutes. Chunked sleep (5s intervals) ensures `kick()` and `stop` are still responsive.
- [x] **Immediate goal execution** — Added `kick()` method to `DreamCycle` that back-dates `last_activity` so work starts within seconds of goal creation instead of waiting 5 minutes. Called from `action_dispatcher.py`, `discord_bot.py` (suggestion picks), and `message_handler.py` (deferred + auto-escalation). Updated user-facing messages: "Starting on it now" instead of "I'll get to it during my next dream cycle."
- [x] **Discord file attachments** — `send_notification()` now accepts optional `file_path` to attach files via `discord.File`. New `send_file` action + handler in `action_dispatcher.py`. Added to intent classifier so "send me the file" routes correctly.
- [x] **Audit loops & heartbeat (partial)** — Diagnosed the idle churn problem (dozens of 0.0s dream cycles doing nothing). Fixed via `_should_run_cycle()`. Heartbeat itself is still useful for adaptive sleep tiers.
- [x] **Fixed intent classifier routing** — `multi_step` (12-step chat) was capturing project-scale work that should go to `create_goal` (50-step dream cycle). Rewrote action descriptions: `create_goal` is now the default for any non-trivial project work; `multi_step` is explicitly scoped to quick tasks the user is waiting for. In `intent_classifier.py`.
- [x] **Added "build, don't report" mindset to PlanExecutor** — Inserted a MINDSET block in the system prompt that tells Archi to produce real deliverables (working code, complete protocols, functional systems) instead of summaries and gap analyses. Changed workspace file descriptions from "reports, research output" to "project deliverables, code, content." In `plan_executor.py`.

</details>

<details>
<summary>Session 32 — Dream cycle effectiveness overhaul</summary>

- [x] **Fixed model to reasoning variant** — Default model was set to `grok-4-1-fast-non-reasoning`, causing the model to loop without planning. Changed to `grok-4-1-fast-reasoning` in `providers.py`.
- [x] **Fixed path-blind loop detection** — `list_files` on different directories all registered as the same action key, triggering false loop abort after 3 calls. Now includes the path in the key: `f"{action_type}:{path[:60]}"`. Same for `read_file`, `web_search`, and `fetch_webpage`.
- [x] **Added sibling task context sharing** — Tasks in the same goal now receive summaries of previously completed sibling tasks as hints, so they build on earlier work instead of starting from scratch. Implemented in `autonomous_executor.py`.
- [x] **Added step budget awareness** — PlanExecutor prompt now includes step count and remaining budget. Warns model to transition from research to output at the halfway point, and urgently at 3 steps remaining. In `plan_executor.py`.
- [x] **Raised MAX_STEPS_PER_TASK to 50** — Old limit of 15 was from the local model era. Budget ($0.50/cycle) and time (10 min) caps are the real safety nets. In `plan_executor.py`.
- [x] **Model-inferred goal creation** — Intent classifier can now recognize when a chat request is too large for a quick response and automatically create a goal, responding conversationally (e.g., "This is going to take some real work, so I'll handle it in the background."). In `intent_classifier.py` and `action_dispatcher.py`.
- [x] **Auto-escalation for overflowing chat tasks** — When a chat PlanExecutor uses all its steps without producing output (still researching), it auto-creates a goal and responds: "Can I take some time to think about it? I'll work on it in the background." In `message_handler.py`.
- [x] **Removed cost display from Discord messages** — The `(Cost: $X.XXXX)` footer on every reply was distracting. Removed from both text and image response paths in `discord_bot.py`.

</details>

<details>
<summary>Session 31 — Goal-driven idle behavior</summary>

- [x] **Goal-driven idle behavior** — Replaced autonomous work generation with user-driven flow. When idle with no goals, Archi brainstorms suggestions and presents them via Discord with numbered picks. User decides what to work on — Archi never auto-approves or creates goals on its own.
- [x] **Merged brainstorm + plan_future_work** into `suggest_work()` — single system, runs when idle with nothing to do, always asks user.
- [x] **Synthesis → informational only** — No longer creates follow-up goals. Logs themes to `synthesis_log.jsonl` for morning report.
- [x] **Follow-up extraction → within-goal tasks** — `extract_follow_up_goals()` replaced with `extract_follow_up_tasks()`. Adds tasks to the current goal instead of spawning new goals. Prevents unbounded goal chains.
- [x] **Discord suggestion picking** — User can reply "1", "2", "#3" etc. to pick a brainstormed suggestion. Creates a goal from the chosen idea.
- [x] **Removed proactive_tasks from archi_identity.yaml** — No longer used.

</details>

<details>
<summary>Session 30 — Provider routing & cache fix</summary>

- [x] **P2-19: cache.py O(n) LRU eviction** — Replaced `List` with `OrderedDict` for O(1) `move_to_end()`/`popitem()`. Deleted `_mark_accessed()`.
- [x] **Direct API provider support** — New `src/models/providers.py` (registry, aliases, pricing). Generalized `openrouter_client.py` for any OpenAI-compatible endpoint. Router defaults to xAI direct, falls back to OpenRouter. Discord: "switch to grok direct", etc.
- [x] **Renamed ARCHI_TODO.md → TODO.md** — Updated 8 cross-references across claude/ docs.
- [x] **.env/.env.example sync** — Removed dead local-model vars, added provider key placeholders, added missing DISCORD_OWNER_ID and ARCHI_WHISPER_MODEL.

</details>

<details>
<summary>Sessions 23–29 — Seven-phase codebase audit</summary>

Full audit across 7 phases, cleaning up after the local-to-API migration:

- **Phase 1 (session 23):** Config & project root — fixed stale scripts, added PID lock, cross-platform venv detection, protected claude/ in .gitignore.
- **Phase 2 (session 24):** Models & utilities — deleted local_model.py, backends/, model_detector.py, cuda_bootstrap.py. Rewrote router.py (609→531 lines). Consolidated `strip_thinking`, fixed cost tracker key mismatch.
- **Phase 3 (session 25):** Core engine — removed dead imports, consolidated `_extract_json` into `src/utils/parsing.py`, removed `prefer_local` from 15 call sites, deduplicated goal-counting functions.
- **Phase 4 (session 26):** Interfaces & tools — fixed brainstorm approval bug, removed stale system prompt refs, deleted 3 orphan modules + `src/web/`, added workspace mkdir at startup, added API health check.
- **Phase 5 (session 27):** Tests — deleted 21 script-style test files, fixed integration tests to use free models, integrated diagnostics into fix.py.
- **Phase 6 (session 28):** Data & workspace — cleaned stale data files, renamed synthesis_log to .jsonl, added missing .env.example vars, created workspace/.gitkeep.
- **Phase 7 (session 29):** Documentation — updated project trees in README.md and ARCHITECTURE.md (17 missing files), populated CODE_STANDARDS.md, fixed test_harness.py path in 3 docs, added Discord permissions to README.

</details>

<details>
<summary>Sessions 7–19 — API migration, v2 architecture, features</summary>

- **API-first migration (7–9):** Switched all model calls from local to Grok/OpenRouter, stopped loading local LLM at startup (saved ~6GB VRAM), added system role messages for prompt caching.
- **V2 architecture (10–13):** Built intent_classifier, response_builder, action_dispatcher, message_handler pipeline. Split dream_cycle.py (1,701→4 modules). Created 36 integration tests.
- **Computer use (15–16):** Auto-escalation to Claude for computer use, screenshot sending with zero-cost fast-path.
- **Goal system & dream cycle (9, 14–17):** Goal relevance filter, artifact requirements, aggressive caps, purpose-driven brainstorming, proactive Discord notifications, stale file cleanup.
- **Multi-step chat (17):** Cancel/interrupt support, smart step estimates.
- **Behavioral (18–19):** Epistemic humility, proactive follow-up, deferred request handling.

</details>

<details>
<summary>Sessions 1–6 — Foundation</summary>

Conversation fixes, classifier refinements, goal/research deduplication, dream cycle throttling, safety gates (source code approval, loop detection, claude/ protection), context window improvements, runtime model switching via Discord.

</details>
