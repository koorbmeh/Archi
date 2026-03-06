# Session 200 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 199)

**Phase 2 of "Becoming Someone" + scheduled task enhancements — four major features:**

1. **Worldview system** (`src/core/worldview.py`, ~490 lines) — evolving opinions, preferences, and interests derived from actual experience. Thread-safe CRUD, confidence decay, interest staleness decay, auto-pruning. Integrated into: `conversational_router.py` (system prompt injection via `get_worldview_context()`), `autonomous_executor.py` (lightweight post-task reflection via `reflect_on_task()`), `heartbeat.py` (decay prune every 10 cycles).

2. **Self-reflection** (`src/core/journal.py`, `generate_self_reflection()`) — weekly model-based introspection over recent journal entries. Stores reflection as journal entry, extracts worldview updates (opinions + interests) via model. Simple fallback without model. Triggered every 50 dream cycles (heartbeat Phase 5).

3. **Adaptive retirement** (`src/core/idea_generator.py`, `check_retirement_candidates()`) — queries `scheduler.get_ignored_tasks()` for >70% ignore rate over 14+ days. Auto-retires Archi-created tasks, proposes user-created for retirement via Discord. Runs every 10 dream cycles.

4. **Autonomous scheduling** (`src/core/idea_generator.py`, `suggest_scheduled_tasks()`) — analyzes journal + conversation patterns via model, proposes scheduled tasks. Notify tasks proposed to user, create_goal tasks created silently. Once-per-day cooldown. Runs every 10 dream cycles (offset 7).

**Test count:** ~4460 collected, ~4409 passing (with croniter), 27 pre-existing env-specific failures (croniter, project_context, project_sync, mcp_client). +42 worldview, +7 journal self-reflection, +18 idea_generator tests.

**Note:** Session 199 code was committed but wrap-up was incomplete (git lock file issue). Session 199b completed the wrap-up: added live verification TODO items, verified all docs current.

---

## What to work on this session

### Priority 1: Memory shaping behavior (Phase 2)

The last remaining Phase 2 item. Behavioral rules derived from repeated successes/failures, injected into PlanExecutor hints.

- Extend `src/core/learning_system.py` — after recording N similar failures, auto-generate an avoidance rule: "Don't use approach X for problem type Y — failed N times."
- After N successful approaches, generate a preference rule: "Prefer approach X for problem type Y."
- Store rules in a new structure (possibly `data/behavioral_rules.json` or extend `worldview.json`).
- Inject relevant rules into `src/core/plan_executor/executor.py` hint building — query rules matching the current task type and add as hints.
- See `DESIGN_BECOMING_SOMEONE.md` section 3 ("Memory That Shapes Behavior").
- **Files:** `src/core/learning_system.py`, `src/core/plan_executor/executor.py` (protected — needs explicit justification), possibly new `src/core/behavioral_rules.py`.

### Priority 2: Live verification review

Check logs after next deploy to verify session 196-199 features are working:
- Scheduled tasks firing and tracking engagement
- Journal entries being created
- Worldview forming from task reflections
- Morning reports referencing journal context

### Lower priority (carry forward)

- [ ] Search query broadening live verification
- [ ] Git post-modify commit failures live verification
- [ ] Phase 3: Social/emotional awareness (tone detection, behavioral adjustment)
- [ ] Phase 3: "I changed my mind" (opinion revision + proactive communication)

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- ~4460 unit tests collected, ~4409 passing (with croniter); 27 pre-existing env-specific failures.
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- **plan_executor/executor.py is protected** — modifying it to inject behavioral rules needs explicit justification in commit message.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
