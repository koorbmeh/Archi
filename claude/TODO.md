# Archi — Todo List

Last updated: 2026-02-18 (session 43)

---

## Open Items

- [x] **Fix empty vector store / memory not persisting across restarts** — (Session 41) Vector store data WAS persisting (LanceDB table at data/vectors/archi_memory.lance survived restarts). The 0-entries count was because nothing ever wrote to it — the only write path was gated behind `_learning_success`, and all tasks had failed. Fixed in autonomous_executor.py to store both successes and failures.
- [ ] **Re-evaluate loops, heartbeat, dream cycle** — Are the current patterns producing the outcomes we want? Review whether the heartbeat tiers, dream cycle structure, and monitoring loops are serving us well or need rethinking.
- [ ] **Startup on boot (visible terminal)** — Get Archi auto-starting on laptop reboot again. Must launch in a visible terminal window, not as a background service — if Jesse logs in he needs to see it running.
- [x] **Fix Ctrl+C shutdown responsiveness & ghost processes** — (Session 41) Root cause: GoalWorkerPool.shutdown() blocked forever on `executor.shutdown(wait=True)` while PlanExecutor finished its full task. Fix: Ctrl+C now prints status to console, triggers PlanExecutor cancellation (`signal_task_cancellation`) so running tasks bail at next step boundary, worker pool has real timeout with visible countdown. Discord bot explicitly closed on shutdown via `_bot_client.close()`. stop.py rewritten as nuclear kill — uses `proc.kill()` / `taskkill /F /T`, matches by cmdline + cwd + project path, double-taps survivors.
- [x] **Fix loop detection — two complementary fixes** — (Session 41) Two separate problems: (1) Old code counted TOTAL occurrences per action type across the whole task, so legitimate patterns like read→append→read→fetch→read (3 total reads on same file) got falsely killed. Fix: switched to CONSECUTIVE identical action key counting, so intervening actions break the chain. Added write-then-read exemption (`read_file:X` after `create_file:X`/`append_file:X` tagged as `read_file_verify:X`). (2) When model IS genuinely stuck (consecutive identical actions), old code killed silently with no chance to recover. Fix: escalating warnings injected into prompt — repeat 2 → nudge, repeat 3 → strong warning, repeat 4 → force abort (model saw both warnings and still looped).
- [ ] **Review architecture for better approaches** — Step back and consider whether there's a better way to do any of the things we've already programmed Archi to do. Fresh eyes on the overall design.
- [x] **Help Archi make progress on current goals** — (Session 42) Root cause: `suggest_work()` brainstormed generic "research X, write markdown" topics. Fixed with Opportunity Scanner that reads real project files, error logs, and unused capabilities to propose typed work (build/ask/fix/connect/improve). Type-aware decomposition hints and "FUNCTIONAL OUTPUT PRIORITY" in PlanExecutor prompt. Needs live testing to confirm goals actually produce useful deliverables now.
- [ ] **Test opportunity scanner live** — Start Archi, let it go idle, watch logs for scanner output. Verify suggestions include build/ask/fix types (not just "research X"). Verify first dream cycle produces actionable goals like "Build supplement tracker" instead of "Research supplement timing". Test fallback by disabling scanner.
- [ ] **Test concurrent goals** — Start Archi, create 2 goals via Discord. Verify logs show two workers running concurrently, chat still works, goals_state.json isn't corrupted, and shutdown is clean.
- [ ] **Test wave-based parallelism** — Create a goal with independent tasks. Verify logs show "WAVE 1: N tasks in PARALLEL". Test with `max_parallel_tasks_per_goal: 1` for regression (sequential behavior).
- [ ] **Test ask_user tool** — Create a goal with ambiguous requirements. Verify Archi sends a Discord question, blocks, accepts reply, and continues. Test quiet hours behavior.
- [ ] **Test proactive initiative** — Leave Archi idle during waking hours. Verify it self-initiates work, sends notification, stays under $0.50/day budget.
- [x] **Fix PLAN_MAX_TOKENS truncation killing all tasks** — (Session 43) Root cause: `PLAN_MAX_TOKENS = 1000` was too low for Grok's reasoning model, which spends tokens on `<think>` blocks before producing JSON. Responses hitting exactly 1000 output tokens got truncated mid-JSON, `extract_json()` failed, retry also hit 1000, task force-aborted. 27 instances in one run. Fix: raised to 4096. Cost impact: ~$0.003/step max → ~$0.012/step max, well within per-cycle budget.
- [x] **Fix write_source/edit_file not tracked by path in loop detection** — (Session 43) `write_source` and `edit_file` action keys didn't include the file path, so writing to different files looked like the same action to the loop detector. Added path-based keying (matching `create_file`/`append_file`). Also extended write-then-read exemption to cover `write_source` and `edit_file` so the healthy verify pattern (write → read back) isn't penalized.
- [x] **Add SSL cert diagnostic logging** — (Session 43) arxiv.org still failing with CERTIFICATE_VERIFY_FAILED despite session 41 fix. Added diagnostic logging to show whether certifi loaded successfully or fell back to system default. Will help diagnose on next run.
- [x] **Fix rewrite-loop detection (total writes per path)** — (Session 43) After max_tokens fix, Grok's new failure mode was rewriting the same output file 5-10+ times without calling `done`, breaking the consecutive detector by inserting reads/searches between writes. Added total-writes-per-path counter: nudge at 3, strong at 5, kill at 7.
- [ ] **Make Archi's Discord messages less spammy and more conversational** — Status emojis (❌, ✅) are good — keep those. The problem is volume and repetitive formatting. Every task start, every task fail, every goal update sends its own message in the same rigid format, and after a while it reads like a wall of automated alerts. Goals: (1) Consolidate notifications — one message per goal completion (not one per task). Batch results: "Finished working on your health tracker — got the schema and logger done, but the analyzer hit a wall trying to install pandas." (2) Don't send intermediate progress messages unless the user asks or something noteworthy happens. (3) Failures should say what went wrong in plain language, not dump internal error strings or task IDs. (4) Proactive messages (dream cycle ideas, self-initiated work) should feel like a person mentioning something, not a system notification. (5) Chat responses should sound natural, not like a bot reading from a template. Touches: `discord_bot.py` (notification formatting/batching), `message_handler.py` (response tone), `autonomous_executor.py` / `task_orchestrator.py` (completion notifications), chat system prompt (personality).

