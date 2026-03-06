# Session 213 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done last session (session 212)

**Post-restart check + "test" notification deep-dive + test fix.**

1. **Confirmed Archi still NOT restarted** — No new errors since Mar 6 10:41. All sessions 207-211 code changes still pending deploy. Scheduled tasks ("stretch", "drink water") still at `times_fired: 0`. Dream cycles running but 0 tasks completed.

2. **Deep "test" notification investigation** — Mapped full March 6 timeline: 25 "test" entries from 07:02–14:30, interleaved with legitimate notifications (work suggestions, exploration, etc.). Confirmed garbage guard (committed Mar 3, commit 7d9f51cd) has correct code — `_is_garbage_notification` returns True for "test". Yet entries still appear in conversations.jsonl via `_log_outbound` (only called after successful Discord send), confirming running process uses pre-guard code version. Root cause unresolvable without restart. Current guards are correct and sufficient.

3. **Fixed croniter-dependent test skip** — `test_validate_valid_cron` and `compute_next_run` tests now skip cleanly when `croniter` not installed, using `pytest.mark.skipif`. Previously caused 1 failure in environments without croniter.

**Test count:** 4594 passed, 18 skipped (with croniter installed; 3 additional tests skip without it).

**Touches:** `tests/unit/test_scheduler.py`.

---

## What to work on this session

### Priority 1: Verify post-restart behavior (if Archi has been restarted)

If Jesse has restarted Archi since session 212:
- Check `logs/errors/` for any new errors
- Verify "Invalid format string" is gone (fixed session 207, `format_friendly_time()`)
- Check `data/worldview.json` — should be growing with new preferences from chat tasks (session 209 fix)
- Check `data/journal/` for recent entries
- Verify dream cycles are executing tasks (check `data/dream_log.jsonl`)
- Verify "test" notification spam is gone from conversations.jsonl (garbage guard should catch it)
- Check `data/behavioral_rules.json` for continued growth
- Verify scheduled tasks ("stretch", "drink water") fire at their cron times

If NOT restarted yet: note this in the handoff and move to other priorities.

### Priority 2: Code quality items (all evaluated — low priority)

These have all been evaluated in prior sessions as "not worthwhile" for further splitting:
- `_filter_ideas` (78 lines) — clean single-loop filter logic
- `explore_interest` (64 lines) — mostly prompt
- `suggest_work` (63 lines) — main entry point orchestration
- `_brainstorm_fallback` (61 lines) — long prompt construction
- `_record_task_result()` ~68 lines — mixed concerns, each ~15 lines
- `on_message()` 369 lines — naturally branching handler
- `_handle_config_commands()` 161 lines — 7 handlers
- `execute_task()` ~127 lines — orchestration
- `run_diagnostics()` ~252 lines — script code

### Priority 3: Live verification items (from TODO.md)

Still waiting on restart + live cycles to verify:
- [ ] Interest-driven exploration
- [ ] Taste development
- [ ] Personal project proposals
- [ ] Meta-cognition (needs 50 dream cycles)
- [ ] Opinion revision delivery
- [ ] Adaptive retirement (needs >70% ignore rate over 14+ days)
- [ ] Autonomous scheduling
- [ ] Self-reflection (needs 50 dream cycles + >=5 journal entries in 7 days)
- [ ] Search query broadening
- [ ] Git post-modify commit failures

### If context budget allows

Check `claude/SELF_IMPROVEMENT.md` for proactive improvement directions.

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- 4594 passed, 18 skipped (test count as of session 212).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Never delete files** — log to `claude/PENDING_DELETIONS.md` instead.
- **Never attempt any action requiring interactive confirmation.**
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
