# Archi — Todo List

Last updated: 2026-02-22 (session 87 — TODO cleanup, log review)

---

## Open Items

### Lower Priority

- [ ] **`git_safety.py` checkpoint may miss related files** — (Added 2026-02-22, session 67.) After switching from `git add -A` to specific-file staging, checkpoint commits only capture the single file being modified. Multi-file changes within a single task (e.g., source + test) won't be fully captured in the checkpoint. Acceptable tradeoff — purpose is safety rollback, not version history — but worth noting. `.gitignore` remains the primary defense for secrets. Touches: `git_safety.py`.

### Future Work

- [ ] **Onboarding script** — (Added 2026-02-22, session 87.) Create a guided first-run experience for new users cloning the repo. Should walk through `.env` setup (API keys, Discord token), verify prerequisites (Python, venv, CUDA optional), create initial `data/project_context.json`, and run a basic connectivity test. Currently `scripts/install.py` handles dependencies but there's no interactive setup wizard. Touches: `scripts/`.

## Completed Work

<details>
<summary>Sessions 47–86 — All resolved items (consolidated session 87)</summary>

Items moved here from Open Items during session 87 cleanup. For detailed descriptions of each fix, see git history or the session-specific archive entries below.

**Session 86:** IVF-PQ index for LanceDB vector store scalability (12 new tests).
**Session 85:** stop.py env var tagging fix (ARCHI_RUNNING_INSTANCE).
**Session 84:** Created docs/ARCHITECTURE.md onboarding guide.
**Sessions 80–83:** Core loop test coverage (agent_loop, dream_cycle, goal_manager, conversational_router — 265 tests), direct provider tests (126 tests), Discord project commands (27 tests), startup-on-boot scripts, ThreadPoolExecutor reuse in agent_loop, router.ping() public method.
**Sessions 76–78:** Code Review 2 fixes: safety.py config loading, screenshot path collision, coordinate regex, goal_manager helpers, desktop_control Popen tracking, cost_tracker path fix, memory_manager timeout, agent_loop config dedup, autonomous_executor budget dedup, MD5→SHA256.
**Session 75:** Singleton standardization, discord_bot state encapsulation, ComputerUse God class split.
**Session 74:** Test coverage expansion — plan_executor_actions, integrator, critic, autonomous_executor, memory_manager (138 tests).
**Session 73:** plan_executor.py SRP refactor → plan_executor/ package (5 submodules).
**Session 72:** Security test coverage — path_traversal, command_safety, write_path_validation, qa_evaluator, net_safety, goal_worker_pool (109 tests).
**Sessions 65–71:** Code Review 1 fixes: run_command allowlist, per-goal cancellation, lazy safety config, lazy Desktop/Browser init, MemoryManager DB INSERT, realpath security, git add specific-file, MD5→SHA256, SSRF protection, extract_json consolidation, datetime.utcnow deprecation, thread safety fixes, unbounded growth caps, dependency pinning, startup error handling.
**Sessions 60–64:** Artifact reuse, initiative explanations, deferred replies, conversation memory, project_sync, tiered model routing (Claude escalation), idea history + adaptive cooldown, shutdown cascade prevention, file creation restriction, QueueHandler logging, dream cycle ordering, clean shutdown.
**Sessions 47–59:** PlanExecutor crash fix, Architecture Evolution Phases 1–9 (QA, Critic, Notifications, Router, Planning/DAG, Integration, MCP, Graceful Degradation, Cleanup), shutdown hardening, tool registry singleton, Unicode fix, router misclassification fix, run_python workspace fix.

**Code Review findings resolved:** 5 critical, 8 security warnings, 14 logic/correctness, 8 performance/growth, 7 dependencies/config, 15+ architecture/quality items — all across sessions 65–78.

</details>

<details>
<summary>Session 77 (Cowork) — Code review fixes: 22 items from session 76 review resolved</summary>

**Safety & correctness (critical):**
- [x] **safety.py config loading — `if _prot and _blk:` bug** — Each YAML key now loaded independently. Empty list for one key no longer reverts all others to defaults. Also removed `echo` from `_DEFAULT_ALLOWED_COMMANDS`.
- [x] **recovery.py atomic writes + structural validation** — `save_state()` now writes to `.tmp` then `os.replace()`. `load_state()` validates `isinstance(state.get("steps_taken"), list)`, discards corrupt files.
- [x] **idea_generator.py prune_stale_goals lock bypass** — Added public `GoalManager.remove_goal(gid)` that acquires `_lock`. `prune_stale_goals()` now uses it + `list(goals.items())` snapshot.
- [x] **discord_bot.py concurrent approval overwrite** — Added guard: if `_pending_approval is not None and not set()`, second request is rejected (not silently lost).

**Thread safety (5 modules):**
- [x] **router.py temp model switch** — Added `_temp_lock` protecting `_temp_remaining` / `_temp_previous` in `switch_model_temp()`, `_tick_temp_switch()`, `complete_temp_task()`.
- [x] **image_gen.py module-level flags** — Added `_gen_lock` for `generating_in_progress`, `_model_lock` for `_default_model_alias` writes.
- [x] **ui_memory.py SQLite thread safety** — Added `threading.Lock` wrapping all DB operations (same pattern as timestamps.py). Added `timeout=15.0` to connection.
- [x] **memory_manager.py SQLite timeout** — Added `timeout=10` to `_init_db()` connection.
- [x] **dream_cycle.py memory init race** — Replaced bare `self.memory` with `self._memory` + `threading.Event` (`_memory_ready`). Added `@property memory` and `set_memory()` for safe access.

**Security:**
- [x] **net_safety.py DNS timeout** — `socket.setdefaulttimeout(5)` around `getaddrinfo()` call, restored in `finally`.
- [x] **vector_store.py WHERE clause** — Replaced string-interpolation escaping with allowlist validation (`_KNOWN_TYPES`). Unknown types skip the WHERE clause entirely.

**Logic fixes:**
- [x] **project_sync.py PEP 604 syntax** — Replaced `str | None` with `Optional[str]` for Python 3.9 compatibility.
- [x] **web_search_tool.py rate limiter** — Lock no longer held during sleep. Pattern: compute wait inside lock → release → sleep → re-acquire to stamp.

