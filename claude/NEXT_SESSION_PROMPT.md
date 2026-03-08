# Session 240 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done (session 239)

Built **Self-Extension Phase 5: Integration & End-to-End Testing**. The self-extension system now has proper failure propagation and comprehensive test coverage.

**What was added:**
- `StrategicPlanner.fail_phase()` — marks a phase as failed and pauses the project (new status: "paused")
- `StrategicPlanner.resume_project()` — resets a failed phase to in_progress and resumes the project (for Jesse to retry after fixing the root cause)
- `GoalWorkerPool._check_project_phase_failure()` — detects stuck project-linked goals (failed/blocked tasks, nothing pending) and propagates failure upward to pause the project
- `tests/unit/test_self_extension_e2e.py` — 17 new tests: full cycle E2E, failure propagation, resume, edge cases

**Modified files:**
- `src/core/strategic_planner.py` — +`fail_phase()`, +`resume_project()` (~80 lines)
- `src/core/goal_worker_pool.py` — +`_check_project_phase_failure()` (~35 lines), hooked into `_execute_goal` after task execution

**Test results:** 162 tests pass in planner + goal manager + e2e suite. Pre-existing scheduler + async failures unchanged.

---

## What to work on this session

### Priority 1: Wire `resume_project` into Discord

The `resume_project()` method exists but isn't wired to Discord yet. When a project pauses due to failure, Jesse should be able to say "resume project" to retry. Add handling in `discord_bot.py` similar to the existing plan activation flow.

### Priority 2: Content Calendar + Scheduling (Content Strategy Phase 4)

Uses brand config pillars for topic rotation, auto-publish via heartbeat. Design in `claude/DESIGN_CONTENT_STRATEGY.md`. This is a good next roadmap item now that the self-extension system is complete.

### Priority 3: Visual Content Pipeline (Content Strategy Phase 2)

Integrate SDXL into content flow. Platform-specific image templates. `src/tools/image_generator.py`. **Needs Jesse to sign up for Replicate** (replicate.com) for Flux API.

---

## Jesse action needed

1. **Delete git lock files** — `.git/index.lock` and `.git/HEAD.lock` (0-byte stale locks). Then commit all accumulated changes (sessions 230-239).
2. **Test the approval flow** — Let Archi run until a capability gap is proposed. Reply "go for it" → plan → "go for it" → activate. If a phase fails, try "resume project".
3. **Sign up for Replicate** (replicate.com) — API token for Flux image generation. Add to `.env` as `REPLICATE_API_TOKEN`. Unlocks Content Strategy Phase 2.
4. **Choose Suno access method** — third-party API vs self-hosted. Unlocks Content Strategy Phase 3.

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all code changes.
- Test count: 4885 passed, ~2 skipped. Pre-existing scheduler + async failures.
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`.
- **Stay under 50% context window.** Wrap up proportional to what you did.
- **Never use AskUserQuestion tool.** Never delete files. Never attempt interactive confirmation.
- **Don't re-verify unchanged items.** Verification items are parked until next deploy.
