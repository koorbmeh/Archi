# Archi — Todo List

Last updated: 2026-03-06 (session 200)

---

## Open Items

### Needs live verification

- [ ] **Search query broadening** — (Added session 188. Still untested — DuckDuckGo returns partial matches even for niche queries, so broadening never triggers. Session 187 added `_simplify_query()` and auto-retry on 0 results. Needs a query that truly returns 0 results. **File:** `src/core/plan_executor/actions.py`.)

- [ ] **Git post-modify commit failures** — (Added session 194. Session 195: added fallback identity env vars and improved error logging. Fix should eliminate empty-stderr failures caused by missing git user.name/email. Needs live verification after next deploy. **File:** `src/utils/git_safety.py`.)


- [ ] **Worldview system live verification** — (Added session 199.) Integrated into router, autonomous_executor, heartbeat. Verify: opinions form after tasks, router injects worldview context, pruning/decay works on cycle. **Files:** `src/core/worldview.py`, `src/core/conversational_router.py`.

- [ ] **Adaptive retirement live verification** — (Added session 199.) Runs every 10 dream cycles. Needs a task with >70% ignore rate over 14+ days. **Files:** `src/core/idea_generator.py`, `src/core/heartbeat.py`.

- [ ] **Autonomous scheduling live verification** — (Added session 199.) Runs every 10 dream cycles (offset 7). Needs journal/conversation data to detect patterns. **Files:** `src/core/idea_generator.py`, `src/core/heartbeat.py`.

- [ ] **Self-reflection live verification** — (Added session 199.) Runs every 50 dream cycles. Needs >=5 journal entries in 7 days. **Files:** `src/core/journal.py`, `src/core/heartbeat.py`.

- [ ] **Behavioral rules live verification** — (Added session 200.) Rules crystallize from repeated task outcomes (3+ similar failures/successes). Injected into PlanExecutor hints via `_build_hints()`. Extraction runs during dream cycle learning review. Verify: rules appear in `data/behavioral_rules.json` after repeated patterns, hints show up in task prompts. **Files:** `src/core/behavioral_rules.py`, `src/core/autonomous_executor.py`, `src/core/heartbeat.py`.

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

- [x] **Autonomous scheduling (dream cycle)** — (Added session 196. Fixed session 199.) `suggest_scheduled_tasks()` detects patterns, proposes schedules. Runs every 10 dream cycles. **Files:** `idea_generator.py`, `heartbeat.py`.

- [x] **Adaptive retirement** — (Added session 196. Fixed session 199.) `check_retirement_candidates()` queries ignored tasks, auto-retires Archi-created, proposes user-created. Runs every 10 dream cycles. **File:** `idea_generator.py`, `heartbeat.py`.

### "Becoming Someone" roadmap — next phases (Added session 197)

- [x] **Journal morning orientation integration** — (Added session 197. Fixed session 198.) `reporting.send_morning_report()` calls `journal.get_orientation(days=3)` and passes to formatter. Formatter injects journal context into prompt for continuity. **Files:** `reporting.py`, `notification_formatter.py`. Needs live verification.

- [x] **Worldview system (Phase 2)** — (Added session 197. Fixed session 199.) `data/worldview.json` with evolving opinions, preferences, interests. Integrated into router, autonomous_executor, heartbeat. **File:** `src/core/worldview.py`.

- [x] **Memory shaping behavior (Phase 2)** — (Added session 197. Fixed session 200.) `src/core/behavioral_rules.py` — avoidance and preference rules from repeated outcomes. Injected into PlanExecutor hints via `_build_hints()`. Extraction in heartbeat dream cycle. **Files:** `behavioral_rules.py`, `autonomous_executor.py`, `heartbeat.py`.

- [x] **Self-reflection (Phase 2)** — (Added session 197. Fixed session 199.) Weekly model-based reflection in `journal.py`, triggered every 50 dream cycles. Updates worldview. **Files:** `heartbeat.py`, `journal.py`.

### Back burner

- [ ] **Two-call approach for easy-tier** — (Added session 94.) Only if personality feels robotic after live testing.

- [ ] **Protected-file user-directed override mechanism** — (Added session 95.) On back burner per Jesse (session 97).

- [ ] **Singleton pattern in `local_mcp_server.py` tool caches** — (Added session 137.) Not a bug (per-server-instance).

