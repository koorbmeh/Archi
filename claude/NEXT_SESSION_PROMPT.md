# Session 216 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done last session (session 215)

**Diagnostic logging for "test" spam + no-restart verification.**

1. **Archi still NOT restarted.** No new error logs, conversations.jsonl unchanged, scheduled tasks `times_fired: 0`, worldview unchanged (3 prefs, 2 interests). All sessions 207-213 code changes still pending deployment.

2. **"test" spam deep dive (session 7 of investigating).** Verified running code (commit 233d51c) has `_is_garbage_notification` in `send_notification` AND `_log_outbound` is only called from within `send_notification` (2 call sites: line 423 after Discord send, line 493 for digest). Guard correctly returns True for "test". The paradox: "test" entries appear in conversations.jsonl despite the guard. **Strongest hypothesis: dual Python processes** — an old zombie process without the garbage guard producing "test", while the new process handles legitimate notifications. Both write to the same log file.

3. **Added 3 diagnostic improvements:**
   - Stack trace logging in `send_notification` garbage guard (shows call stack when "test" is caught)
   - Stack trace logging in `_log_outbound` garbage guard (same)
   - PID tracking in `_log_outbound` and `log_conversation` entries (`"pid": os.getpid()`)

   After next restart, if "test" entries still appear, the `pid` field will definitively prove whether they come from the same or different process.

**Test count:** 4530 passed, 18 skipped (excl croniter-dependent scheduler tests). No regressions.

---

## What to work on this session

### Priority 1: Check if Archi has been restarted

**This is STILL the gating question.** Check `logs/errors/` for new entries, or `logs/conversations.jsonl` for entries after 14:41 on Mar 6.

- **If restarted** with latest code: proceed to Priority 2-5.
- **If NOT restarted**: recommend Jesse restart. Then skip to Priority 6.

### Priority 2: Verify "test" diagnostic logging works (if restarted)

- Look for `pid` field in conversations.jsonl entries after restart
- If "test" entries appear with different PID than legitimate entries → **dual-process confirmed**. Jesse should run `tasklist | findstr python` and kill the zombie.
- If "test" entries appear with same PID → there's a code path bypassing `send_notification` that we haven't found. Check the stack trace in `logs/errors/` for `_log_outbound blocked garbage` or `Notification suppressed (garbage)` WARNING entries.
- If no "test" entries after restart → the guards work, mystery was old process code.

### Priority 3: Verify scheduled tasks fire (if restarted)

- Check `data/scheduled_tasks.json` — do `times_fired` increment?
- "Drink water" should appear as "Reminder: Drink water" (session 213 prefix fix)
- "Time to stretch." (16 chars, 3 words) should pass garbage guard directly
- If `times_fired` stays 0, verify croniter: `pip show croniter`

### Priority 4: Worldview system growth verification (if restarted)

- After more dream cycles, check for new opinions from `_lightweight_reflection`
- Verify `explore_interest` fires (every 5th cycle, offset 2)
- Verify taste preferences grow from task completions
- Chat-mode reflection (session 209 fix) should generate worldview updates from conversations

### Priority 5: Verify remaining live items (if restarted)

- Interest exploration, taste development, personal projects, meta-cognition
- Opinion revision delivery
- Self-reflection (every 50 cycles)
- See TODO.md "Needs live verification" sections

### Priority 6: Proactive improvement work (if context budget allows)

Check `claude/SELF_IMPROVEMENT.md` for improvement directions. Good candidates:
- Add monitoring for dream cycle output quality (log formatter results before send)
- Expand test coverage for notification formatting edge cases
- Any new improvement ideas from code review

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- 4530 passed, 18 skipped (test count as of session 215, excl croniter scheduler tests).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Never delete files** — log to `claude/PENDING_DELETIONS.md` instead.
- **Never attempt any action requiring interactive confirmation.**
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