**Quick wins:**
- [x] **heartbeat.py dead code** — Removed `_last_system_event`, `record_system_event()`, and its sole caller in `agent_loop.py`.
- [x] **resilience.py unreachable code** — Replaced dead `if last_exception: raise` / `return None` after retry loop with defensive `raise last_exception`.
- [x] **local_mcp_server.py instance caching** — Added `_get_desktop()` / `_get_browser()` cached accessors. ~40 lines of repeated imports → 2 accessor calls.
- [x] **Protected files list** — Added `agent_loop.py`, `dream_cycle.py`, `goal_manager.py` to `config/rules.yaml` protected_files.
- [x] **requirements.txt dependency bounds** — `openai<3.0→<2.0`, `playwright` added `<2.0`, `discord.py` added `<3.0`.

801 tests passing (+1 new test), 0 failures.

**Files modified:** `src/core/plan_executor/safety.py`, `src/core/plan_executor/recovery.py`, `src/models/router.py`, `src/tools/image_gen.py`, `src/tools/ui_memory.py`, `src/memory/memory_manager.py`, `src/core/dream_cycle.py`, `src/core/idea_generator.py`, `src/core/goal_manager.py`, `src/interfaces/discord_bot.py`, `src/utils/net_safety.py`, `src/tools/web_search_tool.py`, `src/memory/vector_store.py`, `src/utils/project_sync.py`, `src/core/heartbeat.py`, `src/core/agent_loop.py`, `src/core/resilience.py`, `src/tools/local_mcp_server.py`, `config/rules.yaml`, `requirements.txt`, `tests/unit/test_command_safety.py`

</details>

<details>
<summary>Session 75 (Cowork) — Architecture & code quality: all 3 remaining items resolved</summary>

- [x] **Singleton standardization** — Standardized all singletons on double-checked locking + `_reset_for_testing()`. Added thread safety to `get_user_model()`, `get_findings_queue()`, `get_preferences()`. Made `IdeaHistory` a proper singleton via `get_idea_history()` (was creating new instances per call). Added `_reset_for_testing()` to all 5 singleton modules + `tool_registry.py`.
- [x] **discord_bot.py state encapsulation** — Eliminated cross-module private state access. Added `kick_dream_cycle()` (replaces 3 `_dream_cycle` imports) and `close_bot()` (replaces `_bot_client`/`_bot_loop` imports). No external code imports private variables anymore.
- [x] **ComputerUse God class split** — Extracted `ImageAnalyzer` to `src/tools/image_analyzer.py` (vision prompt building, API calls, coordinate parsing). `ComputerUse` now delegates vision and focuses on orchestration.
- 800 tests passing, 0 failures.

**Files created:** `src/tools/image_analyzer.py`
**Files modified:** `src/core/user_model.py`, `src/core/interesting_findings.py`, `src/core/user_preferences.py`, `src/core/idea_history.py`, `src/tools/tool_registry.py`, `src/core/idea_generator.py`, `src/core/dream_cycle.py`, `src/core/goal_worker_pool.py`, `src/interfaces/discord_bot.py`, `src/interfaces/message_handler.py`, `src/interfaces/action_dispatcher.py`, `src/service/archi_service.py`, `src/tools/computer_use.py`

</details>

<details>
<summary>Session 74 (Cowork) — Test coverage expansion (138 new tests, 800 total)</summary>

- [x] **Test coverage gaps closed** — Added 5 new unit test files covering the remaining untested modules:
  - `test_plan_executor_actions.py` (52 tests) — Action routing/dispatch, aliasing (research→web_search), source write denial gate, read-before-edit enforcement, web_search/fetch_webpage, create/append/read/list files, run_command allowlist+blocklist, ask_user deferral detection, run_python, edit_file find/replace with multi-match guard, write_source approval gate
  - `test_integrator.py` (22 tests) — Helper functions, single/multi-task integration with mocked router, fallback summary, evidence building, file reading caps, discovery brief injection, invalid response handling
  - `test_critic.py` (20 tests) — Severity parsing, remediation task extraction (capped at 2, only for "significant"), user model context injection, truncation, edge cases
  - `test_autonomous_executor.py` (19 tests) — Budget loading from rules.yaml, project path resolution (name/focus area/exception handling), task queue processing, follow-up task extraction gating
  - `test_memory_manager.py` (25 tests) — Short-term deque, SQLite persistence, JSON content/metadata, maxlen enforcement, vector store delegation, stats, graceful degradation
- Still missing: `mcp_client`, `vector_store` (lower priority — depend on external services)

</details>

<details>
<summary>Session 73 (Cowork) — plan_executor.py SRP refactor (1/5 🟡 architecture items resolved)</summary>

- [x] **`plan_executor.py` SRP split** — Split 2368-line monolith into `src/core/plan_executor/` package with 5 focused submodules:
  - `executor.py` (~580 lines) — PlanExecutor class, core execution loop, prompt building, self-verification, progress reporting
  - `actions.py` (~530 lines) — ActionMixin with all action handlers (_do_web_search, _do_create_file, _do_write_source, _do_edit_file, _do_run_command, _do_run_python, _do_ask_user, etc.)
  - `safety.py` (~290 lines) — Safety config loading (lazy, cached, thread-safe), path resolution (workspace/project boundary), protected file checks, approval gates, backup/syntax check, error classification
  - `recovery.py` (~170 lines) — Task cancellation signals (single-shot + sticky shutdown), crash recovery state persistence/load/clear
  - `web.py` (~80 lines) — SSL context (certifi), URL opener (keep-alive), _fetch_url_text (HTML→text), SSRF guard
  - `__init__.py` (~100 lines) — Re-exports all public symbols for full backward compatibility. Custom module `__setattr__` proxies `_safety_config_cache` writes to safety submodule for test compat.
- Updated `config/rules.yaml` protected_files for new package structure (6 entries instead of 1)
- Updated `tests/unit/test_command_safety.py` to expect new protected path names
- 662 tests passing, 0 failures

**Files created:** `src/core/plan_executor/__init__.py`, `executor.py`, `actions.py`, `safety.py`, `recovery.py`, `web.py`
**Files deleted:** `src/core/plan_executor.py` (replaced by package)
**Files modified:** `config/rules.yaml`, `tests/unit/test_command_safety.py`

</details>

<details>
<summary>Session 72 (Cowork) — Security & critical path test coverage (109 new tests)</summary>

- [x] **6 new test files** covering all previously-untested security paths:
  - `test_path_traversal.py` (18 tests) — SafetyController.validate_path(), symlink attacks, .. traversal, authorize() path isolation
  - `test_command_safety.py` (17 tests) — allowlist/blocklist loading, command parsing (shlex, .exe strip), protected paths, config caching
  - `test_write_path_validation.py` (19 tests) — workspace/data boundary, traversal, symlink escape, FileWriteTool integration
  - `test_qa_evaluator.py` (22 tests) — _deterministic_checks, evaluate_task verdict logic, evaluate_goal conformance
  - `test_net_safety.py` (16 tests) — SSRF protection, DNS rebinding, cloud metadata IP blocking
  - `test_goal_worker_pool.py` (17 tests) — budget loading, max_workers cap, per-goal cancel, shutdown flags, worker state
