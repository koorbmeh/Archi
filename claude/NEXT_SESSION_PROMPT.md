# Session 198 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 197)

**Daily journal system (Phase 1b of "Becoming Someone" roadmap).** Created `src/core/journal.py` (~220 lines):
- Daily JSON files in `data/journal/YYYY-MM-DD.json` with timestamped entries, type/content/metadata, summary counters
- Entry types: `task_completed`, `conversation`, `observation`, `thing_learned`, `dream_cycle`, `mood_signal`, `reflection`
- Query API: `get_recent_entries(days, type)`, `get_day_summary(day)`, `get_orientation(days)`
- 30-day auto-pruning (runs alongside heartbeat file cleanup)
- Integrated at 3 points: task completions (autonomous_executor), conversations (message_handler), dream cycles (heartbeat)
- +32 tests (all passing)

Also committed session 196's scheduler changes (which were uncommitted): scheduler.py, heartbeat integration, router/dispatcher changes, 54 tests.

**Test count:** 4347 passing, 23 pre-existing env-specific failures (Linux/Cowork).

---

## What to work on this session

### Priority 1: Journal morning orientation integration

The journal system exists but isn't yet used to give Archi context about his recent days. Wire `journal.get_orientation()` into the morning report flow:
- In `reporting.py` → `send_morning_report()`, call `get_orientation(days=3)` and include the output in the morning report prompt so Archi can reference yesterday's context.
- Optionally inject recent journal context into the router system prompt for continuity during conversations.
- **Files:** `src/core/reporting.py`, possibly `src/core/conversational_router.py`

### Priority 2: Engagement acknowledgment window (scheduler)

The scheduler tracks `times_acknowledged` and `times_ignored` but doesn't actually populate them automatically. Implement the 30-minute acknowledgment window:
- When a `notify` task fires, record the task_id and fire timestamp in heartbeat state
- On next user message within 30 minutes, call `scheduler.record_engagement(task_id, acknowledged=True)`
- On heartbeat tick after 30 minutes with no response, call `record_engagement(task_id, acknowledged=False)`
- **Files:** `src/core/heartbeat.py`, `src/interfaces/discord_bot.py`

### Priority 3: Begin Phase 2 — Worldview system

If time remains, start on `src/core/worldview.py`:
- `data/worldview.json` with opinions, preferences, interests
- CRUD operations + query helpers
- Integration with dream cycle (post-task reflection: "did this change my views?")
- See `claude/DESIGN_BECOMING_SOMEONE.md` for design

### Lower priority (carry forward)

- [ ] Search query broadening live verification
- [ ] Git post-modify commit failures live verification
- [ ] Adaptive retirement in idea_generator.py
- [ ] Autonomous scheduling (dream cycle pattern detection)

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- ~4347 unit tests passing in Cowork/Linux (~23 pre-existing env-specific failures).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
