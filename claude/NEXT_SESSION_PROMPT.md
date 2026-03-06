# Session 206 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 205)

**Code review + README update + test verification.** No code changes to `src/`.

(1) **Test suite:** 4543 passed, 18 skipped. Increase from 4472 (session 204) is from `croniter` being available in the test environment — previously-failing scheduler tests now pass.

(2) **"Test" notification spam bug:** Investigated. The garbage guard in `discord_bot.py` (`_is_garbage_notification()`) correctly catches "test" as a single word under 20 chars. The 91 spam messages were from a stale process predating the guard code. **No code fix needed — just needs restart verification.**

(3) **Code quality review:** Reviewed heartbeat dream cycle phase offsets (no collisions, well-distributed), worldview.py, behavioral_rules.py, journal.py, idea_generator.py. All clean — no issues found.

(4) **README updated:** Added missing features from sessions 196-204 (scheduled tasks, personality/growth, curiosity/projects, social awareness).

(5) **claude/ docs updated:** Test count in ARCHITECTURE.md corrected to 4543. SESSION_CONTEXT incremented to 205. TODO.md updated, session 195 archived.

**Test count:** 4543 passed, 18 skipped.

---

## What to work on this session

### Priority 1: Restart and live verification (STILL PENDING)

The live Archi process still needs a restart for sessions 196-204 features to activate. This has been the top priority since session 204 but can't be done from a Cowork session. After restart:
- Monitor logs for: journal entries, worldview data, behavioral rules, scheduled tasks, exploration, taste, personal projects, meta-cognition
- Verify `_repair_blocked_tasks()` cleans up any stuck goals
- **"test" notification spam** — verify garbage guard catches it after restart. **File:** `src/interfaces/discord_bot.py` (`_is_garbage_notification()`).
- Check that new goals get created and tasks actually execute

### Priority 2: Live verification items (all pending since sessions 199-203)

All of these need live data to verify — check after a few dream cycles have run:
- Worldview opinions forming after tasks — `data/worldview.json`
- Behavioral rules appearing — `data/behavioral_rules.json`
- Exploration entries in journal — `data/journal/`
- Taste preferences in worldview.json
- Personal project proposals
- Meta-cognition observations after 50-cycle self-reflection
- Tone detection in router responses
- Opinion revision delivery
- Scheduled task engagement tracking
- Adaptive retirement of ignored tasks

### Priority 3: Carry-forward items

- [ ] Search query broadening live verification — `src/core/plan_executor/actions.py`
- [ ] Git post-modify commit failures live verification — `src/utils/git_safety.py`
- [ ] Test count discrepancy between Linux (~4543) and Windows (~1399, session 125 — very stale)

### If context budget allows after priorities

Check `claude/SELF_IMPROVEMENT.md` for proactive improvement directions. The codebase is in good shape — all tests passing, code quality is solid. Potential directions:
- Add integration tests for the newer Phase 3/4 features
- Look into any new self-improvement opportunities from SELF_IMPROVEMENT.md

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- 4543 passed, 18 skipped (test count as of session 205).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Never delete files** — log to `claude/PENDING_DELETIONS.md` instead.
- **Never attempt any action requiring interactive confirmation.**
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
