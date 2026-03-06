# Archi — Changelog

This file summarizes notable changes per session so you can follow what happened without reading full session transcripts.

---

## Session 198 — 2026-03-06

**Journal Morning Orientation + Engagement Acknowledgment Window**

### Journal morning orientation integration

Wired `journal.get_orientation(days=3)` into the morning report pipeline so Archi can reference yesterday's context in morning messages.

- `reporting.send_morning_report()` now calls `journal.get_orientation(days=3)` and passes the result as `journal_context` to the notification formatter.
- `notification_formatter.format_morning_report()` accepts a new `journal_context` param. When non-empty, injects the context into the data dict and adds a prompt hint encouraging Archi to reference recent days.
- Graceful fallback: if journal import fails, morning report proceeds normally with empty context.

### Engagement acknowledgment window

Implements the 30-minute acknowledgment window for scheduled notify tasks, automatically populating `times_acknowledged` and `times_ignored` in scheduler stats.

- `heartbeat._fire_scheduled_task()` now records `{task_id, fired_at}` in `_pending_ack_tasks` when firing a notify action.
- `heartbeat.acknowledge_recent_tasks()` (public method) — called from `discord_bot.on_message()` whenever the user sends a message. Marks all within-window pending tasks as acknowledged via `scheduler.record_engagement()`.
- `heartbeat._check_engagement_timeouts()` — called every heartbeat tick (~5s). Marks tasks that exceeded the 30-minute window without user response as ignored.
- `discord_bot.on_message()` calls `_heartbeat.acknowledge_recent_tasks()` alongside existing `mark_activity()` and `reset_suggest_cooldown()`.

### Test results

+14 new tests (3 formatter, 2 reporting, 9 heartbeat), 4361 passing, 24 pre-existing env-specific failures.

---

## Session 197 — 2026-03-05

Daily journal system (Phase 1b). See TODO.md for details.

---

## Session 196 — 2026-03-05

**Scheduled Task System (Phase 1a)**

Implemented the core scheduled task system from `DESIGN_SCHEDULED_TASKS.md`. This gives Archi time-awareness — the ability to do things at specific times and on recurring schedules.

### New files

- `src/core/scheduler.py` (~280 lines) — Core module: `ScheduledTask` dataclass, load/save with atomic writes, CRUD operations (`create_task`, `modify_task`, `remove_task`, `list_tasks`), cron parsing via `croniter`, `check_due_tasks()` for heartbeat integration, engagement tracking (`record_engagement`, `get_ignored_tasks`), quiet hours support, fire rate limiting, and formatting helpers.
- `data/scheduled_tasks.json` — Empty seed file for persistent schedule state.
- `tests/unit/test_scheduler.py` — 54 unit tests covering: data model roundtrips, cron validation/parsing, persistence (load/save/corrupt/missing), all CRUD operations (including capacity limits and dedup), due task checking, advance/fire mechanics, engagement tracking and retirement logic, quiet hours, rate limiting, formatting, slugification, heartbeat integration mocks, and dispatcher handler integration.

### Modified files

- `requirements.txt` — Added `croniter>=1.3,<3.0` dependency for cron expression parsing.
- `src/core/heartbeat.py` — Added `_check_scheduled_tasks()` and `_fire_scheduled_task()` methods to the Heartbeat class. Scheduled tasks are checked every tick (~5s), not just during cycles. Supports `notify` (Discord DM) and `create_goal` action types. Respects quiet hours and fire rate limits.
- `src/interfaces/action_dispatcher.py` — Added 4 new handlers: `_handle_create_schedule`, `_handle_modify_schedule`, `_handle_remove_schedule`, `_handle_list_schedule`. Registered in `ACTION_HANDLERS`.
- `src/core/conversational_router.py` — Added `/schedule` and `/reminders` slash commands as fast-paths. Added `"schedule"` intent to the router system prompt so the model can classify natural scheduling language (e.g., "remind me to stretch every day at 4:15"). Added `_handle_schedule_command()` helper and `schedule` case in `_parse_router_response()`.
- `tests/unit/test_action_dispatcher.py` — Updated `test_all_handlers_registered` to include the 4 new schedule handlers.

### What it enables

Jesse can now say things like:
- "Remind me to stretch every day at 4:15" → creates a scheduled notify
- "Every Monday morning, give me a summary" → creates a scheduled goal
- "What reminders do I have?" → lists all schedules
- "Stop the stretch reminder" → removes a schedule
- "Change my morning reminder to 8:30" → modifies a schedule

Archi checks for due tasks every heartbeat tick and fires them automatically.

### What's still needed (Phase 1b+)

- Engagement tracking integration: the 30-minute acknowledgment window check (needs heartbeat tracking of recent notifications).
- Autonomous scheduling: dream cycle integration for Archi to self-create tasks based on detected patterns.
- Adaptive retirement: `idea_generator.py` integration to propose retiring ignored tasks.
- Daily journal system (Phase 1b of the "Becoming Someone" roadmap).

### Test results

54 new tests, all passing. 306 tests passing across all modified modules (scheduler + heartbeat + dispatcher + router). No regressions in the broader test suite (pre-existing env-specific failures unchanged).

---

## Session 195 — 2026-03-05

Heartbeat regression fix, 14 new tests, git commit identity fix. See TODO.md for details.

---

## Earlier sessions

See `claude/TODO.md` completed work section and `claude/archive/` for sessions 1–194.
