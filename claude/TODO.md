# Archi — Todo List

Last updated: 2026-03-06 (session 209)

---

## Open Items

### Needs live verification

- [ ] **Search query broadening** — (Added session 188. Still untested — DuckDuckGo returns partial matches even for niche queries, so broadening never triggers. Session 187 added `_simplify_query()` and auto-retry on 0 results. Needs a query that truly returns 0 results. **File:** `src/core/plan_executor/actions.py`.)

- [ ] **Git post-modify commit failures** — (Added session 194. Session 195: added fallback identity env vars and improved error logging. Fix should eliminate empty-stderr failures caused by missing git user.name/email. Needs live verification after next deploy. **File:** `src/utils/git_safety.py`.)


- [ ] **Worldview system live verification** — (Added session 199. Session 208: two root causes found and fixed. (1) Dream-mode bootstrap: `_lightweight_reflection()` never created opinions/interests from scratch — fixed by adding domain interest seeding when <3 interests exist. (2) Chat-mode gap: `_run_plan_executor()` in `message_handler.py` never called `reflect_on_task()` or `develop_taste()` — 9 chat-mode tasks on Mar 6 produced zero worldview updates. Fixed in session 209 by adding `_record_chat_task_reflection()`. Both paths now update worldview. Needs post-restart verification.) **Files:** `src/core/worldview.py`, `src/core/autonomous_executor.py`, `src/interfaces/message_handler.py`.

- [ ] **Adaptive retirement live verification** — (Added session 199.) Runs every 10 dream cycles. Needs a task with >70% ignore rate over 14+ days. **Files:** `src/core/idea_generator.py`, `src/core/heartbeat.py`.

- [ ] **Autonomous scheduling live verification** — (Added session 199.) Runs every 10 dream cycles (offset 7). Needs journal/conversation data to detect patterns. **Files:** `src/core/idea_generator.py`, `src/core/heartbeat.py`.

- [ ] **Self-reflection live verification** — (Added session 199.) Runs every 50 dream cycles. Needs >=5 journal entries in 7 days. **Files:** `src/core/journal.py`, `src/core/heartbeat.py`.

- [x] **Behavioral rules live verification** — (Added session 200. Verified session 207.) Rules crystallized from live task outcomes: 1 avoidance rule (urllib-based web scraping, 78 evidence, strength 1.0) and 1 preference rule (web_search for puppy/JSON queries, 60 evidence, strength 1.0). Data in `data/behavioral_rules.json`. **Files:** `src/core/behavioral_rules.py`, `src/core/autonomous_executor.py`, `src/core/heartbeat.py`.

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

- [x] **Tone detection live verification** — (Added session 201. Verified session 207.) mood_signal populated correctly in journal entries (busy, frustrated, excited, playful, engaged, neutral all observed). Response style adjusts to tone — shorter for busy, gentler for frustrated, playful for playful. **Files:** `conversational_router.py`, `user_model.py`.

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

- [x] **"test" notification spam** — (Added session 203. Clarified session 207.) Jesse confirmed Archi was never actually sending these to Discord — they were filtered before delivery. The log entries appear as `dream_cycle_outbound` with response "test" at 07:02-07:59 on Mar 6 (11 entries), but Jesse never received them. The garbage guard in `_is_garbage_notification()` is working correctly. The "test" messages in the log are produced by the dream cycle (not by pytest execution). No code fix needed — resolved by existing filtering. **Files:** `src/interfaces/discord_bot.py` (`_is_garbage_notification()`).

- [x] **Stuck dream cycles — unreachable pending tasks** — (Added session 204. Fixed session 204.) Goals with failed tasks had dependent tasks stuck in PENDING because cascade-blocking wasn't applied retroactively to loaded state. Added `_repair_blocked_tasks()` to `prune_stale_goals()` — marks pending tasks with failed dependencies as BLOCKED so all-terminal pruning works. **Files:** `src/core/idea_generator.py`.

