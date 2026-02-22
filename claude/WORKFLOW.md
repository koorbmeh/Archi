# Archi Project — Workflow Routine

**Read this at the start of every session. It tells you how to work on this project.**

---

## Starting a Session

1. **Read the key documents** (all in `claude/`) in this order:
   - `claude/SESSION_CONTEXT.md` — What the project is, current status, open work items, constraints
   - `claude/CODE_STANDARDS.md` — Coding conventions, quality rules, conciseness standards (apply to ALL work)
   - `claude/ARCHITECTURE.md` — Execution flows, file locations, config values
   - `claude/TODO.md` — The work queue (open items + completed archive)
   - `claude/archive/` — Reference docs if needed (original evolution spec, verification report, audit prompt, etc.)
2. **Read the starter prompt** if Jesse provides one — it has session-specific context from where we left off.
3. **Pick work together** — Present the remaining items by readiness/impact, then let Jesse choose (or suggest what to tackle).

## Doing the Work

1. **Read before writing** — Always read the relevant source files before making changes. Understand the existing patterns.
2. **Follow CODE_STANDARDS.md** — Every change must comply with the coding standards, quality attributes, conciseness rules, and logging conventions defined there. If you establish a new convention during the session, add it to the document.
3. **Test what you change** — Run `pytest tests/` after modifications. If tests don't exist for what you're changing, consider writing them.
4. **Assess context usage** — Do as much as is reasonable in a single session. Lightweight tasks (config changes, small fixes, test writing) leave room for more work. Heavy tasks (multi-file refactors, deep exploration) may use most of the window. But when the context gets heavy, wrap up cleanly rather than pushing through and losing coherence. It's better to stop clean and start a fresh session than to produce degraded work.

## After Completing Each Task

1. **Collect improvement ideas as you go** — While reading and modifying code, note any potential improvements, bugs, cleanup opportunities, or missing tests you spot — even if they're unrelated to the current task. Keep a mental list; you'll need it at end-of-session.
2. **Timestamp everything in TODO.md** — When adding a new item, include `(Added YYYY-MM-DD)` or `(Added YYYY-MM-DD, session N)`. When checking off an item, include `(Fixed YYYY-MM-DD)` or the session reference with a date. This is critical for log analysis — Archi runs overnight and we need to know whether a log entry happened before or after a fix was deployed.

## Recurring Issue Prevention

When you notice a pattern of similar bugs or issues, don't just fix each instance — look for the structural cause and propose a systemic fix.

**During a task, watch for:**
- Multiple similar bugs in the same area → probably a design issue, not just bad values
- Tests revealing several small issues at once → the area likely needs a broader cleanup pass
- The same kind of fix being applied repeatedly across sessions → propose a structural change or abstraction that prevents the class of bug entirely

**After fixing something, ask:**
- Could this same kind of bug exist elsewhere in the codebase? If so, add a TODO to audit for it.
- Is there a test that would have caught this before it became a problem? If not, write one.
- Is there a pattern or convention we could adopt that makes this bug impossible in the future?

**Track patterns across sessions:** If the "What's Been Done" section in SESSION_CONTEXT.md shows a cluster of similar fixes (e.g., three different classifier edge cases in a row), that's a signal. Before moving on, consider whether the classifier design itself needs rethinking, not just more patches.

## Checking Logs

Jesse will often ask you to "check the logs" or "look at what Archi's been up to." When he does, here's where to look:

- **`logs/conversations.jsonl`** — **Essential context.** Every user↔Archi Discord exchange (timestamped, includes action type and cost). Shows what Jesse asked, what Archi replied, and when. Use this to understand why goals were created, what the user expected, and how responses relate to failures or outcomes. Check this early — it grounds everything else.
- **`logs/errors/YYYY-MM-DD.log`** — Main operational log (INFO/WARNING/ERROR from all modules). Start here for diagnosing issues — shows dream cycles, task execution, API calls, failures, and idle states.
- **`logs/actions/YYYY-MM-DD.jsonl`** — Detailed per-day action log (heartbeats, task executions, system events)
- **`logs/chat_trace.log`** — Debugging trace for chat flow (intent parsing, fast-path routing, model selection)
- **`data/dream_log.jsonl`** — Dream cycle summaries (tasks completed, duration, files created, insights)
- **`data/goals_state.json`** — Current goals and tasks with full lifecycle (created, started, completed, results)
- **`data/overnight_results.json`** — Recent task results (gets cleared after morning report)

