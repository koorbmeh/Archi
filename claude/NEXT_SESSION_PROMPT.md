# Session 214 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done last session (session 213)

**Post-restart verification + "test" defense-in-depth + scheduled task payload fix.**

1. **Confirmed Archi restarted at 13:53 Mar 6.** Post-restart verification: no "Invalid format string" errors (session 207 fix working), worldview populating (3 preferences, 2 interests), journal logging correctly (37 entries, mood signals working), behavioral rules growing (143 avoidance evidence, 110 preference evidence). Dream cycles running but 0 tasks completed (no pending work).

2. **"test" spam continues after restart** — deep investigation confirmed garbage guard code is correct and tested, but "test" entries still appear in conversations.jsonl from the new process (5 entries 14:28-14:41, all post-restart). Defense-in-depth fix applied: added garbage guard inside `_log_outbound` itself + upgraded guard log to WARNING. Root cause unclear — possibly zombie old process or unknown code path.

3. **Scheduled task payload fix** — found that "Drink water" (11 chars, 2 words) was blocked by `_is_garbage_notification` (<15 chars AND ≤2 words). Fixed: `_fire_scheduled_task` now wraps short payloads (<20 chars) with "Reminder: " prefix. Also found `times_fired: 0` for both tasks — needs live verification.

**Test count:** 4594 passed, 18 skipped (with croniter installed; unchanged from session 212).

**Touches:** `src/interfaces/discord_bot.py`, `src/core/heartbeat.py`.

---

## What to work on this session

### Priority 1: Verify "test" defense-in-depth fix

After this session's commit is deployed (restart), check:
- Does `_log_outbound` block "test" entries? Look for WARNING logs: `_log_outbound blocked garbage: 'test'`
- Do "test" entries still appear in conversations.jsonl?
- If they DO appear, the "test" is bypassing BOTH guards — suggests a code path not going through `send_notification` at all. Check if a second Python process is running (`tasklist | findstr python` on Windows).

### Priority 2: Verify scheduled tasks fire

After restart:
- Check `data/scheduled_tasks.json` — do `times_fired` increment?
- Check if "Reminder: Drink water" and "Time to stretch." appear in Discord
- If `times_fired` stays 0, check if croniter is installed (`pip show croniter` on Windows). The `advance_task` function needs croniter to compute next_run_at. If croniter is missing, tasks fire but don't advance, causing infinite re-fire attempts.

### Priority 3: Worldview system growth verification

The worldview bootstrapped (3 preferences, 2 interests) but needs more data:
- After more dream cycles, check for new opinions from `_lightweight_reflection`
- Verify `explore_interest` fires (every 5th cycle, offset 2) — check for `last_explored` updates on interests
- Verify taste preferences grow from task completions

### Priority 4: Code quality items (all evaluated — low priority)

Same as session 213 starter — `_record_task_result()`, `on_message()`, `_handle_config_commands()`, `execute_task()`, `run_diagnostics()` all evaluated as not worthwhile for further splitting.

### If context budget allows

Check `claude/SELF_IMPROVEMENT.md` for proactive improvement directions.

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- 4594 passed, 18 skipped (test count as of session 213).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Never delete files** — log to `claude/PENDING_DELETIONS.md` instead.
- **Never attempt any action requiring interactive confirmation.**
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