- [x] **git index.lock file** — (Added session 203. Fixed session 204.) Removed stale 0-byte lock and committed session 203 changes.

- [ ] **Recurring git lock files** — (Added session 208.) `index.lock` and `HEAD.lock` found again during session 208. This is a recurring issue — Archi's git operations may be leaving stale locks when tasks are interrupted or concurrent. Consider adding lock cleanup to `scripts/fix.py` or pre-commit safety in `git_safety.py`. **Files:** `src/utils/git_safety.py`, `scripts/fix.py`.

- [ ] **Refactor long functions in idea_generator.py** — (Added session 208.) 8 functions over 60 lines, `generate_meta_cognition()` is 133 lines. Most concerning per CODE_STANDARDS.md 40-line guideline. Low priority but should be addressed when context allows. **File:** `src/core/idea_generator.py`.

### Back burner

- [ ] **Two-call approach for easy-tier** — (Added session 94.) Only if personality feels robotic after live testing.

- [ ] **Protected-file user-directed override mechanism** — (Added session 95.) On back burner per Jesse (session 97).

- [ ] **Singleton pattern in `local_mcp_server.py` tool caches** — (Added session 137.) Not a bug (per-server-instance).

---

## Completed Work (last 10 sessions)

Older completed work has been archived to `claude/archive/COMPLETED_WORK_SESSIONS_1_96.md`.

**Session 209:** Chat-mode worldview reflection. Found the primary root cause of worldview.json never being created: chat-mode PlanExecutor tasks (via `message_handler._run_plan_executor()`) never called `reflect_on_task()` or `develop_taste()` — only dream-mode tasks (via `autonomous_executor._record_task_result()`) did. All 9 user tasks on Mar 6 were chat-mode, so zero worldview updates occurred. Fix: added `_record_chat_task_reflection()` in `message_handler.py`, called after every chat-mode PlanExecutor result. Covers worldview reflection, taste development, and behavioral rules. Complementary to session 208's dream-mode bootstrap fix. Also investigated "Invalid format string" error in live scheduler — could not reproduce, likely from old code before session 207 deploy. +5 tests. **Test count:** 4586 passed, 18 skipped (up from 4581). **Touches:** `src/interfaces/message_handler.py`, `tests/unit/test_message_handler.py`.

**Session 208:** Worldview bootstrap fix. Diagnosed and fixed why `data/worldview.json` was never created despite 7+ dream cycles and 6+ completed tasks. Root cause: `_lightweight_reflection()` only reinforced existing opinions (never created new ones) — empty worldview means no-op every time. `develop_taste()` also dead: `model_used` never populated in PlanExecutor results, and efficiency condition required `verified=True`. Fix: (1) `_lightweight_reflection()` now seeds interests from task domains (keyword-to-domain mapping) when <3 interests exist — bootstraps the worldview so exploration/self-reflection can take over. (2) `develop_taste()` now records preferences for unverified-but-efficient tasks (strength 0.3 vs 0.5 for verified). (3) `autonomous_executor` now injects `router.get_active_model_info()` into result dict so `develop_taste()` can track model effectiveness. Also removed stale git lock files (index.lock + HEAD.lock). +5 tests, updated 2 existing tests. **Test count:** 4581 passed, 18 skipped (up from 4576). **Touches:** `src/core/worldview.py`, `src/core/autonomous_executor.py`, `tests/unit/test_worldview.py`.

