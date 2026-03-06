# Session 207 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 206)

**Import cleanup + test coverage expansion.** Minimal code changes to `src/`.

(1) **Unused imports removed:** `timedelta` from `worldview.py` and `behavioral_rules.py`, `Dict` from `worldview.py` and `scheduler.py`. Verified `Dict` IS used in `behavioral_rules.py` (line 306, function signature) — agent analysis was wrong, caught by tests.

(2) **25 new edge case tests added:**
- `test_worldview.py` (+15): load fills missing keys, save atomicity, personal project cap prioritizes active, preference cap, interest decay removes dead, model name parsing with slashes/colons, failed model records struggle, no-model no-pref, taste context max_chars, reflect-on-task no-match/reinforce/weaken, revision not flagged for new opinion, revision flagged on high confidence, clear revision case-insensitive, pending revisions returns copy.
- `test_behavioral_rules.py` (+10): find_matching_rule picks best overlap, process outcome reinforces avoidance/preference, empty description returns None, load fills missing keys, cluster with minimal common keywords, mixed success/failure separate clusters, prune preserves recent low-strength, prune removes decayed old rules.

(3) **Bug found during testing:** `_prune()` in `behavioral_rules.py` checks `last_reinforced` field (not `last_updated`). Important to use the right field name when creating rules programmatically.

**Test count:** 4568 passed, 18 skipped (up from 4543).

---

## What to work on this session

### Priority 1: Restart and live verification (STILL PENDING — session 204+)

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
- [ ] Test count discrepancy between Linux (~4568) and Windows (~1399, session 125 — very stale)

### If context budget allows after priorities

Check `claude/SELF_IMPROVEMENT.md` for proactive improvement directions. Potential work:
- Refactor long functions in `idea_generator.py` (8 functions over 60 lines, `generate_meta_cognition()` is 133 lines — most concerning)
- Add integration tests for Phase 3/4 features
- Look into self-improvement directions from SELF_IMPROVEMENT.md

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- 4568 passed, 18 skipped (test count as of session 206).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Never delete files** — log to `claude/PENDING_DELETIONS.md` instead.
- **Never attempt any action requiring interactive confirmation.**
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
