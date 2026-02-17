# Archi Project — Workflow Routine

**Read this at the start of every session. It tells you how to work on this project.**

---

## Starting a Session

1. **Read the key documents** (all in `claude/`) in this order:
   - `claude/SESSION_CONTEXT.md` — What the project is, what's been done, current status
   - `claude/CODE_STANDARDS.md` — Coding conventions, quality rules, conciseness standards (apply to ALL work)
   - `claude/ARCHITECTURE.md` — Execution flows, file locations, line numbers, config values
   - `claude/TODO.md` — The work queue, organized by category
   - `claude/AUDIT_PROMPT.md` — If doing an audit session, check the progress tracker for where to resume
2. **Read the starter prompt** if Jesse provides one — it has session-specific context from where we left off.
3. **Pick work together** — Present the remaining items by readiness/impact, then let Jesse choose (or suggest what to tackle).

## Doing the Work

1. **Read before writing** — Always read the relevant source files before making changes. Understand the existing patterns.
2. **Follow CODE_STANDARDS.md** — Every change must comply with the coding standards, quality attributes, conciseness rules, and logging conventions defined there. If you establish a new convention during the session, add it to the document.
3. **Test what you change** — Run `pytest tests/` after modifications. If tests don't exist for what you're changing, consider writing them.
4. **Assess context usage** — Do as much as is reasonable in a single session. Lightweight tasks (config changes, small fixes, test writing) leave room for more work. Heavy tasks (multi-file refactors, deep exploration) may use most of the window. But when the context gets heavy, wrap up cleanly rather than pushing through and losing coherence. It's better to stop clean and start a fresh session than to produce degraded work.

## After Completing Each Task

1. **Present ideas FIRST, update docs SECOND** — After finishing a task, present any ideas you came up with for new TODO items while you were digging through the code. Ask Jesse if he wants to add them. Do this *before* updating the docs, so you only need to update once with both the completed work and any new items.
2. **Update the claude docs** — Update `claude/SESSION_CONTEXT.md`, `claude/ARCHITECTURE.md`, `claude/TODO.md`, `claude/CODE_STANDARDS.md`, and this file (`claude/WORKFLOW.md`) as needed to reflect what was completed, any new conventions established, and any new items or process improvements.

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

- **`logs/errors/YYYY-MM-DD.log`** — Main operational log (INFO/WARNING/ERROR from all modules). **Start here** for diagnosing issues — shows dream cycles, task execution, API calls, failures, and idle states.
- **`logs/conversations.jsonl`** — Every user↔Archi Discord exchange (timestamped, includes action type and cost)
- **`logs/actions/YYYY-MM-DD.jsonl`** — Detailed per-day action log (heartbeats, task executions, system events)
- **`logs/chat_trace.log`** — Debugging trace for chat flow (intent parsing, fast-path routing, model selection)
- **`data/dream_log.jsonl`** — Dream cycle summaries (tasks completed, duration, files created, insights)
- **`data/goals_state.json`** — Current goals and tasks with full lifecycle (created, started, completed, results)
- **`data/overnight_results.json`** — Recent task results (gets cleared after morning report)

Start with `logs/errors/` for the big picture (dream cycles, task execution, failures), then `conversations.jsonl` for Discord exchanges, then drill into `goals_state.json` and the daily action log for details. Compare what's in `workspace/projects/` against the goals to see if work is being repeated or going unused.

## Before Deleting or Renaming Anything

**Always search for references first.** Before removing a file, renaming a function, or deprecating a config value, grep the entire codebase for references to it. This includes source code, config files, docs, .gitignore, .env.example, scripts, tests, and error messages. Fix or update every reference before completing the deletion.

**Trace the ripple.** Don't stop at direct references. If you change a function in `utils.py`, check everything that imports `utils.py`, then check if those importers are themselves used by other files that might now behave differently. Trace at least two levels deep. See `claude/CODE_STANDARDS.md` "Before Writing Any Code" for the full rule.

## Maintaining External Docs & Config

After significant changes, check whether these need updating:

1. **README.md** — Keep the GitHub README current with features, setup instructions, architecture overview, and config. If a session adds or changes user-facing behavior, update the README before wrapping up.
2. **.gitignore** — Uses a whitelist strategy (ignore all, then `!` specific files). If you add or remove tracked files, update the whitelist to match.
3. **.env.example** — If you add, remove, or rename environment variables, update .env.example so new users get the right template. Also update any comments that reference file names or docs.
4. **Scripts (`scripts/`)** — The install, start, stop, fix, and reset scripts should reflect current dependencies, paths, model names, and startup procedures. After changing config, adding dependencies, or modifying startup flow, review and update the relevant scripts.

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

1. **Produce a starter prompt** — Write a copy-paste prompt Jesse can use to get the next session started with full context.
