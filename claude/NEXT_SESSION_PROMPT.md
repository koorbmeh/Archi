# Session 203 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 202)

**Phase 4 of "Becoming Someone": interest-driven exploration + aesthetic taste development.**

(1) **Interest-driven exploration.** `explore_interest()` in `idea_generator.py` picks the highest-curiosity worldview interest, researches it via model call, updates `last_explored`, logs to journal as `exploration` entry, and seeds related interests from `connects_to`. Heartbeat Phase 6 (~20% of cycles, every 5th offset 2) shares findings via `format_exploration_sharing()` in notification_formatter with personality-rich commentary.

(2) **Aesthetic/taste development.** `develop_taste()` in `worldview.py` analyzes each task's success, cost, step count, model used, and verification status. Classifies task type (research/writing/coding/analysis) and records preferences in three worldview domains: `taste_efficiency`, `taste_caution`, `taste_model`. Called from `_record_task_result()` after every task. `get_taste_context()` injects learned preferences into PlanExecutor execution hints via `_gather_execution_hints()`.

**Test count:** ~4530 collected, ~4417 passing (excl croniter); 23 pre-existing croniter + ~20 env-specific failures. +16 new tests (7 taste, 5 exploration, 3 formatter, 1 classification).

**Phase 4 partial (sections 4, 9) is done.** Remaining Phase 4 items: section 10 (long-term personal projects) and section 11 (meta-cognition).

---

## What to work on this session

### Priority 1: Live verification review

Deploy sessions 196-202 and check logs to verify the full stack is working:
- **Scheduled tasks** — firing, tracking engagement, quiet hours respected
- **Journal entries** — task completions, conversations, dream cycles, mood signals, explorations logged
- **Worldview** — opinions forming from task reflections, context injected in router
- **Behavioral rules** — rules appearing in `data/behavioral_rules.json` after repeated patterns
- **Tone detection** — `mood_signal` in router responses, mood context in prompts
- **Opinion revisions** — `pending_revisions` in worldview.json after opinion changes
- **Interest exploration** — triggers every 5th cycle, `last_explored` updates, exploration journal entries
- **Taste development** — `taste_*` domains in worldview.json preferences, context in execution hints
- **Morning reports** — referencing journal context and worldview
- **Adaptive retirement** — ignored tasks detected and handled
- **Self-reflection** — triggers after 50 dream cycles with sufficient journal entries

### Priority 2: Phase 4 — Long-term personal projects

(DESIGN_BECOMING_SOMEONE.md section 10):
- Things Archi pursues because *he* wants to, not because Jesse asked
- Emerge from interests in the worldview system
- Given a small slice of dream cycle time
- Can be shared with Jesse or kept internal until useful
- **Files:** `src/core/heartbeat.py`, `src/core/idea_generator.py`, `src/core/worldview.py`

### Priority 3: Phase 4 — Meta-cognition

(DESIGN_BECOMING_SOMEONE.md section 11):
- Archi thinks about his own thinking patterns
- Notices tendencies (over-estimating complexity, repeating same solutions)
- Adjusts approach based on self-observation
- Feeds back into self-reflection and worldview
- **Files:** `src/core/journal.py`, `src/core/worldview.py`, `src/core/heartbeat.py`

### Lower priority (carry forward)

- [ ] Search query broadening live verification
- [ ] Git post-modify commit failures live verification
- [ ] All Phase 2-4 live verification items (see TODO.md)

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- ~4530 collected, ~4417 passing (excl croniter); 23 pre-existing croniter + ~20 env-specific failures (mcp_client, project_context, project_sync).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