## Future Ideas

- [ ] **Store conversation context in long-term memory** — Currently the only write path to the vector store is task completion (successes and failures). Conversations with the user, learned preferences, corrections, and important context are never stored. Need at least: (1) store notable user messages/instructions during chat (message_handler.py), (2) store dream cycle summaries/decisions, (3) periodically consolidate short-term memory into long-term. The vector store and MemoryManager are working and persistent — they just need more callers.
- [ ] **Wire user_preferences into project_context** — When Archi learns something from conversation (e.g., "I'm not doing the job search anymore"), update `current_projects` or `interests` in `project_context.json` automatically.
- [ ] **Discord command to add/remove projects** — Let Jesse manage active_projects via chat instead of editing JSON manually.
- [ ] Add more direct provider tests (Anthropic, DeepSeek, etc.)
- [ ] Provider health monitoring — auto-fallback if a direct provider is down

---

## Completed Work

<details>
<summary>Session 43 (Cowork) — Fix task failures: max_tokens truncation, loop detection, SSL diagnostics</summary>

- [x] **Fixed PLAN_MAX_TOKENS truncation (1000 → 4096)** — The #1 cause of task failures. Grok's reasoning model uses `<think>` blocks that consumed most of the 1000-token budget, leaving the JSON action truncated and unparseable. 27 out of ~80 API responses in one run hit exactly 1000 output tokens. Every "JSON retry failed" in the logs was caused by this. Raising to 4096 gives ample room for reasoning + JSON output.
- [x] **Added write_source/edit_file to path-based loop detection** — These action types fell through to bare `action_type` keys (no path), so writing to different files looked like repeating. Now keyed by path like `create_file` and `append_file`. Extended write-then-read exemption to cover all four write actions.
- [x] **Added SSL cert diagnostic logging** — certifi may not be loading correctly (arxiv.org still failing despite session 41 fix). Added logging at module load to show whether certifi CA bundle or system default is in use. Will diagnose on next run.
- [x] **Confirmed progress_callback fix already in place** — The image gen `progress_cb` signature mismatch (1 arg vs 3) was from an older code version; current code at line 249 correctly passes `(step_num, count, status_text)`.
- [x] **Added total-writes-per-path rewrite-loop detection** — After live test confirmed max_tokens fix eliminated JSON truncation (zero "JSON retry failed"), the new #1 failure was Grok rewriting the same output file 5-10+ times without calling `done`. The consecutive detector missed this because the model breaks chains with reads/searches in between. New tracker counts total writes per path across the whole task: nudge at 3, strong warning at 5, force-abort at 7. Complements the existing consecutive detector.

