# Archi — Todo List

Last updated: 2026-03-06 (session 203)

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

- [x] **Tone detection / mood tracking (Phase 3)** — (Added session 201. Fixed session 201.) Router extracts `mood_signal` per message, stored in UserModel (in-memory, 1hr decay), injected into router prompt + notification formatter for behavioral adjustment. **Files:** `conversational_router.py`, `user_model.py`, `notification_formatter.py`.

- [x] **"I changed my mind" — opinion revision (Phase 3)** — (Added session 201. Fixed session 201.) Worldview detects significant opinion changes, flags as `pending_revisions`. Heartbeat delivers via `format_opinion_revision()` in notification_formatter. **Files:** `worldview.py`, `heartbeat.py`, `notification_formatter.py`.

### Needs live verification (Phase 3, added session 201)

- [ ] **Tone detection live verification** — (Added session 201.) Verify mood_signal is populated in router responses, mood context injected into prompts, behavioral adjustment visible in response style. **Files:** `conversational_router.py`, `user_model.py`.

- [ ] **Opinion revision live verification** — (Added session 201.) Needs an opinion to change significantly (position change + confidence delta >= 0.3 or new_confidence >= 0.6). Check `data/worldview.json` for `pending_revisions`, verify heartbeat delivers notification. **Files:** `worldview.py`, `heartbeat.py`, `notification_formatter.py`.

### "Becoming Someone" Phase 4 (Added session 202)

- [x] **Interest-driven exploration (Phase 4)** — (Added session 202. Fixed session 202.) `explore_interest()` in `idea_generator.py` picks highest-curiosity worldview interest, researches via model call, logs to journal, seeds related interests. Heartbeat Phase 6 (~20% of cycles) shares findings via `format_exploration_sharing()`. **Files:** `idea_generator.py`, `heartbeat.py`, `notification_formatter.py`.

- [x] **Aesthetic/taste development (Phase 4)** — (Added session 202. Fixed session 202.) `develop_taste()` in `worldview.py` tracks cost-effectiveness by task type, model performance, and efficiency patterns. Called post-task in `_record_task_result()`. `get_taste_context()` injects preferences into execution hints. **Files:** `worldview.py`, `autonomous_executor.py`.

- [x] **Long-term personal projects (Phase 4)** — (Added session 203. Fixed session 203.) `propose_personal_project()` + `work_on_personal_project()` in `idea_generator.py`. Projects emerge from explored high-curiosity interests. Heartbeat Phase 6.5 (every 10th cycle). Share-worthy findings sent via `format_project_sharing()`. Data in `worldview.json`. **Files:** `worldview.py`, `idea_generator.py`, `heartbeat.py`, `notification_formatter.py`.

- [x] **Meta-cognition (Phase 4)** — (Added session 203. Fixed session 203.) `generate_meta_cognition()` in `idea_generator.py`. Analyzes behavioral rules, taste, journal, existing observations to detect meta-patterns. Observations stored in `worldview.json` under `meta_observations`. Injected into router prompt + PlanExecutor hints. Triggered during weekly self-reflection (every 50 cycles). **Files:** `worldview.py`, `idea_generator.py`, `heartbeat.py`, `conversational_router.py`, `autonomous_executor.py`.

### Needs live verification (Phase 4, added session 202-203)

- [ ] **Interest exploration live verification** — (Added session 202.) Verify exploration triggers every 5th dream cycle (offset 2), worldview interests have `last_explored` updated, journal shows exploration entries, Discord receives exploration sharing messages. **Files:** `idea_generator.py`, `heartbeat.py`.

- [ ] **Taste development live verification** — (Added session 202.) Verify taste preferences appear in `data/worldview.json` under `taste_efficiency`, `taste_caution`, `taste_model` domains after task completions. Check that `get_taste_context()` output appears in PlanExecutor hints. **Files:** `worldview.py`, `autonomous_executor.py`.

- [ ] **Personal projects live verification** — (Added session 203.) Verify: projects are proposed from high-curiosity interests, heartbeat Phase 6.5 fires every 10th cycle (offset 4), progress notes accumulate, share-worthy findings sent to Discord. **Files:** `worldview.py`, `idea_generator.py`, `heartbeat.py`.