**Session 207:** Live verification + UX fixes (time format, file delivery). (1) Live verification after restart: journal system working (mood signals, conversations, dream cycles all logging correctly), tone detection verified (busy/frustrated/excited/playful/engaged/neutral all observed with appropriate response adjustments), scheduled tasks create/modify working, behavioral rules forming (1 avoidance + 1 preference rule from live task outcomes), dream cycles running. Worldview.json not yet created — needs more cycles. "test" notification spam confirmed filtered by garbage guard (Jesse never received them). (2) Time format fix: added `format_friendly_time()` to `scheduler.py` converting ISO timestamps to human-readable format (e.g. "4:20 PM today", "9:00 AM tomorrow"). Applied in `action_dispatcher.py` create/modify schedule responses and `format_task_list()`. (3) File delivery fix: chat-mode responses now attach files created by PlanExecutor as Discord attachments (extended `media_files` in `discord_bot.py`); dream-mode goal completion notifications now attach the first sendable file via `file_path=` parameter in `_notify_goal_result()`. 8 MB size limit, skip binary/DB files. +8 tests (format_friendly_time edge cases + format_task_list integration). **Test count:** 4576 passed, 18 skipped (up from 4568). **Touches:** `src/core/scheduler.py`, `src/interfaces/action_dispatcher.py`, `src/interfaces/discord_bot.py`, `src/core/goal_worker_pool.py`, `tests/unit/test_scheduler.py`.

**Session 206:** Import cleanup + test coverage expansion. (1) Removed unused imports: `timedelta` from `worldview.py` and `behavioral_rules.py`, `Dict` from `worldview.py` and `scheduler.py`. (2) Added 25 new edge case tests: worldview load/prune/taste/reflection/revision edge cases (15 tests in `test_worldview.py`), behavioral rules process outcome/load/cluster/prune edge cases (10 tests in `test_behavioral_rules.py`). No code logic changes to src/. **Test count:** 4568 passed, 18 skipped (up from 4543). **Touches:** `src/core/worldview.py`, `src/core/behavioral_rules.py`, `src/core/scheduler.py`, `tests/unit/test_worldview.py`, `tests/unit/test_behavioral_rules.py`.

**Session 205:** Code review + README update + test count verification. (1) Ran full test suite: 4543 passed, 18 skipped (up from 4472 last session — increase from croniter now installed in test env). (2) Investigated "test" notification spam bug — confirmed garbage guard code is correct, spam was from stale process predating the guard; no code fix needed, requires restart verification. (3) Reviewed heartbeat dream cycle phase offsets — no collisions, well-distributed. (4) Reviewed worldview.py, behavioral_rules.py, journal.py, idea_generator.py for code quality — all clean. (5) Updated README.md with features from sessions 196-204 (scheduled tasks, personality/growth, curiosity/projects, social awareness). No code changes. **Test count:** 4543 passed, 18 skipped.

**Session 204:** Post-Phase 4 quality pass + dream cycle health fix. (1) Committed session 203 changes (git index.lock removed). (2) Quality pass: reviewed prompt bloat — router context injections well-bounded (~900 chars max from worldview+meta+project+mood, all capped). PlanExecutor hints have 3000-char hard cap via `_cap_hints`. Cost impact minimal (~$0.002-0.005 per exploration call, well under $0.50/cycle cap). (3) Dream cycle health: diagnosed stuck goals — failed tasks had dependent tasks in PENDING because cascade-blocking wasn't applied to loaded state. Added `_repair_blocked_tasks()` to `prune_stale_goals()`: BFS marks unreachable pending tasks as BLOCKED, enabling all-terminal pruning to clean dead goals. (4) +8 tests (4 repair, 4 updated prune). 4470 passing (excl env-specific). **Touches:** `src/core/idea_generator.py`, `tests/unit/test_idea_generator.py`.