- 662 tests passing (was 553)

</details>

<details>
<summary>Session 71 (Cowork) — Architecture & code quality quick wins (9/15 🟡 items resolved)</summary>

- [x] **Inconsistent base_path in vector_store.py** — Now uses canonical `base_path()` from `src.utils.paths` instead of ad-hoc `os.environ.get("ARCHI_ROOT", os.getcwd())`.
- [x] **ImageGenerator instantiated fresh every call** — Added class-level lazy-init-once with `threading.Lock` in `_ImageGenTool` (same pattern as Desktop/Browser tools).
- [x] **Cross-module private attribute access** — Added `DreamCycle.clear_suggest_cooldown()` public method, passed as callback to `GoalWorkerPool`. Eliminated 3-layer private access through `discord_bot._dream_cycle._last_suggest_time`.
- [x] **`_is_goal_cancelled()` helper** — Added `_is_cancelled(goal_stop)` method to `GoalWorkerPool`, replaced inline pattern.
- [x] **Extract `_is_private_url()` to shared utility** — Created `src/utils/net_safety.py` with `is_private_url()`. `plan_executor._is_private_url()` now delegates to shared utility.
- [x] **Remove `echo` from allowed_commands** — Removed from `config/rules.yaml` to eliminate env var exfiltration vector.
- [x] **Inconsistent exception handling** — Added `logger.debug()` to 7 silent `except` blocks across `qa_evaluator.py`, `critic.py`, `integrator.py`, `discovery.py`. Bumped learning failure in `autonomous_executor.py` from `debug` to `warning`.
- [x] **Model pricing hardcoded** — Evaluated and kept as-is (acceptable tradeoff: simple, clear, zero overhead, pricing changes infrequently).
- [x] **Duplicate `_ImageGenTool` item** — Same fix as ImageGenerator item above.
- 553 tests passing, 0 failures.

**Files modified:** `src/memory/vector_store.py`, `src/tools/tool_registry.py`, `src/core/goal_worker_pool.py`, `src/core/dream_cycle.py`, `src/core/plan_executor.py`, `src/core/qa_evaluator.py`, `src/core/critic.py`, `src/core/integrator.py`, `src/core/discovery.py`, `src/core/autonomous_executor.py`, `config/rules.yaml`
**Files created:** `src/utils/net_safety.py`

</details>

<details>
<summary>Session 69 (Cowork) — Performance & unbounded growth fixes (8/8 🟡 items)</summary>

- [x] **`dream_history` capped at 500** — `_MAX_DREAM_HISTORY` constant; trims after each append. Older entries already in `dream_log.jsonl`.
- [x] **`learning_system.py` experiences capped at 500** — `_MAX_EXPERIENCES` constant; trims after flush and on load from disk.
- [x] **`_worker_states` cleanup** — `_cleanup_stale_states()` removes DONE states >1 hour old; called after each goal finishes.
- [x] **MCP idle monitor race fix** — `_lifecycle_lock` (asyncio.Lock) serialises `call_tool()` connection setup and idle monitor teardown. Double-check after acquire prevents stale teardown.
- [x] **`_fetch_url_text()` connection reuse** — Module-level `_url_opener` via `build_opener(HTTPSHandler)` gives HTTP keep-alive.
- [x] **Discovery file cache** — `_file_list_cache` with 60s TTL avoids repeated `os.walk()` for same project root.
- [x] **Cost tracker atomic writes** — `_save_usage()` now writes to `.tmp` then `os.replace()`. Existing `threading.Lock` already covered concurrency.
- [x] **Agent loop tool timeout** — Tool execution dispatched to `ThreadPoolExecutor` with 30s timeout; can't stall heartbeat.

</details>

<details>
<summary>Session 68 (Cowork) — Code review logic & correctness fixes (14/14 🟡 items)</summary>

- [x] **Consolidate `extract_json()`** — Removed duplicate from `text_cleaning.py`, canonical version in `parsing.py`. Updated `conversational_router.py` import.
- [x] **Public router provider property** — Added `@property provider` to `ModelRouter`, replaced `router._api._provider` access in `agent_loop.py`.
- [x] **`datetime.utcnow()` → `datetime.now(timezone.utc)`** — Fixed in `agent_loop.py` (1) and `logger.py` (3 occurrences).
- [x] **Remove racing `stop_flag.clear()`** — Removed from `dream_cycle.py` finally block; `stop_monitoring()` owns the flag.
- [x] **Thread-safe goal iteration** — `_has_pending_work()` snapshots `goals.values()` via `list()`.
- [x] **Lazy asyncio.Lock in MCP** — `ServerConnection._lock` defaults to `None`, lazy-init in `_ensure_connection()`.
- [x] **Timezone-aware night mode** — `heartbeat.py` uses `get_user_hour()` from `time_awareness.py`.
- [x] **Thread-safe timestamps.py** — Added `threading.Lock` + `timeout=15.0` to sqlite3 connection.
- [x] **Document read-only safety bypass** — Added design decision comment to `_READ_ONLY_ACTIONS` in `safety_controller.py`.
- [x] **Fix unclosed Image.open()** — Wrapped with context manager in `computer_use.py`.
- [x] **Consecutive failure backoff** — `autonomous_executor.py` breaks after 3 consecutive failures with 1s sleep between.
- [x] **Clean MCP loop shutdown** — `shutdown_mcp()` stops event loop and joins thread with 5s timeout.
- [x] **Structured message parsing** — `_extract_user_query` prefers structured `messages` list over string parsing in `router.py`.
- 553 tests passing, 0 failures.

**Files modified:** `src/core/agent_loop.py`, `src/core/logger.py`, `src/core/heartbeat.py`, `src/core/dream_cycle.py`, `src/core/autonomous_executor.py`, `src/core/safety_controller.py`, `src/core/conversational_router.py`, `src/models/router.py`, `src/tools/computer_use.py`, `src/tools/mcp_client.py`, `src/tools/tool_registry.py`, `src/utils/text_cleaning.py`, `src/maintenance/timestamps.py`

</details>

<details>
<summary>Session 66 (Cowork) — Code review critical fixes (5/5 🔴 items)</summary>

