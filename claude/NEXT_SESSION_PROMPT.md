# Session 201 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 200)

**Behavioral rules — memory that shapes action (Phase 2 of "Becoming Someone", final item).**

Created `src/core/behavioral_rules.py` (~410 lines): avoidance and preference rules crystallized from repeated task outcomes. Keyword-based relevance matching, confidence decay (30-day window), automatic pruning, cap at 80 rules. Two rule types:
- **Avoidance:** "Don't use approach X for problem type Y" — formed after 3+ similar failures
- **Preference:** "Prefer approach X for problem type Y" — formed after 3+ similar successes

Integration:
- `autonomous_executor.py`: `get_relevant_rules()` injected into `_build_hints()` as "Context from past work" hints; `process_task_outcome()` called post-task to reinforce matching rules
- `heartbeat.py`: `extract_rules_from_experiences()` runs during dream cycle learning review to crystallize new rules; stale rules pruned every 10 cycles

**Test count:** ~4493 collected, ~4472 passing (with croniter); 20 pre-existing env-specific failures (mcp_client, project_context, project_sync, learning_system). +33 behavioral rules tests.

**Phase 2 of "Becoming Someone" is now complete:** worldview (session 199), self-reflection (session 199), behavioral rules (session 200).

---

## What to work on this session

### Priority 1: Live verification review

Deploy sessions 196-200 and check logs to verify the stack is working:
- **Scheduled tasks** — firing, tracking engagement, quiet hours respected
- **Journal entries** — task completions, conversations, dream cycles logged
- **Worldview** — opinions forming from task reflections, context injected in router
- **Behavioral rules** — rules appearing in `data/behavioral_rules.json` after repeated patterns
- **Morning reports** — referencing journal context and worldview
- **Adaptive retirement** — ignored tasks detected and handled
- **Self-reflection** — triggers after 50 dream cycles with sufficient journal entries

### Priority 2: Phase 3 — Social/emotional awareness

Start on tone detection and behavioral adjustment (DESIGN_BECOMING_SOMEONE.md section 6):
- Extract `mood_signal` from router model response
- Store in user_model (short-term mood tracking)
- Behavioral adjustment: shorter responses when busy/terse, more conversational when engaged
- **Files:** `src/core/conversational_router.py`, `src/core/user_model.py`

### Priority 3: Phase 3 — "I changed my mind"

Opinion revision and proactive communication (DESIGN_BECOMING_SOMEONE.md section 7):
- When worldview opinions change significantly (confidence delta > 0.3), flag for proactive notification
- Bring up changes with Jesse: "Hey, remember when I suggested X? I've been thinking about it..."
- Track revision history in worldview (already has `history` field)
- **Files:** `src/core/worldview.py`, `src/core/notification_formatter.py`, `src/core/heartbeat.py`

### Lower priority (carry forward)

- [ ] Search query broadening live verification
- [ ] Git post-modify commit failures live verification
- [ ] Behavioral rules live verification
- [ ] Phase 4: Initiative with taste, aesthetic development, long-term personal projects, meta-cognition

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- ~4493 collected, ~4472 passing (with croniter); 20 pre-existing env-specific failures (mcp_client, project_context, project_sync, learning_system).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
