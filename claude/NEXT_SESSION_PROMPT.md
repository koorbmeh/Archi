# Session 91 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

---

## What was done last session (session 90)

Two targeted fixes:

1. **Intent classification (Priority 1 — DONE)** — Expanded router prompt's "USER STATEMENTS vs. REQUESTS" section with a new "THINKING OUT LOUD — NOT ACTIONABLE" block covering musings ("I think…", "maybe…", "I wonder if…"), observations ("hmm", "good to know"), notes-to-self ("note to self: …"), and vague hedging ("probably should…", "at some point…"). Added "RULE OF THUMB" heuristic: no imperative verb directed at Archi → default to easy tier. ~30 new tests in test_conversational_router.py.

2. **Grok routing (Priority 2 — DONE)** — Changed bare `grok`, `grok-fast`, `grok-4` aliases from OpenRouter to xAI direct in providers.py. Added `grok-openrouter` alias for explicit OpenRouter routing when needed.

**Test baseline:** 1196 unit tests passing, 15 pre-existing failures (missing `openai` pip package — only affects test_direct_providers.py::TestClientCreation and TestCostEstimation).

---

## What to work on next

Jesse will direct. Current open items from `claude/TODO.md`:

### Lower Priority

- **git_safety multi-file checkpoint** — After switching from `git add -A` to specific-file staging, checkpoint commits only capture the single file being modified. Acceptable tradeoff for now.

### Future Work

- **Onboarding script** — Guided first-run experience for new users. Walk through `.env` setup, verify prerequisites, create initial project_context.json, run connectivity test.

### Possible Follow-ups

- **Validate session 90 intent fix in live use** — If casual remarks still create goals, the prompt may need further tuning or a local fast-path pattern matcher as a pre-filter before the model call.
- **Any new bugs or features Jesse identifies.**

---

## After completing work

1. Run `pytest tests/unit/ -m "not live"` — all ~1196 tests should pass.
2. Update `claude/` docs (SESSION_CONTEXT.md, ARCHITECTURE.md, TODO.md) per WORKFLOW.md wrap-up steps.
3. Write the next `claude/NEXT_SESSION_PROMPT.md`.

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- Current test count: ~1196 unit tests.
- Protected files: plan_executor/ (all 6), safety_controller.py, config.py, git_safety.py, prime_directive.txt, rules.yaml, archi_identity.yaml, mcp_servers.yaml, claude/, heartbeat.py, goal_manager.py, system_monitor.py, health_check.py, performance_monitor.py.