- [x] **Switch run_command to allowlist** — Two-layer safety: shlex.split() + allowlist (configurable in rules.yaml) as primary, blocklist as defense-in-depth.
- [x] **Per-goal cancellation** — Per-goal stop flags (Dict[str, Event]) in GoalWorkerPool. cancel_goal() sets flag, shutdown() sets all. Passed through to TaskOrchestrator.
- [x] **Lazy safety config** — Replaced import-time _load_safety_config() with lazy _get_safety(key) accessor. Thread-safe double-checked locking.
- [x] **Lazy DesktopControl/BrowserControl** — Class-level lazy init with double-checked locking. No import at registration time. Graceful error on headless systems.
- [x] **MemoryManager DB INSERT** — Added SQL INSERT into working_memory table in store_action(). Working memory now actually persists.
- [x] **Blocklist gaps** — Addressed by allowlist fix (curl, wget, bash, cmd, powershell all rejected by allowlist before blocklist runs).
- Added 3 new improvement items discovered during fixes.
- 553 tests passing, 0 failures.

</details>

<details>
<summary>Session 64 (Cowork) — Shutdown reliability, dream cycle ordering, testing speed-up</summary>

- [x] **Speed up dream cycle testing** — Reduced idle_threshold 300→60s, check_interval 30→10s, suggest_cooldown_base 600→120s for faster iteration during development.
- [x] **Fix dream cycle suggesting during active work** — Added `goal_worker_pool.is_working()` check to `_should_run_cycle()` to prevent new suggestions while workers are executing goals.
- [x] **Fix CancelledError in goal_worker_pool** — `_on_goal_done` callback now checks `future.cancelled()` before accessing `future.exception()`.
- [x] **Clean shutdown via transport close** — Root cause of zombie processes: worker threads blocked on httpx API calls can't be interrupted, Python's atexit handler joins all executor threads. Fix: `router.close()` closes httpx transports, immediately failing in-flight requests. `_closed` flag short-circuits retries. Worker pool uses `wait=True` since threads unblock quickly. No `os._exit()` needed.
- [x] **Ask-first proactive ordering** — Dream cycle now asks user for work first, only tries proactive initiative after suggestions go unanswered.
- [x] **Notification grounding constraint** — Added persona instruction to only reference information actually provided, preventing Grok from hallucinating "based on our chats" context.
- [x] **QueueHandler logging** — (Carried from session 63) Replaced root logger console handler with QueueHandler+QueueListener to prevent Windows console I/O from blocking worker threads.
- [x] **Documentation update** — Updated ARCHITECTURE.md (shutdown flow, dream cycle ordering, timing config, notification system, test count 553).

**Files modified:** `config/heartbeat.yaml`, `src/core/dream_cycle.py`, `src/core/goal_worker_pool.py`, `src/service/archi_service.py`, `src/interfaces/discord_bot.py`, `src/core/notification_formatter.py`, `src/models/openrouter_client.py`, `src/models/router.py`, `claude/ARCHITECTURE.md`

</details>

<details>
<summary>Session 63 (Cowork) — Idea history, adaptive cooldown, multi-pick suggestions, cold-start fixes</summary>

- [x] **Idea History + Retry-with-Feedback** — Persistent idea ledger (`data/idea_history.json`) tracking every suggestion with outcomes. Filters consult history before presenting ideas. Retry-with-feedback loop when all ideas rejected. Up to 2 retries before Claude escalation.
- [x] **Adaptive suggestion cooldown** — Exponential backoff when user doesn't respond: base * 2^unanswered_count, up to 4-hour cap. User message resets cooldown.
- [x] **Multi-pick support** — Users can approve multiple suggestions ("do 1 and 3", "all of them").
- [x] **Cold-start filter bypass** — When no active projects or interests exist, skip relevance/purpose filters so ideas can still flow.
- [x] **Richer suggestion descriptions** — Include `reasoning` field so user understands what each idea does and why.
- [x] **Router misclassification fix** — "Tell me more about #2" no longer misclassified as suggestion acceptance.
- [x] **Shutdown cascade prevention** — Cancelled tasks no longer trigger QA→Claude→retry→cancel chains during shutdown.
- [x] **File creation restriction** — `create_file`/`append_file` restricted to `workspace/` and `data/` directories.
- [x] **QueueHandler logging** — Non-blocking logging via QueueHandler+QueueListener to prevent Windows console blocking.

**Files created:** `src/core/idea_history.py`, `tests/unit/test_idea_history.py`
**Files modified:** `src/core/idea_generator.py`, `src/core/dream_cycle.py`, `src/interfaces/discord_bot.py`, `src/models/cache.py`, `src/core/conversational_router.py`, `src/core/goal_worker_pool.py`, `scripts/reset.py`, `src/core/notification_formatter.py`, `src/core/autonomous_executor.py`, `src/core/task_orchestrator.py`, `src/tools/tool_registry.py`, `src/service/archi_service.py`

</details>

<details>
<summary>Session 62 (Cowork) — Tiered model routing: Claude Sonnet 4.6 escalation on failure</summary>

- [x] **Tiered model routing** — Added Claude Sonnet 4.6 via OpenRouter as automatic escalation tier. Two trigger points: (1) QA rejection retry runs entirely on Claude with full prior attempt context. (2) Schema retry exhaustion makes one final Claude attempt. Context manager `router.escalate_for_task()` snapshots/restores model state. Prior attempt summary (key actions + files created) injected as hints so Claude doesn't redo work. Hints-per-step increased from 2→5.
- [x] **Claude context gap analysis** — Traced both escalation paths to verify Claude gets full context. Schema path: full step prompt (good). QA retry path: had gaps — Claude started with empty step history and only 2 hints visible. Fixed both.
- [x] **Documentation update** — Updated ARCHITECTURE.md (tiered routing section, QA section, deferred systems table, design decisions, testing count), SESSION_CONTEXT.md (session 62 summary), TODO.md.

**Files created:** `tests/unit/test_tiered_escalation.py`
**Files modified:** `src/models/router.py`, `src/models/providers.py`, `src/core/autonomous_executor.py`, `src/core/plan_executor.py`

</details>

<details>
<summary>Session 61 (Cowork) — User preferences → project context + conversation long-term memory</summary>

