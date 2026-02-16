# Archi — Todo List

Last updated: 2026-02-16 (session 32)

---

## Open Items

- [ ] **Startup on boot (visible terminal)** — Get Archi auto-starting on laptop reboot again. Must launch in a visible terminal window, not as a background service — if Jesse logs in he needs to see it running.
- [ ] **Audit loops & heartbeat** — Re-evaluate the heartbeat, dream cycle, and other periodic loops. Are they producing useful outcomes? Trim or rethink anything that's just churning without clear value.
- [ ] **Architecture review** — Step back and ask whether there's a better way to do any of the things Archi already does (or is trying to do). Look for over-engineering, unnecessary complexity, or patterns that made sense for local models but don't fit the API-first world.
- [ ] **Companion personality** — Make Archi feel more like a companion and less like a tool. (Scope TBD — could touch tone, proactivity, memory, conversational style, etc.)

## Future Ideas

- [ ] Add more direct provider tests (Anthropic, DeepSeek, etc.)
- [ ] Provider health monitoring — auto-fallback if a direct provider is down

---

## Completed Work

<details>
<summary>Session 32 — Dream cycle effectiveness overhaul</summary>

- [x] **Fixed model to reasoning variant** — Default model was set to `grok-4-1-fast-non-reasoning`, causing the model to loop without planning. Changed to `grok-4-1-fast-reasoning` in `providers.py`.
- [x] **Fixed path-blind loop detection** — `list_files` on different directories all registered as the same action key, triggering false loop abort after 3 calls. Now includes the path in the key: `f"{action_type}:{path[:60]}"`. Same for `read_file`, `web_search`, and `fetch_webpage`.
- [x] **Added sibling task context sharing** — Tasks in the same goal now receive summaries of previously completed sibling tasks as hints, so they build on earlier work instead of starting from scratch. Implemented in `autonomous_executor.py`.
- [x] **Added step budget awareness** — PlanExecutor prompt now includes step count and remaining budget. Warns model to transition from research to output at the halfway point, and urgently at 3 steps remaining. In `plan_executor.py`.
- [x] **Raised MAX_STEPS_PER_TASK to 50** — Old limit of 15 was from the local model era. Budget ($0.50/cycle) and time (10 min) caps are the real safety nets. In `plan_executor.py`.
- [x] **Model-inferred goal creation** — Intent classifier can now recognize when a chat request is too large for a quick response and automatically create a goal, responding conversationally (e.g., "This is going to take some real work, so I'll handle it in the background."). In `intent_classifier.py` and `action_dispatcher.py`.
- [x] **Auto-escalation for overflowing chat tasks** — When a chat PlanExecutor uses all its steps without producing output (still researching), it auto-creates a goal and responds: "Can I take some time to think about it? I'll work on it in the background." In `message_handler.py`.
- [x] **Removed cost display from Discord messages** — The `(Cost: $X.XXXX)` footer on every reply was distracting. Removed from both text and image response paths in `discord_bot.py`.

</details>

<details>
<summary>Session 31 — Goal-driven idle behavior</summary>

- [x] **Goal-driven idle behavior** — Replaced autonomous work generation with user-driven flow. When idle with no goals, Archi brainstorms suggestions and presents them via Discord with numbered picks. User decides what to work on — Archi never auto-approves or creates goals on its own.
- [x] **Merged brainstorm + plan_future_work** into `suggest_work()` — single system, runs when idle with nothing to do, always asks user.
- [x] **Synthesis → informational only** — No longer creates follow-up goals. Logs themes to `synthesis_log.jsonl` for morning report.
- [x] **Follow-up extraction → within-goal tasks** — `extract_follow_up_goals()` replaced with `extract_follow_up_tasks()`. Adds tasks to the current goal instead of spawning new goals. Prevents unbounded goal chains.
- [x] **Discord suggestion picking** — User can reply "1", "2", "#3" etc. to pick a brainstormed suggestion. Creates a goal from the chosen idea.
- [x] **Removed proactive_tasks from archi_identity.yaml** — No longer used.

</details>

<details>
<summary>Session 30 — Provider routing & cache fix</summary>

- [x] **P2-19: cache.py O(n) LRU eviction** — Replaced `List` with `OrderedDict` for O(1) `move_to_end()`/`popitem()`. Deleted `_mark_accessed()`.
- [x] **Direct API provider support** — New `src/models/providers.py` (registry, aliases, pricing). Generalized `openrouter_client.py` for any OpenAI-compatible endpoint. Router defaults to xAI direct, falls back to OpenRouter. Discord: "switch to grok direct", etc.
- [x] **Renamed ARCHI_TODO.md → TODO.md** — Updated 8 cross-references across claude/ docs.
- [x] **.env/.env.example sync** — Removed dead local-model vars, added provider key placeholders, added missing DISCORD_OWNER_ID and ARCHI_WHISPER_MODEL.

</details>

<details>
<summary>Sessions 23–29 — Seven-phase codebase audit</summary>

Full audit across 7 phases, cleaning up after the local-to-API migration:

- **Phase 1 (session 23):** Config & project root — fixed stale scripts, added PID lock, cross-platform venv detection, protected claude/ in .gitignore.
- **Phase 2 (session 24):** Models & utilities — deleted local_model.py, backends/, model_detector.py, cuda_bootstrap.py. Rewrote router.py (609→531 lines). Consolidated `strip_thinking`, fixed cost tracker key mismatch.
- **Phase 3 (session 25):** Core engine — removed dead imports, consolidated `_extract_json` into `src/utils/parsing.py`, removed `prefer_local` from 15 call sites, deduplicated goal-counting functions.
- **Phase 4 (session 26):** Interfaces & tools — fixed brainstorm approval bug, removed stale system prompt refs, deleted 3 orphan modules + `src/web/`, added workspace mkdir at startup, added API health check.
- **Phase 5 (session 27):** Tests — deleted 21 script-style test files, fixed integration tests to use free models, integrated diagnostics into fix.py.
- **Phase 6 (session 28):** Data & workspace — cleaned stale data files, renamed synthesis_log to .jsonl, added missing .env.example vars, created workspace/.gitkeep.
- **Phase 7 (session 29):** Documentation — updated project trees in README.md and ARCHITECTURE.md (17 missing files), populated CODE_STANDARDS.md, fixed test_harness.py path in 3 docs, added Discord permissions to README.

</details>

<details>
<summary>Sessions 7–19 — API migration, v2 architecture, features</summary>

- **API-first migration (7–9):** Switched all model calls from local to Grok/OpenRouter, stopped loading local LLM at startup (saved ~6GB VRAM), added system role messages for prompt caching.
- **V2 architecture (10–13):** Built intent_classifier, response_builder, action_dispatcher, message_handler pipeline. Split dream_cycle.py (1,701→4 modules). Created 36 integration tests.
- **Computer use (15–16):** Auto-escalation to Claude for computer use, screenshot sending with zero-cost fast-path.
- **Goal system & dream cycle (9, 14–17):** Goal relevance filter, artifact requirements, aggressive caps, purpose-driven brainstorming, proactive Discord notifications, stale file cleanup.
- **Multi-step chat (17):** Cancel/interrupt support, smart step estimates.
- **Behavioral (18–19):** Epistemic humility, proactive follow-up, deferred request handling.

</details>

<details>
<summary>Sessions 1–6 — Foundation</summary>

Conversation fixes, classifier refinements, goal/research deduplication, dream cycle throttling, safety gates (source code approval, loop detection, claude/ protection), context window improvements, runtime model switching via Discord.

</details>
