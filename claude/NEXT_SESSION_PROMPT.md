# Session 202 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 201)

**Phase 3 of "Becoming Someone": tone detection + opinion revision.**

(1) **Tone detection / mood tracking.** Router now extracts `mood_signal` per message (busy, frustrated, excited, engaged, tired, playful). Stored in `UserModel._mood_history` (in-memory, last 10, 1hr decay). `get_mood_context()` builds behavioral adjustment hints injected into the router prompt and notification formatter. When Jesse seems busy: "keep responses short." When excited: "match the energy." Also logged to journal as `mood_signal` entry.

(2) **"I changed my mind" — opinion revision.** `worldview.add_opinion()` now detects significant position changes (different text + confidence delta >= 0.3 or new_confidence >= 0.6) and flags them as `pending_revisions` in `data/worldview.json`. Heartbeat Phase 5.5 delivers up to 2 revisions per cycle via `format_opinion_revision()` → Discord DM, then clears them. Proactive: "Hey, I've been rethinking my take on X..."

**Test count:** ~4514 collected, ~4471 passing (excl croniter); 20 pre-existing env-specific failures. +24 new tests (11 mood, 11 revision, 2 router).

**Phase 3 partial (sections 6-7) is done.** Remaining Phase 3 item: section 6 memory of emotional context ("Last time Jesse asked about X, he was frustrated") — could be added later by persisting mood alongside conversation journal entries. Not critical.

---

## What to work on this session

### Priority 1: Live verification review

Deploy sessions 196-201 and check logs to verify the full stack is working:
- **Scheduled tasks** — firing, tracking engagement, quiet hours respected
- **Journal entries** — task completions, conversations, dream cycles, mood signals logged
- **Worldview** — opinions forming from task reflections, context injected in router
- **Behavioral rules** — rules appearing in `data/behavioral_rules.json` after repeated patterns
- **Tone detection** — `mood_signal` in router responses, mood context in prompts
- **Opinion revisions** — `pending_revisions` in worldview.json after opinion changes
- **Morning reports** — referencing journal context and worldview
- **Adaptive retirement** — ignored tasks detected and handled
- **Self-reflection** — triggers after 50 dream cycles with sufficient journal entries

### Priority 2: Phase 4 — Initiative with taste

Start on curiosity-driven exploration (DESIGN_BECOMING_SOMEONE.md section 4):
- **Interest-driven exploration time.** Allocate ~20% of dream cycles to exploring something interesting rather than productive work.
- Use worldview interests (curiosity_level) to pick exploration topics.
- Share findings with personality: "I was looking into that API you mentioned and went down a rabbit hole..."
- **Files:** `src/core/heartbeat.py`, `src/core/worldview.py`, `src/core/idea_generator.py`

### Priority 3: Phase 4 — Aesthetic / taste development

(DESIGN_BECOMING_SOMEONE.md section 9):
- Track which approaches, query patterns, and communication styles work best
- Develop preferences for model performance (which model handles which task type best)
- Use actual QA results + cost_tracker data to inform aesthetic judgments
- **Files:** `src/core/worldview.py`, `src/core/learning_system.py`

### Lower priority (carry forward)

- [ ] Search query broadening live verification
- [ ] Git post-modify commit failures live verification
- [ ] Behavioral rules live verification
- [ ] Tone detection live verification
- [ ] Opinion revision live verification
- [ ] Phase 4: Long-term personal projects, meta-cognition

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- ~4514 collected, ~4471 passing (excl croniter); 20 pre-existing env-specific failures (mcp_client, project_context, project_sync).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