- [x] **Wire user_preferences into project_context** — New `src/utils/project_sync.py` (~60 lines). Hooks into Conversational Router after `extract_user_signals()`. Keyword-matches preference signals against active project names + intent phrases (deactivate: "done with", "not doing"; boost: "focus on", "prioritize"; new interest: "interested in", "want to try"). Updates `project_context.json` atomically via existing `save()`. No extra model call. 16 unit tests.
- [x] **Store conversation context in long-term memory** — Two write paths: (1) Notable chat messages stored to vector store when Router extracts user signals (notability filter). Shared MemoryManager injected via new `message_handler.set_memory()`, called from `archi_service.py` after memory init. (2) Dream cycle synthesis insights stored after synthesis logging with type="dream_summary".
- [x] **TODO cleanup** — Marked "test opportunity scanner live" (validated through live use across sessions 45/59/60) and "review architecture for better approaches" (completed across 9-phase evolution + session 58 audit) as done. Promoted 4 future ideas to open items.

**Files created:** `src/utils/project_sync.py`, `tests/unit/test_project_sync.py`
**Files modified:** `src/core/conversational_router.py`, `src/interfaces/message_handler.py`, `src/service/archi_service.py`, `src/core/dream_cycle.py`

</details>

<details>
<summary>Session 60 (Cowork) — Artifact reuse, project explanations, deferred replies</summary>

- [x] **Artifact reuse** — FileTracker stores `goal_description` in manifest, has `get_files_by_keywords()` for keyword search. autonomous_executor queries file tracker + scans project directory before each task, injecting file awareness hints. Fixed ask_user step history to show actual reply text.
- [x] **Project explanations** — Initiative announcements now pass `reasoning`, `user_value`, and `source` from opportunity scanner through to the notification formatter. LLM prompt updated to explain project context.
- [x] **Deferred replies** — Temporal signal detection in ask_user returns `{"deferred": True}`. Task.deferred_until field with serialization. Task parking in autonomous_executor. get_ready_tasks() filters deferred tasks until resume time.

**Files modified:** `src/core/file_tracker.py`, `src/core/autonomous_executor.py`, `src/core/plan_executor.py`, `src/core/dream_cycle.py`, `src/core/notification_formatter.py`, `src/core/goal_manager.py`

</details>

<details>
<summary>Session 58 (Cowork) — Verification Patch-Up</summary>

- [x] **File Security: blacklist → whitelist** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) `_validate_path_security()` in `tool_registry.py` changed from blocking known system directories to whitelist approach: resolves canonical path via `os.path.realpath()` and verifies it starts with `paths.base_path()`. Rejects everything else. ~10 lines changed.
- [x] **DAG priority preemption** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) Added dedicated `_reactive_executor` (1 thread) to `GoalWorkerPool` for user-requested goals. Proactive goals use the main executor. Reactive goals start immediately without waiting for background work to release a slot. Shutdown updated to clean up both executors.
- [x] **User Model → Notification Formatter** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) Added `get_context_for_formatter()` to `user_model.py` (returns style notes and preferences). Injected into `_call_formatter()` in `notification_formatter.py` so all notifications adapt to Jesse's communication style.
- [x] **User Model → Discovery** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) Added `get_context_for_discovery()` to `user_model.py` (returns project preferences and patterns). Wired into `_rank_files()` in `discovery.py` — user preference keywords boost file relevance scores.
- [x] **Response builder prefix documented** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) Grepped codebase: `build_response()` with `action_prefix` is only called from `message_handler.py` (3 paths: multi_step, coding, non-chat actions). No non-Discord callers. Updated docstring to document why prefix logic is retained and which callers use it.
- [x] **Integrator glue surfacing** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) `integrate_goal()` now surfaces `missing_glue` in its return dict instead of silently discarding it. Added logging for detected glue items. Auto-creation evaluated and deferred — detection + reporting is sufficient since workers handle file creation.
- [x] **Step cap spec alignment** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) Spec originally said 30. Implementation uses 50/25/12 — deliberate choice from session 32 (budget and time caps are primary constraints). Updated `ARCHITECTURE_PROPOSAL.md` to document tiered caps.
- [x] **Zero new test failures** — 490 pass, same pre-existing failures. All changes verified.

**Files modified:** `src/tools/tool_registry.py`, `src/core/goal_worker_pool.py`, `src/core/task_orchestrator.py` (docstring), `src/core/user_model.py`, `src/core/notification_formatter.py`, `src/core/discovery.py`, `src/interfaces/response_builder.py` (docstring), `src/core/integrator.py`, `claude/ARCHITECTURE_PROPOSAL.md`, `claude/VERIFICATION_REPORT.md`, `claude/SESSION_CONTEXT.md`, `claude/TODO.md`

</details>

<details>
<summary>Session 57 (Cowork) — Architecture Evolution Phase 9: Cleanup</summary>

- [x] **Heartbeat simplified to 2-tier** — Removed deep sleep tier (monitoring_mode), evening multiplier, `_time_of_day_multiplier()`, and work_hours/evening config sections. Two tiers remain: command (10s for 2 min after user interaction) and idle (60s). Night mode override (1800s) preserved. `heartbeat.yaml` simplified from ~40 lines to ~25. `heartbeat.py` rewritten from ~160 lines to ~110.
- [x] **Intent classifier legacy model-classify removed** — Removed `_model_classify()` function (~50 lines) and `_INTENT_INSTRUCTION` prompt string (~20 lines). These were superseded by the Conversational Router (Phase 4) but kept as fallback. `classify()` now returns `IntentResult(action="chat_fallback")` for non-fast-path messages instead of making a redundant model call. All fast-paths (datetime, greeting, screenshot, image gen, deferred requests, slash commands) preserved unchanged. Two tests removed (`TestScreenshotInIntentClassifier` — tested removed `_INTENT_INSTRUCTION`).
- [x] **Discord stub comments removed** — Three Phase 4 "Removed in Phase 4" breadcrumb comments deleted from `discord_bot.py` (~lines 773, 784, 987).
- [x] **Router legacy params removed** — `force_api` and `use_reasoning` parameters removed from `ModelRouter.generate()` signature. One call site updated (`message_handler.py` line 285). Stale `_temp_previous` comment fixed (`force_api` → `force_api_override`).
- [x] **`_force_aborted` renamed to `_schema_retries_exhausted`** — Renamed throughout `plan_executor.py` (5 references) and `qa_evaluator.py` (1 reference). Result dict key changed from `"force_aborted"` to `"schema_retries_exhausted"`. Stale "loop detection, think-loop" comment updated to accurately describe the current trigger (JSON schema retries exhausted).
- [x] **Dead code sweep** — Removed 7 unused imports: `re` from `message_handler.py`, `time` and `List` from `router.py`, `Path` from `dream_cycle.py`, `os`/`queue`/`field` from `goal_worker_pool.py`.
- [x] **Zero new test failures** — 475 pass, 11 fail (same pre-existing failures). 2 tests removed (tested deleted code). All spot-checks pass.

