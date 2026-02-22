Read all docs in `claude/` (SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md) before starting.

Session 75 completed all remaining Architecture & Code Quality items from the code review. The code review is now fully resolved — every critical, security, logic, performance, dependencies, and architecture item is done.

**What session 75 did:**
1. **Singleton standardization** — All 5 singletons now use double-checked locking + `_reset_for_testing()`. `IdeaHistory` became a proper singleton via `get_idea_history()` (was creating new instances per call). Convention added to CODE_STANDARDS.md.
2. **discord_bot.py state encapsulation** — Added `kick_dream_cycle()` and `close_bot()` public APIs. No external code imports private `_variables` anymore. Convention added to CODE_STANDARDS.md.
3. **ComputerUse God class split** — Extracted `ImageAnalyzer` to `src/tools/image_analyzer.py` (vision prompt building, API calls, coordinate parsing).

**What's still open:**

**Improvements (2 remaining from code review):**
1. **Create architecture diagram / onboarding guide** — `claude/ARCHITECTURE.md` is aimed at Claude sessions, not human developers. Create `docs/ARCHITECTURE.md` with data flow diagram, concurrency model, state management.
2. **Scalability: IVF index for LanceDB** — No IVF index configured. Configure IVF-PQ when memory exceeds ~10K entries. Touches: `vector_store.py`.

**Non-code-review items:**
- Startup on boot (visible terminal)
- Discord command to add/remove projects
- More direct provider tests

**Other notes:**
- Still missing test coverage for: `mcp_client`, `vector_store` (lower priority — depend on external services).
- `git_safety.py` checkpoint may miss related files (documented acceptable tradeoff).

Key constraints: Follow CODE_STANDARDS.md strictly — read before writing, search before deleting, trace the ripple, net-zero lines where possible. Run `pytest tests/unit/ -m "not live" -p no:cacheprovider` after changes. Present new ideas before updating docs. 800 tests currently passing.
