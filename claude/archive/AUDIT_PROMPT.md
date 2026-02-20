# Archi Codebase Audit — Session Prompt

**Copy-paste this prompt at the start of each audit session.**

---

## Prompt

```
Read all files in claude/ to get oriented, then resume the codebase audit.

This is a multi-session, systematic review of every file and directory in the Archi project. The goals are:

1. **Understand** — Walk me through what each file does, how it fits into the system, and any non-obvious design decisions. I want to learn my own codebase thoroughly.

2. **Clean** — Identify and remove dead code, unused files, stale references, leftover artifacts from removed features (web dashboard, CLI, etc.), empty directories, redundant data files, and anything that adds clutter without value.

3. **Correct** — Find bugs, logic errors, incorrect comments, outdated docstrings, misleading variable names, inconsistent patterns, and things that just don't work right.

4. **Improve** — Make the code as concise as possible without losing effectiveness. "Perfection is achieved not when there is nothing more to add, but when there is nothing left to take away." For every function, class, and module, ask: can this achieve the same result with less code? Look for overly verbose logic, unnecessary abstractions, duplicated patterns that could be shared, and cases where 20 lines could be 5. Also propose better error handling, clearer naming, and more consistent patterns. The goal is a codebase small enough that Claude can read the whole thing without getting overwhelmed.

5. **Portability** — Ensure the project works for someone cloning it fresh from GitHub (including me if I lose this laptop): no hardcoded paths, no assumptions about a specific machine, clear setup instructions, .env.example covers everything, scripts handle first-run gracefully, README is accurate and complete enough to go from zero to running.

6. **Maintainability** — Ensure the code is easy to work on: well-structured, not over-abstracted, consistent style, good test coverage for important behavior, and follows the conventions documented in claude/CODE_STANDARDS.md (build this document as we go).

7. **Quality attributes** — While reviewing each file, also evaluate:
   - **Performance**: Unnecessary loops, redundant file reads, N² patterns, repeated work that could be cached or batched
   - **Reliability**: Unhandled exceptions, race conditions, silent failures, operations that should be atomic but aren't
   - **Cost-effectiveness**: Unnecessary API calls, model calls that could be avoided, cache misses that shouldn't be
   - **Testability**: Code that's hard to test because of tight coupling, hidden dependencies, or global state
   - **Observability**: Missing or excessive logging. We want a debug logging strategy where logging can be toggled on/off by module so that when things work we don't spam the disk, and when they don't we can find the problem fast. Flag anywhere this pattern is missing or inconsistent.
   - **Robustness**: Edge cases, graceful degradation when dependencies are unavailable, recovery from partial failures

## How to Work

- **Go directory by directory** in the order shown in the progress tracker. Check off completed blocks as you go.
- For each directory, read every file, then present your findings organized as:
  - **Overview**: What this directory/file does and how it fits in (the "guided tour" part)
  - **Remove**: Files, functions, imports, or code blocks that should be deleted
  - **Fix**: Bugs, errors, incorrect behavior, wrong comments
  - **Improve**: Refactors, simplifications, conciseness wins, quality attribute issues
  - **Portability issues**: Hardcoded paths, missing env vars, platform assumptions
  - **Questions for Jesse**: Anything where you need my input before deciding
- After we discuss findings for a block, make the agreed-upon changes immediately.
- After completing each block, update:
  - claude/TODO.md — add any new items we defer to later
  - claude/CODE_STANDARDS.md — add any patterns/conventions we establish
  - The progress tracker below
- **Context window management**: Do as much as is reasonable in a single session — don't artificially limit to one block if there's room for more. But when you feel the context getting heavy, wrap up cleanly: finish the current block, update all docs, and produce the starter prompt for next session. It's better to stop clean than to push through and lose coherence.
- If a block is too large for one session, note exactly where you stopped and pick up there next time.

## Ripple Effect Rule

When a change is made to any file, don't just grep for direct references to the thing that changed. Consider the full dependency tree: if you rename a function in utils.py, check everything that imports utils.py — but also check if those importers are themselves imported by other files that might now behave differently. When a change could cascade, trace it at least two levels deep. If you're unsure about the blast radius, say so and we'll trace it together.

Additionally, after completing changes for any block, do a quick check of these files regardless of whether they showed up in a grep — they're the ones most likely to need updates when anything changes:
- README.md
- claude/ARCHITECTURE.md
- .env.example
- .gitignore
- config/rules.yaml (protected files list)
- scripts/ (install, start, fix — do they still reference everything correctly?)

## Review Order & Progress Tracker

Phase 1 — Config & Project Root (sessions 22-23):
- [x] Project root files (.env.example, .gitignore, requirements.txt, pytest.ini, README.md)
- [x] config/ (identity, heartbeat, rules, prime directive + example files)
- [x] scripts/ (start, stop, install, fix, reset, startup bat, README)

Phase 2 — Utilities & Models (session 24):
- [x] src/utils/ (paths, config, git_safety, text_cleaning, parsing)
- [x] src/models/ (router, openrouter_client, cache)
- [x] Deleted: src/models/local_model.py, src/utils/model_detector.py, backends/, src/core/cuda_bootstrap.py

Phase 3 — Core Engine (session 25):
- [x] src/core/ part 1 (agent_loop, heartbeat, safety_controller, logger, resilience)
- [x] src/core/ part 2 (plan_executor, goal_manager, dream_cycle, autonomous_executor, idea_generator)
- [x] src/core/ part 3 (learning_system, user_preferences, interesting_findings, reporting, file_tracker)

Phase 4 — Interfaces & Tools (session 26):
- [x] src/interfaces/ (message_handler, intent_classifier, action_dispatcher, response_builder, discord_bot, chat_history, voice_interface)
- [x] src/tools/ (tool_registry, web_search, image_gen, browser_control, desktop_control, computer_use, ui_memory)
- [x] src/memory/ (memory_manager, vector_store)
- [x] src/monitoring/ (cost_tracker, health_check, performance_monitor, system_monitor)
- [x] src/service/ (archi_service)
- [x] src/maintenance/ (timestamps)
- [x] Deleted: src/tools/system_monitor.py, src/tools/system_health_logger.py, src/tools/categorize_files.py, src/web/

Phase 5 — Tests (session 27):
- [x] tests/unit/ — 18 files reviewed. 5 broken `_root` paths, stale stats keys in test_router.py, stale GROK_API_KEY in test_openrouter_api.py. Several script-style (not pytest).
- [x] tests/integration/ — 4 files reviewed. test_archi_full.py has stale SKIP_LOCAL docstring + GROK_API_KEY. test_gate_a.py zero-cost assertion outdated. test_full_system.py wrong _root.
- [x] tests/scripts/ — 12 files reviewed. test_api_search.py imports deleted grok_client (P2-24 confirmed). test_local_search.py entirely dead. test_budget_enforcement.py uses stale provider="grok".
- [x] tests/integration/test_harness.py — clean, well-structured. test_results.json — empty.
- Deleted: tests/scripts/test_local_search.py (dead — local model test)

Phase 6 — Data & Workspace (session 28):
- [x] data/ — reviewed. Stale files deleted, synthesis_log.json→.jsonl, reset.py updated, all state files purged.
- [x] workspace/ — reviewed. .gitkeep created, reports/projects/ confirmed active (created at startup).
- [x] models/ — reviewed. SDXL and Piper paths correct. piper/.cache/ stale (pending manual delete).

Phase 7 — Documentation & Claude Docs (session 29):
- [x] README.md (comprehensive accuracy check against actual current state)
- [x] claude/ docs (do they still accurately describe the codebase after all our changes?)
- [x] .gitignore (does the whitelist match what actually exists?)

## Rules

- Don't skip files because they're small or seem trivial. Check everything.
- If you find dead code, trace its references before proposing removal.
- If you're unsure whether something is used, grep for it and show me the results.
- Propose changes with enough context that I can make an informed yes/no decision.
- When we establish a convention (naming, error handling, import style, etc.), add it to CODE_STANDARDS.md so future sessions enforce it.
- Run pytest after making changes to any block that has test coverage.
- Favor conciseness. If a 50-line function can be 15 lines without losing clarity, make it 15 lines. If two modules do similar things, consider merging them. If a wrapper adds no value, remove it.
- **Check for dead modules, not just dead functions.** For every file in scope, ask: is this module still imported and used by anything in production code? Trace the full dependency chain. The codebase has gone through major architectural shifts (local-first → API-first, web dashboard → Discord-only) and entire modules may have become orphaned. Flag any module where the only callers are tests or other dead code.
- **Never comment out code. Remove it entirely.** Don't leave commented-out blocks "for reference" or "just in case." Git has the history. When reviewing files, flag any existing commented-out code for deletion. The codebase should contain zero commented-out code blocks.
```

---

## Session Startup Shortcut

If resuming mid-audit, use this shorter version:

```
Read all files in claude/ to get oriented. We're continuing the codebase audit — check the progress tracker in claude/AUDIT_PROMPT.md to see where we left off, and resume from the next unchecked block.
```