- [ ] **Meta-cognition live verification** — (Added session 203.) Verify: observations appear in `data/worldview.json` `meta_observations` after 50-cycle self-reflection, `get_meta_context()` output visible in router prompts and PlanExecutor hints. **Files:** `worldview.py`, `idea_generator.py`, `heartbeat.py`.

### Bug fix needed

- [ ] **"test" notification spam** — (Added session 203.) 91 "test" notifications logged from Mar 1-6 in conversations.jsonl. Garbage guard in `discord_bot.py` should catch these (single word < 20 chars). Likely cause: running process predates the garbage guard code (sessions 186-189) or code was reloaded without full restart. Cleared the stale log entries this session. Verify after next full restart that "test" messages are suppressed. **Files:** `src/interfaces/discord_bot.py` (`_is_garbage_notification()`).

- [ ] **git index.lock file** — (Added session 203.) `.git/index.lock` exists from the live Archi process. Prevents git operations in Cowork sessions. Need to stop the live process before doing git ops, or handle the lock. Not urgent — just means session 203 changes can't be committed from Cowork.

### Back burner

- [ ] **Two-call approach for easy-tier** — (Added session 94.) Only if personality feels robotic after live testing.

- [ ] **Protected-file user-directed override mechanism** — (Added session 95.) On back burner per Jesse (session 97).

- [ ] **Singleton pattern in `local_mcp_server.py` tool caches** — (Added session 137.) Not a bug (per-server-instance).

---

## Completed Work (last 10 sessions)

Older completed work has been archived to `claude/archive/COMPLETED_WORK_SESSIONS_1_96.md`.

**Session 203:** Phase 4 — long-term personal projects + meta-cognition ("Becoming Someone"), log cleanup. (1) Personal projects: `add_personal_project()`, `update_personal_project()`, `get_project_context()` in `worldview.py`. `propose_personal_project()` + `work_on_personal_project()` in `idea_generator.py`. Projects emerge from high-curiosity explored interests, tracked with progress notes and session counts. Heartbeat Phase 6.5 (every 10th cycle, offset 4). `format_project_sharing()` in notification_formatter. (2) Meta-cognition: `add_meta_observation()`, `update_meta_adjustment()`, `get_meta_context()` in `worldview.py`. `generate_meta_cognition()` in `idea_generator.py` analyzes behavioral rules, taste, journal to detect meta-patterns. Runs during weekly self-reflection (Phase 5). Context injected into router prompt + PlanExecutor hints. (3) Live verification: no worldview.json/behavioral_rules.json/journal/ files exist — features haven't been activated (process needs restart). 91 "test" spam notifications in conversations.jsonl (cleared). git index.lock blocks commits. +25 tests (15 worldview, 10 idea_generator), 4555 collected, 4442 passing (23 croniter + env-specific). **Touches:** `worldview.py`, `idea_generator.py`, `heartbeat.py`, `notification_formatter.py`, `conversational_router.py`, `autonomous_executor.py`, `tests/unit/test_worldview.py`, `tests/unit/test_idea_generator.py`, `logs/conversations.jsonl`, `data/dream_log.jsonl`.

**Session 202:** Phase 4 — interest-driven exploration + aesthetic taste development ("Becoming Someone"). (1) Interest exploration: `explore_interest()` in `idea_generator.py` picks highest-curiosity worldview interest, researches via model call, updates `last_explored`, logs to journal, seeds related interests via `connects_to`. Heartbeat Phase 6 (~20% of cycles, every 5th offset 2) shares findings via `format_exploration_sharing()` in notification_formatter. (2) Taste development: `develop_taste()` in `worldview.py` tracks cost-effectiveness by task type (research/writing/coding/analysis), model performance preferences, efficiency patterns. Called post-task from `_record_task_result()`. `get_taste_context()` injects learned preferences into PlanExecutor execution hints. +16 tests (7 taste, 5 exploration, 3 formatter, 1 classification), 4530 collected, 4417 passing (23 pre-existing croniter). **Touches:** `worldview.py`, `idea_generator.py`, `heartbeat.py`, `notification_formatter.py`, `autonomous_executor.py`, `tests/unit/test_worldview.py`, `tests/unit/test_idea_generator.py`, `tests/unit/test_notification_formatter.py`.

