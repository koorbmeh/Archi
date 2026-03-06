# Session 204 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 203)

**Phase 4 of "Becoming Someone": long-term personal projects + meta-cognition. Also log cleanup and live verification review.**

(1) **Long-term personal projects.** `add_personal_project()`, `update_personal_project()`, `get_project_context()` in `worldview.py`. `propose_personal_project()` + `work_on_personal_project()` in `idea_generator.py`. Projects emerge from high-curiosity explored interests (curiosity >= 0.5, already explored). Model decides whether sustained work is warranted. Progress tracked with bounded notes (15 max), session counts. Heartbeat Phase 6.5 (every 10th cycle, offset 4): if no active projects → propose; else → work on most-neglected. Share-worthy findings sent via `format_project_sharing()` in notification_formatter. Cap: 10 projects.

(2) **Meta-cognition.** `add_meta_observation()`, `update_meta_adjustment()`, `get_meta_context()` in `worldview.py`. `generate_meta_cognition()` in `idea_generator.py` gathers evidence from behavioral rules, taste preferences, journal entries, existing observations — needs at least 2 sources. Model identifies meta-patterns (estimation, approach, communication, efficiency, general) and proposes adjustments. Runs alongside weekly self-reflection (Phase 5, every 50 cycles). Observations stored in worldview.json under `meta_observations` (cap 20, deduplicated). Context injected into both router system prompt and PlanExecutor execution hints.

(3) **Live verification review.** Checked logs — no `worldview.json`, `behavioral_rules.json`, or `journal/` files exist yet. Features from sessions 196-202 haven't been activated (process needs restart to load new code). Found 91 "test" notification spam entries in conversations.jsonl (cleared). Dream cycles running but completing 0 tasks. git index.lock prevents commits from Cowork.

(4) **Log cleanup.** Removed 91 "test" spam entries from conversations.jsonl. Trimmed dream_log.jsonl to last 10 entries. Could not commit due to git index.lock.

**Test count:** ~4555 collected, ~4442 passing (excl croniter); 23 pre-existing croniter + env-specific failures. +25 new tests (15 worldview, 10 idea_generator).

**Phase 4 is complete.** All 4 sections (exploration, taste, personal projects, meta-cognition) implemented and tested.

---

## What to work on this session

### Priority 1: Restart and live verification

The live Archi process needs a restart for all sessions 196-203 features to activate. After restart:
- Clear the git index.lock so commits work
- Commit session 203 changes (not committed due to lock)
- Monitor logs for: journal entries, worldview data, behavioral rules, scheduled tasks, exploration, taste, personal projects, meta-cognition
- **"test" notification spam** — verify garbage guard catches it after restart. If still happening, trace the source. **File:** `src/interfaces/discord_bot.py` (`_is_garbage_notification()`).

### Priority 2: Post-Phase 4 quality pass

With all "Becoming Someone" phases complete, review the overall system for:
- **Integration coherence** — do worldview opinions, behavioral rules, taste preferences, meta-observations, and personal projects all interact smoothly?
- **Prompt bloat** — check the router system prompt and PlanExecutor hints aren't getting too long from all the injected contexts (worldview + taste + meta + project + mood + user model)
- **Cost impact** — each new model call (exploration, project work, meta-cognition, self-reflection) costs money. Verify the cycle frequencies don't overshoot the $0.50/cycle budget.

### Priority 3: Dream cycle health

Dream log shows 0 tasks completed per cycle over multiple days. After restart:
- Check if goals are being decomposed and tasks executed
- Look at `data/goals_state.json` — 4 goals exist but none seem to be processing
- If tasks are stalling, check PlanExecutor error patterns

### Lower priority (carry forward)

- [ ] Search query broadening live verification
- [ ] Git post-modify commit failures live verification
- [ ] All Phase 2-4 live verification items (see TODO.md)
- [ ] Test count discrepancy between Linux and Windows

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- ~4555 collected, ~4442 passing (excl croniter); 23 pre-existing croniter + env-specific failures.
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Never delete files** — log to `claude/PENDING_DELETIONS.md` instead.
- **Never attempt any action requiring interactive confirmation.**
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