**Files modified:** `src/core/plan_executor.py`

</details>

<details>
<summary>Session 42 (Cowork) — Opportunity Scanner: make Archi actually useful</summary>

- [x] **Created Opportunity Scanner** (`src/core/opportunity_scanner.py`, ~350 lines) — Replaced brainstorm prompt in `suggest_work()` with structured scanners that read real data. Four scanners: `scan_projects()` reads vision/overview files and compares to existing files to find build gaps; `scan_errors()` reads 3 days of error logs grouped by module; `scan_capabilities()` checks which tools (ask_user, run_python, write_source) have never been used; `scan_user_context()` reads recent conversations and vector memory. Combiner deduplicates by word overlap >0.5, ranks by value/effort, caps to top 7. Returns typed `Opportunity` dataclass objects (build/ask/fix/connect/improve).
- [x] **Populated project_context.json** — Was empty `{}`, meaning Archi had zero awareness of actual projects. Populated with Health Optimization and Archi Self-Improvement project data including real file paths, descriptions, focus areas, and autonomous tasks.
- [x] **Auto-populate project context** — Added `auto_populate()` to `src/utils/project_context.py`. Scans `workspace/projects/` subdirectories, reads vision files, builds structured context, merges with existing (preserving user customizations). Called from `dream_cycle._load_project_context()` when context is empty.
- [x] **Type-aware goal decomposition** — `goal_manager.py` now detects opportunity type via `infer_opportunity_type()` and injects type-specific hints: build→"First task MUST create a working .py script", ask→"First task MUST use ask_user", fix→"read source and error logs first", connect→"read existing code first".
- [x] **Builder prompts in PlanExecutor** — Added "FUNCTIONAL OUTPUT PRIORITY" block to MINDSET section: test code after writing, use ask_user for data Jesse already has, write_source + run_python is the power combo, working 30-line script beats 200-line report.
- [x] **Scanner integration in idea_generator.py** — `suggest_work()` now calls `scan_all()` first, converts Opportunities to backward-compatible idea dicts, falls back to `_brainstorm_fallback()` (preserved old brainstorm prompt) if scanner returns nothing. All existing filtering (dedup, relevance, purpose-driven, memory dedup) still applies downstream.
- [x] **Dream cycle auto-populate** — `_load_project_context()` checks if loaded context has `active_projects`, calls `auto_populate()` if empty.

**Files created:** `src/core/opportunity_scanner.py`
**Files modified:** `src/core/idea_generator.py`, `src/utils/project_context.py`, `src/core/goal_manager.py`, `src/core/plan_executor.py`, `src/core/dream_cycle.py`, `data/project_context.json`

</details>

<details>
<summary>Session 41 (Cowork) — SDXL batch efficiency, SSL cert fix, timing tuning</summary>