**Files modified:** `src/core/heartbeat.py`, `config/heartbeat.yaml`, `src/interfaces/intent_classifier.py`, `src/interfaces/discord_bot.py`, `src/models/router.py`, `src/interfaces/message_handler.py`, `src/core/plan_executor.py`, `src/core/qa_evaluator.py`, `src/core/dream_cycle.py`, `src/core/goal_worker_pool.py`, `tests/unit/test_screenshot.py`

</details>

<details>
<summary>Session 56 (Cowork) — Architecture Evolution Phase 8: Graceful Degradation</summary>

- [x] **Provider Fallback Chain** (`src/models/fallback.py`, ~230 lines) — When the primary provider fails, cascades through a priority-ordered chain of backup providers. Default chain: xai → openrouter → deepseek → openai → anthropic → mistral (only providers with API keys in .env are included). Per-provider CircuitBreaker instances (3 consecutive failures → circuit OPEN). Exponential backoff on recovery attempts: 30s → 60s → 120s → 300s cap. Auto-recovery: successful call resets breaker and restores primary. Thread-safe with threading.Lock. State-change callback fires on degradation events (degraded, recovered, total_outage). Manual reset via `reset_provider()`.
- [x] **Router integration** — `_use_api()` in `router.py` now routes through the fallback chain via `call_with_fallback()`. If user has force-overridden to a specific model, tries that first then falls through to chain. On total outage (all providers down), checks cache as last resort (returns cached response with `degraded: True` flag). If nothing works, returns friendly user-facing error message. New helper `_record_success()` consolidates stats tracking. New methods: `get_provider_health()`, `is_degraded()`, `all_providers_down()`, `_get_or_create_client()`.
- [x] **Degraded mode visibility** — Discord "what model"/"status"/"api status" now shows provider health with icons: 🟢 closed (healthy), 🔴 open (down), 🟡 half_open (testing recovery). Shows "⚠️ Running in degraded mode" when operating on a non-primary provider. Default degradation handler sends Discord notifications (⚠️ degraded, ✅ recovered, 🔴 total outage) via `send_notification()`.
- [x] **Dream cycle pause** — `_should_run_cycle()` checks `_all_providers_down()` before running. When all providers are down, skips the cycle entirely (logs "All LLM providers down — skipping dream cycle"). `get_status()` now includes `all_providers_down` field for monitoring. Prevents burning budget on retries during outages.
- [x] **Zero new test regressions** — 477/488 tests pass (same 11 pre-existing failures). All imports verified clean.

**Files created:** `src/models/fallback.py`
**Files modified:** `src/models/router.py`, `src/interfaces/discord_bot.py`, `src/core/dream_cycle.py`

</details>

<details>
<summary>Session 55 (Cowork) — Architecture Evolution Phase 7: MCP Tool Integration</summary>

- [x] **MCP Client** (`src/tools/mcp_client.py`, ~320 lines) — Async client that connects to MCP servers via stdio transport. Manages server lifecycle: start on first use, stop after configurable idle timeout. Background event loop in dedicated daemon thread bridges sync callers (PlanExecutor) to the async MCP SDK. `MCPClientManager` handles multiple concurrent servers, tool discovery via `list_all_tools()`, and tool execution via `call_tool()`. Graceful cleanup catches `CancelledError` from anyio task groups.
- [x] **Local MCP Server** (`src/tools/local_mcp_server.py`, ~230 lines) — Wraps Archi's existing tools as a FastMCP server using `@mcp.tool()` decorators. Exposes: `read_file`, `create_file`, `list_files`, `web_search`, `desktop_click/type/screenshot/hotkey/open`, `browser_navigate/click/fill/screenshot/get_text`, `desktop_click_element`. Each tool returns JSON-serialized results matching the existing `{success: bool, ...}` contract. Image generation excluded (privacy — NSFW prompts stay local). Runs via `python -m src.tools.local_mcp_server` with stdio transport.
- [x] **Tool Registry refactor** (`src/tools/tool_registry.py`) — MCP-aware execution layer. `initialize_mcp()` loads `config/mcp_servers.yaml`, starts configured servers, and discovers tools. `execute()` routing: (1) direct-only tools (generate_image) always direct, (2) MCP-backed tools route through MCP client, (3) direct tools as fallback, (4) MCP-only tools (no direct equivalent). Background event loop via `_get_mcp_loop()` in daemon thread — `_run_async()` bridges sync→async with 60s timeout. `shutdown_mcp()` for clean teardown.
- [x] **MCP Server Config** (`config/mcp_servers.yaml`) — Declarative config for MCP servers: `command`, `args`, `env` (with `${VAR}` resolution from os.environ), `idle_timeout`, `enabled`, `exclude_tools`. Local server (always-on) and GitHub server (5min idle timeout) configured. Adding a new MCP server = adding a config entry.
- [x] **GitHub MCP server** — `@modelcontextprotocol/server-github` wired as first external server. Provides 20+ tools: list_issues, get_issue, create_issue, create_pull_request, get_file_contents, search_code, etc. Requires `GITHUB_PERSONAL_ACCESS_TOKEN` in `.env`.
- [x] **PlanExecutor MCP fallback** — `_execute_action()` now routes unknown actions to `self.tools.execute()` instead of returning "Unknown action" error. Enables automatic support for any MCP-provided tool without explicit action handlers.
- [x] **Safety updates** — GitHub read-only tools (list_issues, get_file_contents, etc.) added to L1_LOW in `rules.yaml`. GitHub write tools (create_issue, create_pull_request, etc.) added to L2_MEDIUM. `config/mcp_servers.yaml` added to protected files list.
- [x] **End-to-end verified** — 41 tools discovered from 2 MCP servers (local + GitHub). `read_file` call through MCP bridge succeeded. 477/488 tests pass (11 pre-existing failures unrelated to MCP).

**Files created:** `src/tools/mcp_client.py`, `src/tools/local_mcp_server.py`, `config/mcp_servers.yaml`
**Files modified:** `src/tools/tool_registry.py`, `src/core/plan_executor.py`, `src/core/agent_loop.py`, `config/rules.yaml`, `.env.example`, `requirements.txt`

</details>

<details>
<summary>Session 53 (Cowork) — Architecture Evolution Phase 5: Planning + Scheduling</summary>

