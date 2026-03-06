# Archi — Todo List

Last updated: 2026-03-06 (session 198)

---

## Open Items

### Needs live verification

- [ ] **Search query broadening** — (Added session 188. Still untested — DuckDuckGo returns partial matches even for niche queries, so broadening never triggers. Session 187 added `_simplify_query()` and auto-retry on 0 results. Needs a query that truly returns 0 results. **File:** `src/core/plan_executor/actions.py`.)

- [ ] **Git post-modify commit failures** — (Added session 194. Session 195: added fallback identity env vars and improved error logging. Fix should eliminate empty-stderr failures caused by missing git user.name/email. Needs live verification after next deploy. **File:** `src/utils/git_safety.py`.)

### Low priority

- [ ] **Test count discrepancy between Linux and Windows** — (Added session 125. Investigated session 132, session 193.) Linux ~4412 vs Windows ~1399. Gap is from environmental module availability differences, not code issues. Windows count is from session 125 (70 sessions ago) and likely stale. Needs Windows re-verification.

### Code quality (evaluated / low priority)

- [ ] **`_record_task_result()` still ~68 lines** — (Added session 139.) Further decomposition not worthwhile — remaining code is learning recording + morning report + file tracking, each ~15 lines with different concerns.

- [ ] **`on_message()` still 369 lines** — (Added session 140.) Naturally branching event handler logic.

- [ ] **`_handle_config_commands()` is 161 lines** — (Added session 140.) Contains 7 command handlers, each 10-35 lines.

- [ ] **`autonomous_executor.py` `execute_task()` is ~127 lines** — (Added session 159. Re-evaluated session 166.) Remaining code is orchestration — further extraction would just be wrapper indirection.

- [ ] **`scripts/fix.py` `run_diagnostics()` is ~252 lines** — (Added session 159.) Script code, not runtime.

### Scheduled task system — next phases (Added session 196)

- [x] **Engagement acknowledgment window** — (Added session 196. Fixed session 198.) 30-minute window: `_fire_scheduled_task()` records task_id+timestamp, `acknowledge_recent_tasks()` called on user message, `_check_engagement_timeouts()` marks ignored on tick. **Files:** `heartbeat.py`, `discord_bot.py`. Needs live verification.

- [ ] **Autonomous scheduling (dream cycle)** — Archi notices patterns and proposes scheduled tasks. Integration in `idea_generator.py`. Non-notification tasks created silently; notification tasks proposed to Jesse first. **Files:** `idea_generator.py`, `scheduler.py`.

- [ ] **Adaptive retirement** — `idea_generator.py` calls `scheduler.get_ignored_tasks()` during dream cycles and proposes/auto-retires ignored tasks. User-created tasks get asked about; Archi-created ones disabled silently with notification. **File:** `idea_generator.py`.

### "Becoming Someone" roadmap — next phases (Added session 197)

- [x] **Journal morning orientation integration** — (Added session 197. Fixed session 198.) `reporting.send_morning_report()` calls `journal.get_orientation(days=3)` and passes to formatter. Formatter injects journal context into prompt for continuity. **Files:** `reporting.py`, `notification_formatter.py`. Needs live verification.

- [ ] **Worldview system (Phase 2)** — `data/worldview.json` with evolving opinions, preferences, and interests derived from actual experiences. Inject into router system prompt. See `DESIGN_BECOMING_SOMEONE.md`. **New file:** `src/core/worldview.py`.

- [ ] **Memory shaping behavior (Phase 2)** — Behavioral rules derived from repeated successes/failures. Inject into PlanExecutor hints. See `DESIGN_BECOMING_SOMEONE.md`. **Files:** `learning_system.py`, `plan_executor/executor.py`.

- [ ] **Self-reflection (Phase 2)** — Weekly deep reflection during dream cycles. Store in journal. See `DESIGN_BECOMING_SOMEONE.md`. **Files:** `heartbeat.py`, `journal.py`.

### Back burner

- [ ] **Two-call approach for easy-tier** — (Added session 94.) Only if personality feels robotic after live testing.

- [ ] **Protected-file user-directed override mechanism** — (Added session 95.) On back burner per Jesse (session 97).

- [ ] **Singleton pattern in `local_mcp_server.py` tool caches** — (Added session 137.) Not a bug (per-server-instance).

---

## Completed Work (last 10 sessions)

Older completed work has been archived to `claude/archive/COMPLETED_WORK_SESSIONS_1_96.md`.

**Session 198:** Journal morning orientation + engagement acknowledgment window. (1) Wired `journal.get_orientation(days=3)` into `reporting.send_morning_report()` → `notification_formatter.format_morning_report()`. Formatter injects journal context into prompt so Archi can reference yesterday's work in morning messages. (2) Implemented 30-minute engagement acknowledgment window for scheduled notify tasks: `_fire_scheduled_task()` records `{task_id, fired_at}` in `_pending_ack_tasks`; `acknowledge_recent_tasks()` (called from `discord_bot.on_message()`) marks acknowledged; `_check_engagement_timeouts()` (every tick) marks ignored after 30 min. +14 tests, 4361 passing, 24 pre-existing env-specific failures. **Touches:** `src/core/reporting.py`, `src/core/notification_formatter.py`, `src/core/heartbeat.py`, `src/interfaces/discord_bot.py`, `tests/unit/test_notification_formatter.py`, `tests/unit/test_reporting.py`, `tests/unit/test_heartbeat.py`.