- [x] **SDXL batch pipeline reuse** — Pipeline was being loaded and unloaded for every single image in a batch (~4s overhead each). Added `keep_loaded` parameter to `generate()`, pipeline reuse via `_loaded_model` tracking, and explicit `unload()` method. Router now persists `_image_gen` instance across batch calls. Dispatcher wraps batch loop in try/finally with `finish_image_batch()` cleanup. **Verified in logs: 10-image batch at 08:16 shows "Reusing loaded pipeline" 9 times, saving ~36s.**
- [x] **SSL certificate fix for web research** — `urllib.request.urlopen` on Windows doesn't use certifi's CA bundle, causing `CERTIFICATE_VERIFY_FAILED` on arxiv.org and other sites. Added `ssl.create_default_context(cafile=certifi.where())` and passed to all `urlopen` calls. **Verified in logs: arxiv.org and lilianweng.github.io fetch successfully after fix. No more SSL errors.**
- [x] **Reduced idle/cooldown timers** — `idle_threshold` from 300s → 60s (Archi starts working after 1 minute of idle instead of 5). `check_interval` from 30s → 15s. `SUGGEST_COOLDOWN_SECS` from 3600 → 600 (10 min between suggestion prompts instead of 1 hour). **Verified in logs: dream cycle started ~3 min after last activity instead of waiting 5+.**
- [x] **Log review & new issue identification** — Identified loop detection still too aggressive for read_file (3 repeats triggers abort during legitimate verify-your-work patterns), memory persistence broken (0 entries on every startup), and Brave search 429 rate limiting.
- [x] **Shutdown hardening (ghost process fix)** — Archi sent a Discord message minutes after Ctrl+C because GoalWorkerPool.shutdown() called `executor.shutdown(wait=True)` which blocked forever while PlanExecutor finished its API calls. Four fixes: (1) Ctrl+C signal handler now prints to console and triggers `signal_task_cancellation("shutdown")` so PlanExecutor bails at next step. (2) GoalWorkerPool.shutdown() uses real timeout with console progress, calls `shutdown(wait=False)` + per-future deadlines. (3) archi_service.stop() explicitly closes Discord bot via `asyncio.run_coroutine_threadsafe(_bot_client.close(), _bot_loop)`. (4) stop.py rewritten as nuclear kill — `proc.kill()` / `taskkill /F /T`, triple detection (cmdline identifiers + cwd + project path), double-tap survivors.

- [x] **Memory storage fix** — Vector store was always 0 entries because the only write path (`store_long_term`) was gated behind `_learning_success`, and every task had failed. Now stores BOTH successes (type: `research_result`) and failures (type: `task_failure`) so Archi builds memory regardless. Failure memories include error info, last actions, and summary to avoid repeating mistakes.
- [x] **Loop detection rewrite — consecutive counting + escalating warnings** — Two complementary fixes: (1) Switched from total-occurrence counting to CONSECUTIVE identical action key counting. Old code counted all `read_file:path` across the entire task (3 total = killed), which falsely killed legitimate patterns like read→append→read→fetch→read. New code only counts consecutive identical keys, so any different action in between resets the counter. (2) Added escalating warnings instead of silent kill: repeat 2 → nudge, repeat 3 → strong warning, repeat 4 → force abort. Also added write-then-read exemption (`read_file:X` after `create_file:X`/`append_file:X` tagged as `read_file_verify:X`). Verified against all 4 log kills: kills #1/#2 (genuine loops) still caught, kill #3 (false positive) no longer triggers, kill #4 (borderline) gets warnings first.

**Files modified:** `src/tools/image_gen.py`, `src/models/router.py`, `src/interfaces/action_dispatcher.py`, `src/core/plan_executor.py`, `src/tools/web_search_tool.py`, `src/utils/config.py`, `src/core/idea_generator.py`, `src/core/agent_loop.py`, `src/core/goal_worker_pool.py`, `src/service/archi_service.py`, `scripts/stop.py`, `src/core/autonomous_executor.py`

</details>

<details>
<summary>Session 40 (Cowork) — SDXL diagnostics, duplicate MemoryManager, image gen fast-path & multi-image</summary>

- [x] **Diagnosed real SDXL failure** — The `except ImportError` in `_load_pipeline()` was catching the actual error but logging a misleading "diffusers not installed" message. Changed to `except ImportError as e` with the real error in the log. Added `check_dependencies()` method that tests each package individually (torch, diffusers, transformers, accelerate, safetensors, StableDiffusionXLPipeline) — runs automatically on pipeline failure and logs per-package status.
- [x] **Improved CUDA detection** — `_detect_device()` now distinguishes between CPU-only torch builds (no `torch.version.cuda`) and CUDA builds with runtime issues. Logs the GPU name on success. Tells you exactly how to reinstall with CUDA when it's a CPU-only build.
- [x] **Fixed duplicate MemoryManager** — `agent_loop.py` was creating its own `MemoryManager()` independently of the dream_cycle's background-initialized one. Two separate VectorStore instances, two embedding model loads. Fix: added `memory` parameter to `run_agent_loop()`, `archi_service.py` now waits for the dream_cycle's memory init thread and passes the shared instance.
- [x] **Image generation fast-path** — Added zero-cost fast-path in `intent_classifier.py` for obvious image requests ("generate an image of...", "draw me...", "paint..."). Skips the LLM call entirely. Also handles multi-image: regex pattern catches "generate 3 images of X" and extracts both prompt and count.
- [x] **Multi-image support** — `_handle_generate_image()` in `action_dispatcher.py` now accepts a `count` parameter (max 10), generates sequentially, stops on first failure, reports all paths. Updated the intent instruction to show the count parameter and explicitly tell Grok to ALWAYS use the action (never describe generating without calling it).
- [x] **Image hallucination detection** — Added image-related claim phrases to `_is_chat_claiming_action_done()`: "i've generated", "images are saved", "saved to workspace/images", "here are your pictures", etc. Catches Grok hallucinating image generation instead of calling the tool.