---

## Completed Work (last 10 sessions)

Older completed work has been archived to `claude/archive/COMPLETED_WORK_SESSIONS_1_96.md`.

**Session 200:** Behavioral rules — memory that shapes action (Phase 2 of "Becoming Someone"). Created `src/core/behavioral_rules.py` (~410 lines): avoidance/preference rules crystallized from repeated task outcomes, keyword-based relevance matching, confidence decay, auto-pruning. Integrated into: `autonomous_executor.py` (`get_relevant_rules()` in `_build_hints()` + `process_task_outcome()` post-task), `heartbeat.py` (dream cycle extraction + periodic pruning). +33 tests, 4267 passing (excl env-specific). Completes Phase 2 of "Becoming Someone" roadmap. **Touches:** `src/core/behavioral_rules.py` (new), `src/core/autonomous_executor.py`, `src/core/heartbeat.py`, `tests/unit/test_behavioral_rules.py` (new).

**Session 199:** Worldview system + self-reflection + adaptive retirement + autonomous scheduling (Phase 2 of "Becoming Someone" + scheduled tasks next phases). (1) Created `src/core/worldview.py` (~490 lines): opinions/preferences/interests with confidence decay, stale-interest decay, size caps, thread-safe CRUD. Integrated into `conversational_router.py` (system prompt injection) and `autonomous_executor.py` (post-task lightweight reflection). (2) Added `generate_self_reflection()` to `journal.py`: model-driven weekly analysis, stores as journal entry, updates worldview. Triggered every 50 dream cycles. (3) Adaptive retirement: `check_retirement_candidates()` in `idea_generator.py` queries ignored tasks, auto-retires Archi-created, proposes user-created. Every 10 dream cycles. (4) Autonomous scheduling: `suggest_scheduled_tasks()` analyzes journal + conversation patterns, proposes schedules (once/day). Every 10 dream cycles, offset 7. +46 tests (worldview 42, journal 97→, idea_generator 237→, heartbeat integration), 4409 passing, 4-5 pre-existing env-specific failures. **Touches:** `src/core/worldview.py` (new), `src/core/journal.py`, `src/core/idea_generator.py`, `src/core/heartbeat.py`, `src/core/autonomous_executor.py`, `src/core/conversational_router.py`, `src/core/notification_formatter.py`, `src/core/reporting.py`, `tests/unit/test_worldview.py` (new), `tests/unit/test_journal.py`, `tests/unit/test_idea_generator.py`, `tests/unit/test_heartbeat.py`.

**Session 198:** Journal morning orientation + engagement acknowledgment window. (1) Wired `journal.get_orientation(days=3)` into `reporting.send_morning_report()` → `notification_formatter.format_morning_report()`. Formatter injects journal context into prompt so Archi can reference yesterday's work in morning messages. (2) Implemented 30-minute engagement acknowledgment window for scheduled notify tasks: `_fire_scheduled_task()` records `{task_id, fired_at}` in `_pending_ack_tasks`; `acknowledge_recent_tasks()` (called from `discord_bot.on_message()`) marks acknowledged; `_check_engagement_timeouts()` (every tick) marks ignored after 30 min. +14 tests, 4361 passing, 24 pre-existing env-specific failures. **Touches:** `src/core/reporting.py`, `src/core/notification_formatter.py`, `src/core/heartbeat.py`, `src/interfaces/discord_bot.py`, `tests/unit/test_notification_formatter.py`, `tests/unit/test_reporting.py`, `tests/unit/test_heartbeat.py`.

**Session 197:** Daily journal system (Phase 1b of "Becoming Someone" roadmap). Created `src/core/journal.py` (~220 lines): daily JSON files in `data/journal/YYYY-MM-DD.json`, timestamped entries with type/content/metadata, summary counters, morning orientation, day summaries, 30-day auto-pruning. Integrated with: `autonomous_executor._record_task_result()` (task completions), `message_handler.process_message()` (conversations), `heartbeat._run_cycle()` (dream cycles + pruning). +32 tests, 4347 passing, 23 pre-existing env-specific failures. **Touches:** `src/core/journal.py` (new), `src/core/autonomous_executor.py`, `src/core/heartbeat.py`, `src/interfaces/message_handler.py`, `tests/unit/test_journal.py` (new).

