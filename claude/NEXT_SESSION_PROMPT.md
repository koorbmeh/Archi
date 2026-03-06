# Session 200 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 199)

**Phase 2 of "Becoming Someone" + scheduled task next phases.** Four features implemented and fully tested:

1. **Worldview system** — `src/core/worldview.py` (~490 lines): opinions, preferences, interests with confidence/strength scores, evidence tracking, decay/pruning, thread-safe CRUD. `get_worldview_context()` injects into conversational router system prompt. `reflect_on_task()` called from autonomous_executor after each task (lightweight keyword-based, no model cost). Decay runs every 10 dream cycles alongside journal pruning.

2. **Self-reflection** — `journal.py` `generate_self_reflection()`: weekly model-based analysis of journal entries, stores reflection as entry, calls `_update_worldview_from_reflection()` to extract new opinions/interests via model. Triggered every 50 dream cycles (heartbeat Phase 5). Simple fallback without model call.

3. **Adaptive retirement** — `idea_generator.py` `check_retirement_candidates()`: queries `scheduler.get_ignored_tasks()` (>70% ignore rate over 14+ days). Archi-created tasks disabled silently; user-created tasks proposed for retirement via Discord. Runs every 10 dream cycles (heartbeat Phase 0.95).

4. **Autonomous scheduling** — `idea_generator.py` `suggest_scheduled_tasks()`: gathers evidence from journal + conversation logs, uses model to detect recurring patterns, proposes schedules. Once-per-day cooldown. Runs every 10 dream cycles offset by 7 (heartbeat Phase 2.7).

**Test count:** 4409 passing (Linux/Cowork), 5 pre-existing env-specific failures (project_context, project_sync, mcp_client).

---

## What to work on this session

### Priority 1: Memory shaping behavior (Phase 2 continued)

Behavioral rules derived from repeated successes/failures, injected into PlanExecutor hints:
- Extend `learning_system.py` to detect repeated failure/success patterns
- Generate behavioral rules: "Don't use approach X for problem type Y — failed 3 times"
- Inject rules into PlanExecutor prompts as hints (similar to Architect spec hints)
- See `claude/DESIGN_BECOMING_SOMEONE.md` section 3
- **Files:** `src/core/learning_system.py`, `src/core/plan_executor/executor.py`

### Priority 2: Live verification of session 196-199 features

All the scheduled task and "Becoming Someone" features need live verification:
- Engagement ack window (session 198) — needs live Discord test
- Worldview system (session 199) — confirm `data/worldview.json` populates after tasks
- Self-reflection (session 199) — confirm journal entries appear after 50 cycles
- Adaptive retirement (session 199) — confirm ignored tasks get retirement proposals
- Autonomous scheduling (session 199) — confirm pattern detection produces proposals
- **Action:** Check logs after next deploy for evidence of these features running

### Priority 3: Phase 3 — Social/emotional awareness

From `DESIGN_BECOMING_SOMEONE.md`:
- Tone detection: analyze Jesse's message patterns for mood signals
- Behavioral adjustment: shorter responses when busy, more conversational when engaged
- Memory of emotional context in user_model
- **Files:** `src/core/conversational_router.py`, `src/core/user_model.py`

### Lower priority (carry forward)

- [ ] Search query broadening live verification
- [ ] Git post-modify commit failures live verification
- [ ] "I changed my mind" — opinion revision + proactive communication (Phase 3)
- [ ] Initiative with taste — curiosity-driven exploration (Phase 4)

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- ~4409 unit tests passing in Cowork/Linux (~5 pre-existing env-specific failures).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