**Session 201:** Phase 3 — tone detection + opinion revision ("Becoming Someone"). (1) Tone detection: Router extracts `mood_signal` per message (busy/frustrated/excited/engaged/tired/playful), stored in UserModel (in-memory, last 10, 1hr decay), injected into router prompt + notification formatter for behavioral adjustment. `get_mood_context()` returns short instructions like "Jesse seems busy — keep responses short." (2) Opinion revision: `worldview.add_opinion()` detects significant position changes (confidence delta >= 0.3 or new_confidence >= 0.6), stores as `pending_revisions` in worldview.json. Heartbeat Phase 5.5 delivers up to 2/cycle via `format_opinion_revision()`. Cleared after delivery. +24 tests (11 mood, 11 revision, 2 router), 4471 passing (20 pre-existing env-specific). **Touches:** `conversational_router.py`, `user_model.py`, `notification_formatter.py`, `worldview.py`, `heartbeat.py`, `tests/unit/test_user_model.py`, `tests/unit/test_worldview.py`, `tests/unit/test_conversational_router.py`.

**Session 200:** Behavioral rules — memory that shapes action (Phase 2 of "Becoming Someone"). Created `src/core/behavioral_rules.py` (~410 lines): avoidance/preference rules crystallized from repeated task outcomes, keyword-based relevance matching, confidence decay, auto-pruning. Integrated into: `autonomous_executor.py` (`get_relevant_rules()` in `_build_hints()` + `process_task_outcome()` post-task), `heartbeat.py` (dream cycle extraction + periodic pruning). +33 tests, 4472 passing (20 pre-existing env-specific failures). Completes Phase 2 of "Becoming Someone" roadmap. **Touches:** `src/core/behavioral_rules.py` (new), `src/core/autonomous_executor.py`, `src/core/heartbeat.py`, `tests/unit/test_behavioral_rules.py` (new).

**Session 199:** Worldview system + self-reflection + adaptive retirement + autonomous scheduling (Phase 2 of "Becoming Someone" + scheduled tasks next phases). (1) Created `src/core/worldview.py` (~490 lines): opinions/preferences/interests with confidence decay, stale-interest decay, size caps, thread-safe CRUD. Integrated into `conversational_router.py` (system prompt injection) and `autonomous_executor.py` (post-task lightweight reflection). (2) Added `generate_self_reflection()` to `journal.py`: model-driven weekly analysis, stores as journal entry, updates worldview. Triggered every 50 dream cycles. (3) Adaptive retirement: `check_retirement_candidates()` in `idea_generator.py` queries ignored tasks, auto-retires Archi-created, proposes user-created. Every 10 dream cycles. (4) Autonomous scheduling: `suggest_scheduled_tasks()` analyzes journal + conversation patterns, proposes schedules (once/day). Every 10 dream cycles, offset 7. +46 tests (worldview 42, journal 97→, idea_generator 237→, heartbeat integration), 4409 passing, 4-5 pre-existing env-specific failures. **Touches:** `src/core/worldview.py` (new), `src/core/journal.py`, `src/core/idea_generator.py`, `src/core/heartbeat.py`, `src/core/autonomous_executor.py`, `src/core/conversational_router.py`, `src/core/notification_formatter.py`, `src/core/reporting.py`, `tests/unit/test_worldview.py` (new), `tests/unit/test_journal.py`, `tests/unit/test_idea_generator.py`, `tests/unit/test_heartbeat.py`.

**Session 198:** Journal morning orientation + engagement acknowledgment window. (1) Wired `journal.get_orientation(days=3)` into `reporting.send_morning_report()` → `notification_formatter.format_morning_report()`. Formatter injects journal context into prompt so Archi can reference yesterday's work in morning messages. (2) Implemented 30-minute engagement acknowledgment window for scheduled notify tasks: `_fire_scheduled_task()` records `{task_id, fired_at}` in `_pending_ack_tasks`; `acknowledge_recent_tasks()` (called from `discord_bot.on_message()`) marks acknowledged; `_check_engagement_timeouts()` (every tick) marks ignored after 30 min. +14 tests, 4361 passing, 24 pre-existing env-specific failures. **Touches:** `src/core/reporting.py`, `src/core/notification_formatter.py`, `src/core/heartbeat.py`, `src/interfaces/discord_bot.py`, `tests/unit/test_notification_formatter.py`, `tests/unit/test_reporting.py`, `tests/unit/test_heartbeat.py`.