**Session 196:** Scheduled task system (Phase 1a). Implemented the core scheduled task system from `DESIGN_SCHEDULED_TASKS.md`. (1) Created `src/core/scheduler.py` (~280 lines): `ScheduledTask` dataclass, atomic load/save, CRUD, cron parsing via `croniter`, `check_due_tasks()`, engagement tracking, quiet hours, rate limiting, retirement logic. (2) Added `croniter>=1.3,<3.0` to requirements.txt. (3) Integrated with heartbeat: `_check_scheduled_tasks()` runs every tick, fires `notify` and `create_goal` actions. (4) Added 4 schedule handlers to action_dispatcher. (5) Added `"schedule"` intent + `/schedule` slash command to conversational_router. (6) 54 new tests, 306 passing across all modified modules. (7) Created `claude/CHANGELOG.md` for session-by-session change tracking. **Touches:** `src/core/scheduler.py` (new), `src/core/heartbeat.py`, `src/interfaces/action_dispatcher.py`, `src/core/conversational_router.py`, `requirements.txt`, `data/scheduled_tasks.json` (new), `tests/unit/test_scheduler.py` (new), `tests/unit/test_action_dispatcher.py`, `claude/ARCHITECTURE.md`, `claude/CHANGELOG.md` (new).

**Session 195:** Test run + regression fix + 14 new tests + git commit fix. (1) Fixed heartbeat regression from session 194 — `_dispatch_work()` crashed on MagicMock comparison because `getattr()` returns a MagicMock (not default) when the attribute auto-exists; added `isinstance` guard. (2) Wrote 14 new tests covering session 194's pre-write JSON/HTML validation, heartbeat goal notification cooldown, send_file retry detection, and goal pool init. (3) Fixed git commit failures: added fallback identity env vars (`GIT_AUTHOR_NAME`/`GIT_COMMITTER_NAME` = "Archi") and improved error logging to capture stdout when stderr is empty. 4412 collected, 4388 passing, 24 pre-existing env-specific failures. **Touches:** `src/core/heartbeat.py`, `src/utils/git_safety.py`, `tests/unit/test_plan_executor_actions.py`, `tests/unit/test_heartbeat.py`, `tests/unit/test_discord_bot.py`, `tests/unit/test_goal_worker_pool.py`, `tests/unit/test_git_safety.py`.

**Session 194:** Log analysis + 3 bug fixes (no tests — Cowork env). (1) Pre-write validation for create_file: JSON and HTML content now validated BEFORE writing to disk, preventing truncated files from persisting. (2) Post-goal notification dedup: heartbeat skips work suggestions for 60s after a goal completion notification, preventing duplicate messages about the same topic. (3) send_file retry fix: `_pending_action_retry` now detects send_file follow-ups by response text pattern instead of `rr.action`, fixing the case where router misclassifies "send me the file" as `new_request` instead of `send_file`. Also confirmed session 189-191 deployment is live (garbage guard, conversation starters, health gate all working). ~4260 tests (not re-run this session). **Touches:** `src/core/plan_executor/actions.py`, `src/interfaces/discord_bot.py`, `src/core/heartbeat.py`, `src/core/goal_worker_pool.py`.

**Session 193:** Backfilled input_schema for existing skills, fixed `_extract_input_schema` false positive on capitalized docstring words, added `_extract_description()` for docstring-derived skill descriptions (improves personality in `/skill list`), fixed docstring scanning to find strings after imports. +6 tests, ~4260 total (4235 passing, 24 pre-existing env-specific failures, 2 skipped). **Touches:** `src/core/skill_creator.py`, `data/skills/fetch_stock_prices/SKILL.json`, `data/skills/summarize_web_pages/SKILL.json`, `tests/unit/test_skill_system.py`.

**Session 192:** Skill creator `input_schema` extraction — `_extract_input_schema()` uses AST (params.get calls) + docstring parsing to populate input_schema.properties with param names, types, defaults, descriptions, and required list. Log analysis confirmed sessions 189-191 code not deployed. +9 tests, ~4229 passing. **Touches:** `src/core/skill_creator.py`, `tests/unit/test_skill_system.py`.

(Sessions 1–191 archived to `claude/archive/COMPLETED_WORK_SESSIONS_1_96.md` and earlier TODO.md entries.)
