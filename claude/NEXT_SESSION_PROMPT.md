# Session 205 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 204)

**Post-Phase 4 quality pass + dream cycle health fix.**

(1) **Committed session 203 changes.** Removed stale `.git/index.lock` (0 bytes, from live Archi process). Committed all Phase 4 code (personal projects + meta-cognition).

(2) **Prompt bloat review — all clear.** Router context injections are well-bounded: worldview(400 chars) + meta(200) + project(200) + mood(~100) = ~900 chars max, all hard-capped. PlanExecutor hints have a 3000-char budget via `_cap_hints()` with priority-based trimming. No action needed.

(3) **Cost impact review — all clear.** New model calls per 10 cycles: exploration (2x, ~$0.005), personal project (1x, ~$0.002), scheduling proposals (1x, ~$0.003). Per 50 cycles: self-reflection + meta-cognition (2x, ~$0.01). Well under the $0.50/cycle cap. No action needed.

(4) **Dream cycle health fix.** Root cause: goals with failed tasks had dependent tasks stuck in PENDING state. `get_ready_tasks()` only returns PENDING tasks whose dependencies are COMPLETED — failed tasks never become completed, so dependent tasks could never start. `is_complete()` returns False (not all completed), so goals stay active but produce no work. The stale goal pruner's all-terminal check didn't catch them because PENDING isn't terminal. **Fix:** Added `_repair_blocked_tasks()` to `prune_stale_goals()` in `idea_generator.py`. BFS from failed tasks marks reachable pending dependents as BLOCKED. Now the all-terminal pruner catches and removes dead goals, clearing the way for new work. **File:** `src/core/idea_generator.py`.

**Test count:** 4472 collected, 4470 passing (excl env-specific: mcp_client, project_context, project_sync). +8 tests (4 repair, 4 updated prune).

---

## What to work on this session

### Priority 1: Restart and live verification

The live Archi process still needs a restart for sessions 196-204 features to activate. After restart:
- Monitor logs for: journal entries, worldview data, behavioral rules, scheduled tasks, exploration, taste, personal projects, meta-cognition
- Verify `_repair_blocked_tasks()` cleans up the 3 stuck goals (skill summarize, pet insurance, AI papers)
- **"test" notification spam** — verify garbage guard catches it after restart. If still happening, trace the source. **File:** `src/interfaces/discord_bot.py` (`_is_garbage_notification()`).
- Check that new goals get created and tasks actually execute

### Priority 2: Post-restart dream cycle health

If dream cycles still produce 0 tasks after restart + stuck goal cleanup:
- Check `data/goals_state.json` — are new goals being created?
- Check `suggest_work()` output — are suggestions being generated?
- Look at error logs for PlanExecutor failures
- Verify the Architect decomposition is producing valid tasks

### Priority 3: Live verification items (all pending)

All of these need live data to verify — check after a few dream cycles have run:
- Worldview opinions forming after tasks
- Behavioral rules appearing in `data/behavioral_rules.json`
- Exploration entries in journal
- Taste preferences in worldview.json
- Personal project proposals
- Meta-cognition observations after 50-cycle self-reflection
- Tone detection in router responses
- Opinion revision delivery
- Scheduled task engagement tracking
- Adaptive retirement of ignored tasks

### Lower priority (carry forward)

- [ ] Search query broadening live verification
- [ ] Git post-modify commit failures live verification
- [ ] Test count discrepancy between Linux and Windows

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- 4472 collected, 4470 passing (excl env-specific).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Never delete files** — log to `claude/PENDING_DELETIONS.md` instead.
- **Never attempt any action requiring interactive confirmation.**
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