- [x] **Discovery Phase** (`src/core/discovery.py`, ~280 lines) — Scans project files before the Architect runs. Matches goal keywords to active projects in project_context.json, enumerates files (skipping .git, __pycache__, etc.), ranks by relevance (entry points, READMEs, keyword matches, recency), reads selectively (Python structure extraction for code files, full content for docs), compresses into a structured brief via one model call. Deterministic fallback if model fails. Feeds into decompose_goal().
- [x] **Architect Enhancement** — Rewrote decompose_goal() in `goal_manager.py`. Now accepts `discovery_brief` (from Discovery) and `user_prefs` (from User Model). Prompt upgraded to "You are the Architect" — requires concrete specs per task: `files_to_create`, `inputs`, `expected_output`, `interfaces`. Task class extended with these 4 fields, serialized/deserialized in to_dict/_load_state. max_tokens raised from 1000→1500 for richer specs.
- [x] **Event-Driven DAG Scheduler** — Full rewrite of `task_orchestrator.py`. Replaced wave-based batching (gather all ready → run wave → gather again) with persistent ThreadPoolExecutor + as_completed() event loop. When a task finishes, immediately checks which pending tasks are now unblocked and submits them — no waiting for wave boundaries. Stops on 3 consecutive failures (more nuanced than "all tasks in wave failed"). _submit_ready_tasks() respects max_parallel slots and snapshots sibling context at submission time.
- [x] **Request Prioritization** — `submit_goal()` and `kick()` accept `reactive: bool` parameter. All user-initiated call sites (action_dispatcher.py, message_handler.py deferred requests, message_handler.py auto-escalation, discord_bot.py suggestion picks) pass `reactive=True`. Dream cycle self-initiatives stay `reactive=False` (default). Reactive flag logged and tracked in GoalWorkerState for monitoring.
- [x] **Spec hints in PlanExecutor** — `autonomous_executor.py` now injects Architect spec fields (files_to_create, inputs, expected_output, interfaces) as hints into PlanExecutor, so workers execute against concrete specs instead of discovering what to build mid-execution.
- [x] **Discovery wiring** — `goal_worker_pool._execute_goal()` runs Discovery→User Model→Architect pipeline before the DAG scheduler. Discovery cost tracked in GoalWorkerState.

**Files created:** `src/core/discovery.py`
**Files modified:** `src/core/goal_manager.py`, `src/core/task_orchestrator.py`, `src/core/goal_worker_pool.py`, `src/core/autonomous_executor.py`, `src/core/dream_cycle.py`, `src/interfaces/action_dispatcher.py`, `src/interfaces/message_handler.py`, `src/interfaces/discord_bot.py`

</details>

<details>
<summary>Session 50 (Cowork) — Architecture Evolution Phase 3: Notifications + Feedback</summary>

- [x] **Notification Formatter** (`src/core/notification_formatter.py`, ~370 lines) — Single model call per notification via Grok 4.1 Fast (~$0.0002/call). Takes structured data (event type, results, stats) and produces a conversational message matching Archi's persona (warm, concise, varied phrasing, no markdown/bullet formatting). Every notification type has a deterministic fallback string so notifications still send if the model call fails.
- [x] **Migrated all notification paths** — Goal completion, morning report, hourly summary, work suggestions, idle prompt, finding notifications, initiative announcements, interrupted task recovery, and decomposition failure messages all route through the formatter. Removed ~100 lines of hardcoded template strings from `reporting.py`, `goal_worker_pool.py`, and `dream_cycle.py`.
- [x] **Reaction-based feedback** — Added `on_raw_reaction_add` handler to Discord bot. Tracked notification messages (stored by message ID) detect 👍/👎/❤️/🎉/🔥/😕/😞 reactions from Jesse and record them as learning feedback via `learning_system.record_feedback()`. `send_notification()` now accepts optional `track_context` dict to register messages for tracking. Added `dm_reactions` and `reactions` intents.
- [x] **Significant goal feedback prompt** — Goals with 3+ tasks or 10+ minutes of work append "Anything you'd change?" to completion messages, prompting Jesse to provide directed feedback.
- [x] **Router passthrough** — `send_morning_report()`, `send_hourly_summary()`, and `send_finding_notification()` now accept an optional `router` parameter. Dream cycle passes its router to these calls so the formatter can make model calls.

**Files created:** `src/core/notification_formatter.py`
**Files modified:** `src/interfaces/discord_bot.py`, `src/core/goal_worker_pool.py`, `src/core/reporting.py`, `src/core/dream_cycle.py`

</details>

<details>
<summary>Session 49 (Cowork) — Architecture Evolution Phase 2: QA + Critic</summary>

- [x] **QA Evaluator** (`src/core/qa_evaluator.py`, ~200 lines) — Post-task quality gate. Layer 1: deterministic checks (files exist, Python syntax valid, not empty/truncated, done summary present). Layer 2: model-based semantic evaluation (does output actually accomplish the task?). Returns accept/reject/fail. On rejection, task retries once with QA feedback injected as hints so the model knows exactly what to fix.
- [x] **Critic** (`src/core/critic.py`, ~150 lines) — Adversarial per-goal evaluation after all tasks complete. Prompts model to find real problems: output that just looks busy, code that won't run, wrong approach, things Jesse wouldn't use. Severity levels: none, minor (logged only), significant (adds up to 2 remediation tasks and re-runs orchestrator for fix-up pass).
- [x] **QA wiring** — Inserted into `autonomous_executor.execute_task()` after PlanExecutor returns but before learning system recording. On QA rejection, creates new PlanExecutor with QA feedback as hints, retries task once. On QA fail, marks task as failure. QA cost tracked in total task cost.
- [x] **Critic wiring** — Inserted into `goal_worker_pool._execute_goal()` after orchestrator finishes but before notification. Gathers all task results and files for the goal, runs critique. On significant severity, adds remediation tasks and re-runs orchestrator within remaining budget.
- [x] **Loop detection removal** — Removed ~120 lines from PlanExecutor: consecutive action key tracking (`_recent_action_keys`, `_WARN_AT`/`_STRONG_WARN_AT`/`_KILL_AT`), rewrite-loop tracking (`_path_write_counts`, `_PATH_WRITE_WARN`/`_PATH_WRITE_STRONG`/`_PATH_WRITE_KILL`), all warning injection logic, and both force-abort code paths. Kept hard step cap of 50 and `_force_aborted` flag (now only set on JSON retry exhaustion). Mechanical error recovery hints now stored in step record's `error_hint` field and injected by `_build_step_prompt`.

