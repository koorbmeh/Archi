# Archi — Code Standards & Conventions

**This is a living document. Every session — audit or feature work — must follow these rules and add new ones as patterns emerge.**

**Read this file at the start of every session alongside the other claude/ docs.**

---

## The Prime Directive for Code

*"Perfection is achieved not when there is nothing more to add, but when there is nothing left to take away."*

Every change should leave the codebase smaller or no larger than it was. If you add 30 lines of new functionality, look for 30 lines elsewhere that can be removed, simplified, or consolidated. The goal is a codebase compact enough that Claude can read the entire thing in a session without losing coherence.

---

## Before Writing Any Code

1. **Read before writing.** Always read the file(s) you're about to modify. Understand the existing patterns before introducing changes.
2. **Search before deleting.** Before removing any file, function, class, import, config key, or env var, grep the entire codebase for references. Fix or remove every reference before completing the deletion.
3. **Check for tests.** If tests exist for what you're changing, run them before and after. If no tests exist for critical behavior you're modifying, consider writing them.
4. **Trace the ripple.** When changing any function signature, class interface, config key, or file name, trace the dependency tree at least two levels deep. Check not just direct callers, but callers-of-callers. If unsure about blast radius, say so.
5. **Check the usual suspects.** After any non-trivial change, glance at these files even if they didn't show up in a grep — they're the ones most likely to need a corresponding update: README.md, claude/ARCHITECTURE.md, .env.example, .gitignore, config/rules.yaml, scripts/.

## Code Style

Patterns established through the Phase 1-7 audit:

- **Imports:** stdlib → third-party → local, one import per line. No unused imports.
- **Error handling:** Raise in library code, log+return-None in handlers that must not crash (dream cycle, action dispatch). Never silently swallow exceptions.
- **Naming:** snake_case for files/functions/variables, PascalCase for classes, UPPER_SNAKE for constants. Prefix private helpers with `_`.
- **Config access:** Use `src/utils/config.py` helpers (`get_rules()`, `get_dream_cycle_config()`, etc.). Never read yaml files directly in business logic.
- **New tools:** Add class in `src/tools/`, register in `tool_registry.py`. New actions go in `action_dispatcher.py` handler registry.
- **New env vars:** Add to `.env.example` with placeholder and comment. Add to `src/utils/config.py` if needed.

## Logging & Observability

Debug logging should be **useful when needed and silent when not**. The standard:

1. **Use Python's `logging` module with per-module loggers**: `logger = logging.getLogger(__name__)`. Never use `print()` for operational output.
2. **Log levels matter**:
   - `DEBUG` — Detailed diagnostic info (variable values, decision points, flow tracing). Off by default in production.
   - `INFO` — Normal operational events worth noting (startup, shutdown, cycle start/end, goal completion).
   - `WARNING` — Something unexpected but recoverable (fallback triggered, retry needed, degraded mode).
   - `ERROR` — Something failed and needs attention (API error, file not found, task failure).
3. **Toggle-able by module**: The logging config should allow turning DEBUG on/off per module (e.g., turn on debug for `plan_executor` without flooding logs with `heartbeat` noise). During the audit, flag any module that doesn't follow this pattern.
4. **No log spam**: Avoid logging inside tight loops, per-message in high-frequency paths, or repeating the same warning every cycle. Use rate limiting or "log once" patterns where appropriate.
5. **Structured where it helps**: For machine-parseable logs (dream_log, action logs, cost tracking), use JSON. For human-readable debugging, plain text is fine.

## Conciseness Rules

1. **No dead code.** If it's commented out "just in case" or behind an always-false flag, delete it. Git has the history.
2. **No wrapper functions that add nothing.** If a function's body is just `return other_function(args)`, remove the wrapper and call the original directly.
3. **No duplicate logic.** If two modules do similar things (e.g., two different JSON extraction helpers), consolidate into one shared utility.
4. **No over-abstraction.** A base class with one subclass, an interface with one implementation, a factory that builds one thing — remove the abstraction layer and use the concrete thing directly.
5. **Favor Python's expressiveness.** Use comprehensions, unpacking, `defaultdict`, `dataclasses`, and built-in patterns where they make code shorter AND clearer. Don't golf it into unreadability, but don't write Java-style boilerplate in Python either.
6. **Short functions > long functions.** If a function is over ~40 lines, it's probably doing more than one thing and should be split. But don't split just to split — only if the pieces are independently meaningful.

## Quality Attributes

These should be considered during every code change, not just during the audit:

1. **Performance** — Avoid N² patterns, redundant file reads, repeated work that could be cached. Be especially mindful of anything that runs inside the dream cycle loop or the message processing hot path.
2. **Reliability** — Handle exceptions at appropriate levels. Don't catch-and-silence errors unless you have a specific recovery strategy. Prefer failing loud to failing silent.
3. **Cost-effectiveness** — Every API call costs money. Avoid unnecessary model calls, cache where appropriate, use fast-paths to skip the model entirely when possible.
4. **Robustness** — Assume dependencies can be unavailable (API down, Discord disconnected, file missing, GPU not present). Degrade gracefully with clear error messages rather than crashing.
5. **Testability** — Keep functions pure where possible. Inject dependencies rather than reaching for globals. If something is hard to test, that's a design smell.