**Files modified:** `src/tools/image_gen.py`, `src/core/agent_loop.py`, `src/service/archi_service.py`, `src/interfaces/intent_classifier.py`, `src/interfaces/action_dispatcher.py`

</details>

<details>
<summary>Session 39 (Cowork) — Loop detection fix, double router init, primp warning, startup speed</summary>

- [x] **Fixed create_file loop detection false positives** — `create_file` and `append_file` action keys now include the file path (previously they were just the bare action name, so creating 3 different files in a row triggered the loop detector). Also raised the repeat threshold from 3 to 5 for file-write actions, since overwrite-to-refine (draft → read back → rewrite better) is legitimate behavior.
- [x] **Fixed double ModelRouter initialization** — `archi_service.py` called `enable_autonomous_mode()` before `set_router()`, causing the dream cycle to lazy-initialize a duplicate router. Swapped the call order so the shared router is available when `enable_autonomous_mode` calls `_get_router()`.
- [x] **Noted primp/ddgs version mismatch** — The `chrome_120` impersonation warning comes from a version mismatch between `ddgs` and `primp`. Added upgrade instructions to `requirements.txt`. Fix: `pip install --upgrade ddgs primp`.
- [x] **Fixed slow startup (36s gap from sentence-transformers import)** — Moved `lancedb` and `sentence_transformers` imports from module-level to inside `VectorStore.__init__()` so the delay appears in logs instead of being invisible. More importantly, moved the entire `MemoryManager()` initialization to a background thread in `DreamCycle.__init__()`. Startup no longer blocks on torch/ML imports — memory comes online asynchronously and the `GoalWorkerPool` reference is updated when ready.

**Files modified:** `src/core/plan_executor.py`, `src/service/archi_service.py`, `src/memory/vector_store.py`, `src/core/dream_cycle.py`, `requirements.txt`

</details>

<details>
<summary>Session 38 (Cowork) — Identity config split + reset.py hardening</summary>

- [x] **Split identity config into static + dynamic** — `config/archi_identity.yaml` now holds only static config (name, role, timezone, working hours). Dynamic project data moved to `data/project_context.json`, which Archi can update at runtime. Created `src/utils/project_context.py` as centralized load/save/scan module with fallback to legacy identity yaml.
- [x] **File scanning in idea generator** — `suggest_work()` now calls `scan_project_files()` to inject actual file listings into Grok's brainstorming prompt. Prevents hallucination of nonexistent files like `gaps.md`.
- [x] **Rewrote autonomous_tasks** — Changed from hallucination-prone phrases ("Identify gaps in current protocol") to grounded ones ("Review existing files in the project and suggest improvements").
- [x] **Removed dead identity config keys** — `requires_approval`, `absolute_rules`, `communication`, `constraints.prefer_local`, `full_name`, `prime_directive` path — none were read by code. Removed ~90 lines of decorative config.
- [x] **Fixed reset.py: initiative_state.json** — Added to cleanup list (was missing since session 36 added initiative tracking).
- [x] **Fixed reset.py: monthly cost preservation** — Monthly cost totals now survive resets so budget enforcement isn't bypassed by resetting mid-month.
- [x] **Interactive project context reset** — `reset.py` now asks "Also clear project context? [y/N]" separately. Default preserves it. New `--clear-context` flag for automation.
- [x] **Updated all consumers** — `idea_generator.py`, `dream_cycle.py`, `autonomous_executor.py`, `message_handler.py` all switched from reading `archi_identity.yaml` to `project_context.load()`.

**Files created:** `src/utils/project_context.py`, `data/project_context.json`
**Files modified:** `config/archi_identity.yaml`, `src/core/idea_generator.py`, `src/core/dream_cycle.py`, `src/core/autonomous_executor.py`, `src/interfaces/message_handler.py`, `scripts/reset.py`

</details>

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
