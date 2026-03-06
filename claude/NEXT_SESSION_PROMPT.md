# Session 199 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 198)

**Journal morning orientation + engagement acknowledgment window.** Two features:

1. **Journal morning orientation integration** — `reporting.send_morning_report()` now calls `journal.get_orientation(days=3)` and passes the result to `notification_formatter.format_morning_report()` as `journal_context`. The formatter injects it into the prompt with a continuity hint so Archi can reference yesterday's context in morning messages. Graceful fallback if journal import fails.

2. **Engagement acknowledgment window** — Implements the 30-minute ack window for scheduled notify tasks. `_fire_scheduled_task()` records `{task_id, fired_at}` in `_pending_ack_tasks`. `acknowledge_recent_tasks()` (called from `discord_bot.on_message()`) marks within-window tasks as acknowledged. `_check_engagement_timeouts()` (every tick) marks expired tasks as ignored. This populates `times_acknowledged` and `times_ignored` in scheduler stats automatically.

**Test count:** 4361 passing, 24 pre-existing env-specific failures (Linux/Cowork). +14 new tests.

---

## What to work on this session

### Priority 1: Begin Phase 2 — Worldview system

Start on `src/core/worldview.py`:
- `data/worldview.json` with opinions, preferences, interests derived from actual experiences
- CRUD operations + query helpers
- Integration with dream cycle (post-task reflection: "did this change my views?")
- See `claude/DESIGN_BECOMING_SOMEONE.md` for design
- **New file:** `src/core/worldview.py`, `data/worldview.json`

### Priority 2: Adaptive retirement in idea_generator.py

`idea_generator.py` should call `scheduler.get_ignored_tasks()` during dream cycles and propose/auto-retire ignored tasks:
- User-created tasks: propose retirement to Jesse via Discord notification
- Archi-created tasks: disable silently with a notification
- **Files:** `src/core/idea_generator.py`, `src/core/heartbeat.py`

### Priority 3: Autonomous scheduling (dream cycle)

Archi notices patterns and proposes scheduled tasks:
- Integration in `idea_generator.py`
- Non-notification tasks created silently; notification tasks proposed to Jesse first
- **Files:** `idea_generator.py`, `scheduler.py`

### Lower priority (carry forward)

- [ ] Search query broadening live verification
- [ ] Git post-modify commit failures live verification
- [ ] Memory shaping behavior (Phase 2) — behavioral rules from repeated successes/failures
- [ ] Self-reflection (Phase 2) — weekly deep reflection during dream cycles

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- ~4361 unit tests passing in Cowork/Linux (~24 pre-existing env-specific failures).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