**Session 197:** Daily journal system (Phase 1b of "Becoming Someone" roadmap). Created `src/core/journal.py` (~220 lines): daily JSON files in `data/journal/YYYY-MM-DD.json`, timestamped entries with type/content/metadata, summary counters, morning orientation, day summaries, 30-day auto-pruning. Integrated with: `autonomous_executor._record_task_result()` (task completions), `message_handler.process_message()` (conversations), `heartbeat._run_cycle()` (dream cycles + pruning). +32 tests, 4347 passing, 23 pre-existing env-specific failures. **Touches:** `src/core/journal.py` (new), `src/core/autonomous_executor.py`, `src/core/heartbeat.py`, `src/interfaces/message_handler.py`, `tests/unit/test_journal.py` (new).

**Session 196:** Scheduled task system (Phase 1a). Implemented the core scheduled task system from `DESIGN_SCHEDULED_TASKS.md`. (1) Created `src/core/scheduler.py` (~280 lines): `ScheduledTask` dataclass, atomic load/save, CRUD, cron parsing via `croniter`, `check_due_tasks()`, engagement tracking, quiet hours, rate limiting, retirement logic. (2) Added `croniter>=1.3,<3.0` to requirements.txt. (3) Integrated with heartbeat: `_check_scheduled_tasks()` runs every tick, fires `notify` and `create_goal` actions. (4) Added 4 schedule handlers to action_dispatcher. (5) Added `"schedule"` intent + `/schedule` slash command to conversational_router. (6) 54 new tests, 306 passing across all modified modules. (7) Created `claude/CHANGELOG.md` for session-by-session change tracking. **Touches:** `src/core/scheduler.py` (new), `src/core/heartbeat.py`, `src/interfaces/action_dispatcher.py`, `src/core/conversational_router.py`, `requirements.txt`, `data/scheduled_tasks.json` (new), `tests/unit/test_scheduler.py` (new), `tests/unit/test_action_dispatcher.py`, `claude/ARCHITECTURE.md`, `claude/CHANGELOG.md` (new).

**Session 195:** Test run + regression fix + 14 new tests + git commit fix. (1) Fixed heartbeat regression from session 194 — `_dispatch_work()` crashed on MagicMock comparison because `getattr()` returns a MagicMock (not default) when the attribute auto-exists; added `isinstance` guard. (2) Wrote 14 new tests covering session 194's pre-write JSON/HTML validation, heartbeat goal notification cooldown, send_file retry detection, and goal pool init. (3) Fixed git commit failures: added fallback identity env vars (`GIT_AUTHOR_NAME`/`GIT_COMMITTER_NAME` = "Archi") and improved error logging to capture stdout when stderr is empty. 4412 collected, 4388 passing, 24 pre-existing env-specific failures. **Touches:** `src/core/heartbeat.py`, `src/utils/git_safety.py`, `tests/unit/test_plan_executor_actions.py`, `tests/unit/test_heartbeat.py`, `tests/unit/test_discord_bot.py`, `tests/unit/test_goal_worker_pool.py`, `tests/unit/test_git_safety.py`.

**Session 194:** Log analysis + 3 bug fixes (no tests — Cowork env). (1) Pre-write validation for create_file: JSON and HTML content now validated BEFORE writing to disk, preventing truncated files from persisting. (2) Post-goal notification dedup: heartbeat skips work suggestions for 60s after a goal completion notification, preventing duplicate messages about the same topic. (3) send_file retry fix: `_pending_action_retry` now detects send_file follow-ups by response text pattern instead of `rr.action`, fixing the case where router misclassifies "send me the file" as `new_request` instead of `send_file`. Also confirmed session 189-191 deployment is live (garbage guard, conversation starters, health gate all working). ~4260 tests (not re-run this session). **Touches:** `src/core/plan_executor/actions.py`, `src/interfaces/discord_bot.py`, `src/core/heartbeat.py`, `src/core/goal_worker_pool.py`.

(Sessions 1–193 archived to `claude/archive/COMPLETED_WORK_SESSIONS_1_96.md` and earlier TODO.md entries.)
