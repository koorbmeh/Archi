# Session 58: Verification Patch-Up

Copy everything below this line and paste it to start the session.

---

You are continuing the Archi architecture evolution. Sessions 47-48 designed it, sessions 48-57 implemented it, and a verification audit just ran. The audit found 7 partial items, 2 missing items, and 2 changed-from-spec items. Your job this session is to fix them.

## Read These First

1. `claude/SESSION_CONTEXT.md` — Project overview and current status.
2. `claude/CODE_STANDARDS.md` — Coding conventions. Follow for ALL changes.
3. `claude/ARCHITECTURE_PROPOSAL.md` — The original spec.
4. `claude/VERIFICATION_REPORT.md` — The full audit report with findings.

## What to Fix (priority order)

### 1. File Security: Blacklist → Whitelist

**File:** `src/tools/tool_registry.py` (`_validate_path_security()`)

**Problem:** Security uses a blacklist (blocks `/etc/`, `/usr/`, `/bin/`, etc.) instead of a whitelist (allow only workspace root). A path like `/tmp/malicious` passes the current check.

**Fix:** Resolve the canonical path and verify it starts with the project workspace root (from `paths.base_path()`). Reject everything else. Keep the logging on blocked attempts. ~10 lines changed.

### 2. DAG Priority Preemption

**File:** `src/core/task_orchestrator.py`

**Problem:** The `reactive` flag is tracked on tasks but both reactive and proactive tasks go into the same `ThreadPoolExecutor` queue. Reactive user messages don't actually preempt background work.

**Fix:** Either use a `PriorityQueue` feeding the executor, or use separate executors (small one for reactive, main one for proactive), or cancel/pause the lowest-priority proactive task when a reactive task arrives. The spec says: "reactive (user Discord messages) preempts proactive (background work)." ~20-30 lines.

### 3. User Model → Notification Formatter

**File:** `src/core/notification_formatter.py`, `src/core/user_model.py`

**Problem:** The notification formatter doesn't query the user model for Jesse's communication style preferences. Notifications use Archi's persona but don't adapt tone to match what Jesse prefers.

**Fix:** Add `get_context_for_formatter()` to `user_model.py` (return style notes: formality, verbosity, emoji preference, etc.). Inject that context into the formatter's system prompt alongside the notification data. ~15 lines total.

### 4. User Model → Discovery

**File:** `src/core/discovery.py`, `src/core/user_model.py`

**Problem:** `_rank_files()` uses only goal keywords and general heuristics (entry points, READMEs). It doesn't query the user model for Jesse's known project preferences.

**Fix:** Add `get_context_for_discovery()` to `user_model.py` (return known project preferences, frequently-referenced files, domain knowledge). Use those to boost relevance scores in `_rank_files()`. ~10 lines.

### 5. Response Builder Prefix Cleanup

**File:** `src/interfaces/response_builder.py`

**Problem:** Prefix-assembly logic was supposed to be removed but was retained for "backward compatibility on non-Discord callers." The audit couldn't confirm whether non-Discord callers actually exist.

**Fix:** Grep the codebase for all callers of `response_builder`. If no non-Discord callers use the prefix logic, remove it. If callers exist, add a comment documenting why it's retained and which callers need it.

### 6. Integrator Glue Creation

**File:** `src/core/integrator.py`

**Problem:** The integrator detects missing glue (entry points, config files) but doesn't auto-create them. It's flagged as "future enhancement."

**Fix:** When the integrator identifies missing glue (e.g., `__init__.py`, `main.py`, config), emit the file contents as part of its output so workers can create them. This doesn't need to be fully autonomous — producing the content and flagging it for creation is enough. Evaluate whether this is worth doing now or if detection-only is sufficient for current usage.

### 7. Step Cap Alignment

**File:** `src/core/plan_executor.py`

**Problem:** Spec says hard step cap = 30. Implementation uses `MAX_STEPS_PER_TASK = 50`, `MAX_STEPS_CODING = 25`, `MAX_STEPS_CHAT = 12`. The intent is preserved (safety net exists) but the numbers differ.

**Decision needed:** Either update the spec to match the implementation (if the current values work well in practice) or align the code to the spec's value of 30. Check the session logs — if 50 was a deliberate choice during implementation, update the spec. If it was arbitrary, consider whether 30 is better.

## How to Work

- Fix items in priority order (1 is highest impact, 7 is lowest).
- Run `pytest tests/` after each change to verify no regressions.
- Follow `claude/CODE_STANDARDS.md` — net zero or negative lines preferred.
- Update `claude/VERIFICATION_REPORT.md` to mark fixed items.
- Update `claude/SESSION_CONTEXT.md` with session 58 summary when done.
- Update `claude/TODO.md` if any items move to completed or new items are discovered.