**Session 203:** Phase 4 — long-term personal projects + meta-cognition ("Becoming Someone"), log cleanup. (1) Personal projects: `add_personal_project()`, `update_personal_project()`, `get_project_context()` in `worldview.py`. `propose_personal_project()` + `work_on_personal_project()` in `idea_generator.py`. Projects emerge from high-curiosity explored interests, tracked with progress notes and session counts. Heartbeat Phase 6.5 (every 10th cycle, offset 4). `format_project_sharing()` in notification_formatter. (2) Meta-cognition: `add_meta_observation()`, `update_meta_adjustment()`, `get_meta_context()` in `worldview.py`. `generate_meta_cognition()` in `idea_generator.py` analyzes behavioral rules, taste, journal to detect meta-patterns. Runs during weekly self-reflection (Phase 5). Context injected into router prompt + PlanExecutor hints. (3) Live verification: no worldview.json/behavioral_rules.json/journal/ files exist — features haven't been activated (process needs restart). 91 "test" spam notifications in conversations.jsonl (cleared). git index.lock blocks commits. +25 tests (15 worldview, 10 idea_generator), 4555 collected, 4442 passing (23 croniter + env-specific). **Touches:** `worldview.py`, `idea_generator.py`, `heartbeat.py`, `notification_formatter.py`, `conversational_router.py`, `autonomous_executor.py`, `tests/unit/test_worldview.py`, `tests/unit/test_idea_generator.py`, `logs/conversations.jsonl`, `data/dream_log.jsonl`.

**Session 202:** Phase 4 — interest-driven exploration + aesthetic taste development ("Becoming Someone"). (1) Interest exploration: `explore_interest()` in `idea_generator.py` picks highest-curiosity worldview interest, researches via model call, updates `last_explored`, logs to journal, seeds related interests via `connects_to`. Heartbeat Phase 6 (~20% of cycles, every 5th offset 2) shares findings via `format_exploration_sharing()` in notification_formatter. (2) Taste development: `develop_taste()` in `worldview.py` tracks cost-effectiveness by task type (research/writing/coding/analysis), model performance preferences, efficiency patterns. Called post-task from `_record_task_result()`. `get_taste_context()` injects learned preferences into PlanExecutor execution hints. +16 tests (7 taste, 5 exploration, 3 formatter, 1 classification), 4530 collected, 4417 passing (23 pre-existing croniter). **Touches:** `worldview.py`, `idea_generator.py`, `heartbeat.py`, `notification_formatter.py`, `autonomous_executor.py`, `tests/unit/test_worldview.py`, `tests/unit/test_idea_generator.py`, `tests/unit/test_notification_formatter.py`.

**Session 201:** Phase 3 — tone detection + opinion revision ("Becoming Someone"). (1) Tone detection: Router extracts `mood_signal` per message (busy/frustrated/excited/engaged/tired/playful), stored in UserModel (in-memory, last 10, 1hr decay), injected into router prompt + notification formatter for behavioral adjustment. `get_mood_context()` returns short instructions like "Jesse seems busy — keep responses short." (2) Opinion revision: `worldview.add_opinion()` detects significant position changes (confidence delta >= 0.3 or new_confidence >= 0.6), stores as `pending_revisions` in worldview.json. Heartbeat Phase 5.5 delivers up to 2/cycle via `format_opinion_revision()`. Cleared after delivery. +24 tests (11 mood, 11 revision, 2 router), 4471 passing (20 pre-existing env-specific). **Touches:** `conversational_router.py`, `user_model.py`, `notification_formatter.py`, `worldview.py`, `heartbeat.py`, `tests/unit/test_user_model.py`, `tests/unit/test_worldview.py`, `tests/unit/test_conversational_router.py`.

**Session 200:** Behavioral rules — memory that shapes action (Phase 2 of "Becoming Someone"). Created `src/core/behavioral_rules.py` (~410 lines): avoidance/preference rules crystallized from repeated task outcomes, keyword-based relevance matching, confidence decay, auto-pruning. Integrated into: `autonomous_executor.py` (`get_relevant_rules()` in `_build_hints()` + `process_task_outcome()` post-task), `heartbeat.py` (dream cycle extraction + periodic pruning). +33 tests, 4472 passing (20 pre-existing env-specific failures). Completes Phase 2 of "Becoming Someone" roadmap. **Touches:** `src/core/behavioral_rules.py` (new), `src/core/autonomous_executor.py`, `src/core/heartbeat.py`, `tests/unit/test_behavioral_rules.py` (new).

(Sessions 1–199 archived to `claude/archive/COMPLETED_WORK_SESSIONS_1_96.md` and earlier TODO.md entries.)
