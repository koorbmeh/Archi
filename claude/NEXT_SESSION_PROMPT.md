# Session 215 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done last session (session 214)

**Verification pass — all priority items blocked on restart.**

1. **Session 213 code NOT deployed.** The 13:53 Mar 6 restart used code through session 211 (committed 13:47). Session 213 was committed at 15:24 — 1.5 hours AFTER the restart. The `_log_outbound` garbage guard, scheduled task "Reminder: " prefix, and test hardening are all still pending deployment.

2. **"test" spam investigation (session 5 of trying).** 28 entries on Mar 6, all with `cost_usd: 0`. Traced every code path exhaustively: `_call_formatter` rejects < 10 chars, `send_notification` garbage guard blocks "test", `_is_garbage_notification("test")` returns True at multiple check points. Yet "test" still appears in `_log_outbound` output. Root cause unknown. The session 213 `_log_outbound` guard should finally catch it once deployed. If "test" STILL appears after deploying session 213, there's a truly undiscovered code path writing to conversations.jsonl.

3. **Scheduled tasks `times_fired: 0`.** Code analysis confirms `advance_task` handles missing croniter gracefully (fallback to +1h). `check_due_tasks` doesn't need croniter. Most likely: FUSE mount snapshot from before fire times.

4. **Worldview/behavioral rules unchanged.** 3 prefs, 2 interests, 1 avoidance (143 evidence), 1 preference (110 evidence). Expected — no new tasks completed since session 213.

5. **"Invalid format string" confirmed fixed** — no errors after the 13:53 restart. Session 207's `format_friendly_time()` fix is working.

**Test count:** 4594 passed, 18 skipped (unchanged from session 213).

**No code changes or file touches.**

---

## What to work on this session

### Priority 1: Check if Archi has been restarted since session 213

**This is the gating question.** Check `logs/errors/` for new entries, or `logs/conversations.jsonl` for entries after 14:41 on Mar 6.

- **If restarted** with session 213 code: verify all the items below.
- **If NOT restarted**: recommend Jesse restart Archi to deploy sessions 207-213 fixes. Then skip to Priority 5.

### Priority 2: Verify "test" defense-in-depth fix (if restarted)

- Look for WARNING log: `_log_outbound blocked garbage: 'test'`
- Check if "test" entries still appear in conversations.jsonl after restart
- If they DO appear despite both guards, something is writing to conversations.jsonl directly (not through `_log_outbound` or `send_notification`). Check `tasklist | findstr python` for dual processes.

### Priority 3: Verify scheduled tasks fire (if restarted)

- Check `data/scheduled_tasks.json` — do `times_fired` increment?
- "Drink water" should appear as "Reminder: Drink water" (session 213 prefix fix)
- "Time to stretch." (16 chars, 3 words) should pass garbage guard directly
- If `times_fired` stays 0, verify croniter: `pip show croniter`

### Priority 4: Worldview system growth verification (if restarted)

- After more dream cycles, check for new opinions from `_lightweight_reflection`
- Verify `explore_interest` fires (every 5th cycle, offset 2)
- Verify taste preferences grow from task completions
- Chat-mode reflection (session 209 fix) should generate worldview updates from user conversations

### Priority 5: Proactive improvement work (if context budget allows)

Check `claude/SELF_IMPROVEMENT.md` for improvement directions. Good candidates:
- Investigate why the model might be generating "test" as output (check Grok API behavior)
- Add monitoring for dream cycle output quality (log formatter results before send)
- Expand test coverage for notification formatting edge cases

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- 4594 passed, 18 skipped (test count as of session 214).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Never delete files** — log to `claude/PENDING_DELETIONS.md` instead.
- **Never attempt any action requiring interactive confirmation.**
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