## Portability Rules

These are non-negotiable. Every commit should pass these checks:

1. **No hardcoded Windows paths in src/.** Use `paths.base_path()` or `os.path.join()`. Never `C:\Users\...` or backslash path literals.
2. **No API keys or tokens in source files.** All secrets go in `.env` (which is gitignored). If you add a new secret, also add it to `.env.example` with a placeholder value and a comment explaining what it's for.
3. **No assumptions about a specific machine.** The code should work for anyone who clones the repo and follows the setup guide. If a feature requires specific hardware (GPU for SDXL, etc.), it should degrade gracefully with a clear error message.
4. **Scripts handle first-run.** Install and start scripts should check for prerequisites and give helpful errors, not crash with cryptic tracebacks.
5. **README matches reality.** If you change user-facing behavior, update the README before the session ends.

## File Hygiene

1. **No empty files** (except `__init__.py` which may be intentionally empty for package structure).
2. **No orphaned files.** If a feature is removed, all its files, imports, config entries, test files, doc references, and .gitignore entries get removed too.
3. **No stale comments.** If you change what code does, update the comments. A wrong comment is worse than no comment.
4. **Data directory ships clean.** Runtime-generated files (logs, databases, caches) should not be committed. The repo should include empty placeholder structure where needed (via .gitkeep) and let the app create files at runtime.

## When Adding New Features

1. **Follow existing patterns.** Look at how similar features are implemented before inventing a new approach.
2. **Register properly.** New tools go in tool_registry. New actions go in action_dispatcher. New config goes in the appropriate yaml file AND .env.example if needed.
3. **Update docs.** New features need: updated README section, updated ARCHITECTURE.md entry, and if they affect the user-facing interface, updated TEST_PROMPTS.md with verification steps.
4. **Write tests.** At minimum, unit tests for any new classifier logic, routing changes, or data transformations. Integration tests for new pipeline paths.
5. **Net zero or negative lines.** If adding a feature makes the codebase significantly larger, look for dead code or consolidation opportunities elsewhere to offset it.

## When Removing Features

1. **Trace all references** before deleting. Use grep across the entire project including docs, configs, tests, scripts, and error messages.
2. **Update .gitignore** if the whitelist referenced the removed files.
3. **Update .env.example** if the feature had environment variables.
4. **Update claude/ docs** to remove references to the deleted feature.
5. **Run full test suite** to catch any import errors or broken references.

## Conventions Established During Audit

*(New entries will be added here as the audit progresses. Format:)*

- **Scripts use `_common.py`** — All scripts under `scripts/` import shared utilities from `scripts/_common.py` (ROOT, PYTHON, header, run, load_env, set_env). Don't duplicate these. Established session 23.
- **Cross-platform venv path** — Use `_common.PYTHON` and `_common.VENV_PYTHON` instead of hardcoding `venv/Scripts/python.exe`. Established session 23.
- **No dead comments** — Don't comment out code. Remove it entirely. Git has the history. Established session 23.
- **Protected paths use prefix matching** — In `rules.yaml`, protect whole directories with prefix (e.g. `claude/`) rather than listing individual files. Established session 23.
- **PID lock for daemons** — Any long-running process writes `data/archi.pid` on startup and checks for existing instances. `stop.py` clears the lock. Established session 23.
- **Timestamp all TODO/doc changes** — Every item added to `TODO.md` or `SESSION_CONTEXT.md` must include an ISO date (YYYY-MM-DD) so log analysis can correlate fixes with log entries. For TODO items: include the date when added (e.g., "Added 2026-02-19") and when completed (e.g., "Fixed 2026-02-19"). For SESSION_CONTEXT: the "Last session" and "Last updated" fields already have dates — keep those accurate. This matters because Archi runs overnight and logs span multiple days; without timestamps you can't tell if a log failure happened before or after a fix was deployed. Established session 45.
- **Singleton pattern** — Module-level singletons use double-checked locking with `_reset_for_testing()`. Pattern: module-level `_instance: Optional[T] = None` + `_instance_lock = threading.Lock()`, accessor function checks fast path (not None → return), then acquires lock and checks again. Every singleton module exports `_reset_for_testing()` to clear the instance for test isolation. Examples: `get_user_model()`, `get_idea_history()`, `get_findings_queue()`, `get_shared_registry()`, `get_preferences()`. For classes that need different instances in different contexts (e.g., `LearningSystem` with per-component data dirs), use dependency injection instead of singletons. Established session 75.
- **No cross-module private state access** — Never import `_private_variables` from another module. Instead, add a public function/method to expose the needed behavior. Example: `discord_bot.kick_dream_cycle()` instead of importing `discord_bot._dream_cycle`. Established session 75.