**Order of operations:** Read `conversations.jsonl` first for context (what was asked, what was promised). Then `logs/errors/` for the big picture (dream cycles, task execution, failures). Cross-reference with `goals_state.json` and the daily action log for details. Compare what's in `workspace/projects/` against the goals to see if work is being repeated or going unused.

## Before Deleting or Renaming Anything

Follow the "Before Writing Any Code" rules in `claude/CODE_STANDARDS.md` — especially search for references and trace the ripple effect at least two levels deep.

## Production Testing

The `/test` Discord command runs quick smoke tests (5 prompts) through the live pipeline. `/test full` runs the complete suite. These reuse `tests/integration/test_harness.py` definitions and validators.

For changes that aren't covered by the test harness, suggest a concrete way to verify on the live system:
- **Minimal effort** — ideally just "send `/test`" or "send this message and check the response"
- **Observable** — point to the specific log file, Discord output, or metric
- **Time-bounded** — say how long to wait (e.g., "5 minutes for a dream cycle to start")

Examples:
- Routing change → "Run `/test` to confirm fast-paths and model routes still work. Then check `logs/actions/YYYY-MM-DD.jsonl`."
- Classifier change → "Send this exact Discord message: '...'. Check `logs/chat_trace.log` for the routing decision."
- Loop detection → "Let Archi run a dream cycle with research tasks. Check `data/dream_log.jsonl` — tasks should complete in <8 steps."

## Wrapping Up a Session

**This is mandatory. Every session must end with all five steps below, no exceptions.** If context is running low, stop doing task work and switch to wrap-up. A clean handoff is more valuable than one more half-finished fix.

### Step 1: Present new TODO items

Go through every file you read or modified during the session. Present any improvement ideas, bugs, cleanup opportunities, missing tests, or potential issues you noticed — even small ones. Ask Jesse which ones to add to TODO.md. This is the single most important step for keeping the project moving forward. Don't be shy — if something looked off, mention it.

### Step 2: Update TODO.md

- Mark completed items as `[x]` with `(Fixed YYYY-MM-DD, session N)`.
- Add any new items Jesse approved from Step 1 with `(Added YYYY-MM-DD, session N)`.
- **Verify before marking done** — actually check the source files to confirm fixes landed. Don't trust session notes alone.

### Step 3: Update SESSION_CONTEXT.md

- Update the "Last session" line with a brief summary of what this session did.
- Update the "Open work" paragraph to reflect current state.
- Update the "Last updated" date and session number.

### Step 4: Update ALL claude/ docs

**Every session must review every doc for staleness — not just TODO.md and SESSION_CONTEXT.md.** The most commonly missed doc is ARCHITECTURE.md, which drifts when module responsibilities, test counts, config values, or execution flows change without an explicit update. If you changed or learned something about any of these, update the relevant doc:

- **`ARCHITECTURE.md`** — execution flows, file locations, config values, module responsibilities, test counts, directory layout changes. **If you added/removed/renamed files, changed how modules interact, or updated test counts, this doc MUST be updated.** Check the Testing section test count every session — it's almost always stale.
- **`CODE_STANDARDS.md`** — if any new conventions were established or existing ones refined.
- **`WORKFLOW.md`** — if any process improvements were identified.

**Quick staleness check:** Before writing NEXT_SESSION_PROMPT.md, scan each doc for numbers, dates, and factual claims that may have changed this session (e.g., test counts, module line counts, file paths, session numbers).

### Step 5: Write NEXT_SESSION_PROMPT.md

Write `claude/NEXT_SESSION_PROMPT.md` — a self-contained prompt that Jesse can drop into the next session's context window to pick up exactly where we left off. It must include:

1. **First line:** `Read all docs in claude/ first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.`
2. **What was done this session** — brief summary, test count.
3. **What to work on next** — prioritized list pulled from TODO.md open items, grouped by priority (bug-class fixes first, then dead weight, then refactors, then lower priority). Include the specific file paths and a one-line description of the fix for each item.
4. **After completing fixes** — remind to run full test suite, update claude/ docs, and produce the next NEXT_SESSION_PROMPT.md.
5. **Key constraints** — reference CODE_STANDARDS.md, current test count.

This file gets overwritten every session. It is the primary handoff mechanism between sessions — treat it as the most important deliverable of the wrap-up.