**Session 197:** Daily journal system (Phase 1b of "Becoming Someone" roadmap). Created `src/core/journal.py` (~220 lines): daily JSON files in `data/journal/YYYY-MM-DD.json`, timestamped entries with type/content/metadata, summary counters, morning orientation, day summaries, 30-day auto-pruning. Integrated with: `autonomous_executor._record_task_result()` (task completions), `message_handler.process_message()` (conversations), `heartbeat._run_cycle()` (dream cycles + pruning). +32 tests, 4347 passing, 23 pre-existing env-specific failures. **Touches:** `src/core/journal.py` (new), `src/core/autonomous_executor.py`, `src/core/heartbeat.py`, `src/interfaces/message_handler.py`, `tests/unit/test_journal.py` (new).

**Session 196:** Scheduled task system (Phase 1a). Implemented the core scheduled task system from `DESIGN_SCHEDULED_TASKS.md`. (1) Created `src/core/scheduler.py` (~280 lines): `ScheduledTask` dataclass, atomic load/save, CRUD, cron parsing via `croniter`, `check_due_tasks()`, engagement tracking, quiet hours, rate limiting, retirement logic. (2) Added `croniter>=1.3,<3.0` to requirements.txt. (3) Integrated with heartbeat: `_check_scheduled_tasks()` runs every tick, fires `notify` and `create_goal` actions. (4) Added 4 schedule handlers to action_dispatcher. (5) Added `"schedule"` intent + `/schedule` slash command to conversational_router. (6) 54 new tests, 306 passing across all modified modules. (7) Created `claude/CHANGELOG.md` for session-by-session change tracking. **Touches:** `src/core/scheduler.py` (new), `src/core/heartbeat.py`, `src/interfaces/action_dispatcher.py`, `src/core/conversational_router.py`, `requirements.txt`, `data/scheduled_tasks.json` (new), `tests/unit/test_scheduler.py` (new), `tests/unit/test_action_dispatcher.py`, `claude/ARCHITECTURE.md`, `claude/CHANGELOG.md` (new).

**Session 195:** Test run + regression fix + 14 new tests + git commit fix. (1) Fixed heartbeat regression from session 194 — `_dispatch_work()` crashed on MagicMock comparison because `getattr()` returns a MagicMock (not default) when the attribute auto-exists; added `isinstance` guard. (2) Wrote 14 new tests covering session 194's pre-write JSON/HTML validation, heartbeat goal notification cooldown, send_file retry detection, and goal pool init. (3) Fixed git commit failures: added fallback identity env vars (`GIT_AUTHOR_NAME`/`GIT_COMMITTER_NAME` = "Archi") and improved error logging to capture stdout when stderr is empty. 4412 collected, 4388 passing, 24 pre-existing env-specific failures. **Touches:** `src/core/heartbeat.py`, `src/utils/git_safety.py`, `tests/unit/test_plan_executor_actions.py`, `tests/unit/test_heartbeat.py`, `tests/unit/test_discord_bot.py`, `tests/unit/test_goal_worker_pool.py`, `tests/unit/test_git_safety.py`.

**Session 194:** Log analysis + 3 bug fixes (no tests — Cowork env). (1) Pre-write validation for create_file: JSON and HTML content now validated BEFORE writing to disk, preventing truncated files from persisting. (2) Post-goal notification dedup: heartbeat skips work suggestions for 60s after a goal completion notification, preventing duplicate messages about the same topic. (3) send_file retry fix: `_pending_action_retry` now detects send_file follow-ups by response text pattern instead of `rr.action`, fixing the case where router misclassifies "send me the file" as `new_request` instead of `send_file`. Also confirmed session 189-191 deployment is live (garbage guard, conversation starters, health gate all working). ~4260 tests (not re-run this session). **Touches:** `src/core/plan_executor/actions.py`, `src/interfaces/discord_bot.py`, `src/core/heartbeat.py`, `src/core/goal_worker_pool.py`.

**Session 193:** Backfilled input_schema for existing skills, fixed `_extract_input_schema` false positive on capitalized docstring words, added `_extract_description()` for docstring-derived skill descriptions (improves personality in `/skill list`), fixed docstring scanning to find strings after imports. +6 tests, ~4260 total (4235 passing, 24 pre-existing env-specific failures, 2 skipped). **Touches:** `src/core/skill_creator.py`, `data/skills/fetch_stock_prices/SKILL.json`, `data/skills/summarize_web_pages/SKILL.json`, `tests/unit/test_skill_system.py`.

**Session 192:** Skill creator `input_schema` extraction — `_extract_input_schema()` uses AST (params.get calls) + docstring parsing to populate input_schema.properties with param names, types, defaults, descriptions, and required list. Log analysis confirmed sessions 189-191 code not deployed. +9 tests, ~4229 passing. **Touches:** `src/core/skill_creator.py`, `tests/unit/test_skill_system.py`.

**Session 191:** Fixed Discord startup network failure (CRITICAL) + skill_summarize_web_pages silent failure + 12 new tests. Three-part network fix: DNS probe loop, transient error retry in `run_bot()`, heartbeat health gate. Skill fix: certifi SSL opener, realistic User-Agent, empty-error fallback. ~4220 tests passing. **Touches:** `src/service/archi_service.py`, `src/interfaces/discord_bot.py`, `src/core/skill_system.py`, `data/skills/summarize_web_pages/skill.py`, `tests/unit/test_archi_service.py`.

**Session 190:** Live test log analysis (no code changes). Verified garbage notification guard and tool name stripping. Found Discord startup network failure bug and skill_summarize_web_pages failure.

**Session 189:** Fixed "test" message bypass, tool name leak in task completion, conversation starter diversity (forced category rotation). +17 tests. ~4207 passing.

(Sessions 1–188 archived to `claude/archive/COMPLETED_WORK_SESSIONS_1_96.md` and earlier TODO.md entries.)