**Files created:** `src/core/qa_evaluator.py`, `src/core/critic.py`
**Files modified:** `src/core/autonomous_executor.py`, `src/core/goal_worker_pool.py`, `src/core/plan_executor.py`

</details>

<details>
<summary>Session 48 (Cowork) — Architecture Evolution Phase 1: PlanExecutor Internals + Security</summary>

- [x] **Context Compression** — After step 8, older steps are compressed to one-liners (action + outcome only, no snippets). Most recent 5 steps retain full fidelity. Saves ~200-500 tokens per compressed step, preventing prompt bloat on long tasks. New `_compress_step()` static method.
- [x] **Structured Output Contracts** — New `src/core/output_schemas.py` with `ACTION_SCHEMAS` dict mapping every action to required fields+types and `validate_action()` function. Wired into PlanExecutor step handler: validates model JSON before dispatch, auto re-prompts with targeted schema error messages, caps at 2 retries (up from 1).
- [x] **Mechanical Error Recovery** — New `_classify_error()` function classifies action failures as transient (network/timeout → retry with 2s backoff, no step burned), mechanical (file not found, syntax error → targeted fix hint injected into next prompt), or permanent (protected file, blocked command → fail immediately). ~60 lines, rule-based, no model call.
- [x] **Reflection Prompt** — Added self-check checklist to the "done" action description in the step prompt: re-read task, verify files exist with correct content, test code, check for gaps. Model must confirm all checks pass before calling done.
- [x] **File Security Hardening** — Added `os.path.realpath()` symlink resolution to `_resolve_workspace_path()` and `_resolve_project_path()` (boundary check now uses resolved real paths). Added `_validate_path_security()` in `tool_registry.py` blocking system directories (/etc/, /usr/, C:\Windows, etc.) with defense-in-depth checks on FileReadTool and FileWriteTool.

**Files created:** `src/core/output_schemas.py`
**Files modified:** `src/core/plan_executor.py`, `src/tools/tool_registry.py`

</details>

<details>
<summary>Session 47 (Cowork) — Fix _step_history crash, suggestion pick, doubled responses, descriptive notifications</summary>

- [x] **Fixed PlanExecutor _step_history crash** — `self._step_history` was never initialized, causing `AttributeError` on step 1 of every task. Added `self._step_history = steps_taken` after crash recovery block.
- [x] **Fixed suggestion pick affirmative routing (two rounds)** — First: added exact-match affirmatives with 40-char limit. Second: added substring matching for longer messages ("I have no idea what that is, but go ahead I guess") — checks for phrases like "go ahead", "do it", "sounds good" anywhere in the message.
- [x] **Fixed doubled create_goal response** — Model prefix + hardcoded response produced doubled messages. Skip prefix for `create_goal` and `generate_image` actions.
- [x] **Made goal notifications descriptive** — Completion messages now show task "Done:" summaries (what was built, how to use it) instead of bare filenames. Falls back to filenames if no summaries available. Updated PlanExecutor "done" prompt to instruct Grok to explain files and usage.
- [x] **Third pass on Discord message tone** — Removed remaining emoji prefixes, bold markdown, verbose echoes from all notification paths.

**Files modified:** `src/core/plan_executor.py`, `src/interfaces/discord_bot.py`, `src/interfaces/action_dispatcher.py`, `src/interfaces/message_handler.py`, `src/core/dream_cycle.py`, `src/core/goal_worker_pool.py`, `src/core/reporting.py`

</details>

<details>
<summary>Session 46 (Cowork) — Discord message spam reduction & conversational tone</summary>

- [x] **Consolidated goal notifications** — Replaced per-task notifications in `goal_worker_pool.py` with a single `_notify_goal_result()` that sends ONE message per goal covering successes, failures, and budget status. Removed separate failure/budget-pause messages.
- [x] **Removed per-task spam from autonomous_executor** — Removed per-task `❌ Task failed` notifications and per-goal completion notifications (now handled by goal_worker_pool).
- [x] **Rewrote reporting.py** — Morning report: natural greeting summarizing the night instead of rigid template. Hourly summary: conversational headline. Finding notifications: "Hey — came across something" instead of system alert. User goal completion: warmer phrasing.
- [x] **Updated chat system prompt** — Rewrote Communication section: "Talk like a person, not a bot", don't restate what user said, match their energy, skip filler phrases.
- [x] **Rewrote discord_bot.py messages** — ask_user: "Quick question — ..." not "❓ **I have a question:**". Source approval: "I want to modify a source file — need your OK." Interrupted task recovery: "Looks like I was in the middle of something before the restart."
- [x] **Rewrote proactive initiative message** — One-liner format instead of multi-line system notification.

**Files modified:** `src/core/goal_worker_pool.py`, `src/core/autonomous_executor.py`, `src/core/reporting.py`, `src/core/dream_cycle.py`, `src/interfaces/message_handler.py`, `src/interfaces/discord_bot.py`

</details>

<details>
<summary>Session 44 (Cowork) — Third live test round: ask_user routing, dedup spam, write_source truncation</summary>

- [x] **Log analysis: 8/12 tasks completed** — Major improvement from prior runs (0% → 67% success). Max_tokens fix working perfectly (zero JSON truncation). Rewrite-loop detection firing correctly. Total cost ~$0.17 for 5 goals.
- [x] **Fixed ask_user consuming chat messages** — Added `_is_likely_new_command()` heuristic to `_check_pending_question()` in `discord_bot.py`. Checks for datetime patterns, slash commands, goal-creation phrases, image gen starters, stop/cancel. Messages matching these patterns fall through to normal routing instead of being eaten by the ask_user listener.
- [x] **Fixed duplicate ask_user spam** — Added piggyback dedup to `ask_user()` in `discord_bot.py`. When a question is already pending (another task asked first), new callers wait for the existing answer instead of sending a duplicate Discord message. Thread-safe with double-checked locking.
- [x] **Fixed write_source incomplete code** — Two-part fix: (1) Decomposition prompt (`goal_manager.py`) now includes "CODE SIZE — KEEP write_source SMALL" section: under 80 lines per call, break into multiple tasks for larger programs. Also "ask_user — DON'T DUPLICATE QUESTIONS" section. (2) PlanExecutor system prompt (`plan_executor.py`) now includes "KEEP SCRIPTS SHORT" guidance: use append_file/edit_file to add features incrementally, never rewrite entire file when code is truncated.

**Files modified:** `src/interfaces/discord_bot.py`, `src/core/goal_manager.py`, `src/core/plan_executor.py`

</details>

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
