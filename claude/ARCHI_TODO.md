# Archi — Master Todo List

Last updated: 2026-02-16 (session 29 — codebase audit Phase 7, audit complete)

---

## Open Items — Phase 1 Audit Findings (still open)

- [x] **P1-19: README.md Discord bot setup** — Added permissions checklist (Message Content Intent, Send Messages, Embed Links, Attach Files, Read Message History). Done in Phase 7 (session 29).
- [x] **P1-24: test_harness.py consider moving to tests/** — RESOLVED. Jesse moved to tests/integration/ (session 27).
- [x] **P1-25: Ensure workspace/ dir created at startup** — RESOLVED in Phase 4 (P4-25).

## Open Items — Phase 2 Audit Findings

### Decision: Remove all local model infrastructure

Jesse confirmed (session 24): local models are done. Archi is API-only now. The only local GPU workload is SDXL image generation (diffusers), which has zero dependency on LocalModel, backends, or model_detector. SDXL continues to work independently.

### DELETE — Files removed by Jesse

- [x] **P2-D1: Delete `src/models/local_model.py`** (625 lines)
- [x] **P2-D2: Delete `backends/` directory** (4 files, 874 lines)
- [x] **P2-D3: Delete `src/utils/model_detector.py`** (303 lines)
- [x] **P2-D4: Delete `src/core/cuda_bootstrap.py`** (95 lines)
- [x] **P2-T1: Delete `tests/unit/test_local_model.py`**

### Deferred to later phases (non-local-model items)

- [x] **P2-04: Consolidate `_extract_json` duplicates** — RESOLVED in Phase 3 (session 25). Moved to `src/utils/parsing.py` as `extract_json()`.
- [x] **P2-13: Remove `prefer_local` dead parameter** — RESOLVED in Phase 3 (session 25). Removed from router.py signature + 15 call sites across src/core/ and src/interfaces/.
- [x] **P2-16: Check `is_available()` on OpenRouterClient** — RESOLVED in Phase 4 (P4-21). health_check.py now pings openrouter/auto.
- [ ] **P2-19: cache.py O(n) LRU eviction** — Consider `collections.OrderedDict`. Defer (low priority).
- [x] **P2-23: Rename `web_chat_history.json`** — RESOLVED in Phase 4 (P4-09). Renamed to chat_history.json with auto-migration.
- [x] **P2-24: test_api_search.py imports non-existent `grok_client`** — RESOLVED: file will be deleted (P5-D7).

### Phase 3 done items (session 25)

Session 25: All src/core/ files reviewed. cuda_bootstrap.py already deleted in Phase 2. P3-20 (image_gen GPU guard) WITHDRAWN — valid for SDXL GPU protection. Clean files: agent_loop.py, heartbeat.py, safety_controller.py, resilience.py, logger.py, file_tracker.py, __init__.py.

- [x] **P3-06: idea_generator.py — Removed unused `os` and `Path` imports**
- [x] **P3-15: goal_manager.py — Removed unused `import re`**
- [x] **P3-17: learning_system.py — Removed unused `import re`**
- [x] **P3-05 + P3-23: Removed dead `bypass_cooldown` param** — from reporting.py `_notify()` and autonomous_executor.py `_notify()`, plus all call sites.
- [x] **P3-18: Removed invalid `stop=[]` kwargs from learning_system.py** — router.generate() has no `stop` parameter.
- [x] **P2-13: Removed `prefer_local` dead parameter** — from router.py `generate()` signature + 15 call sites (9 in src/core/, 6 in src/interfaces/).
- [x] **P3-01/02/03: Replaced duplicated functions in autonomous_executor.py** — `_MAX_ACTIVE_GOALS`, `_count_active_goals()`, `_is_duplicate_goal()` replaced with imports from idea_generator.
- [x] **P2-04/P3-08/P3-11/P3-22: Consolidated `_extract_json` into src/utils/parsing.py** — Added `extract_json()` to parsing.py, removed duplicates from plan_executor.py and interesting_findings.py, updated dream_cycle.py import.
- [x] **P3-19: Fixed stale "GPU/CUDA" log message in dream_cycle.py** — Changed to "Learning system skipped: %s".

### Phase 4 action items (session 26)

#### DELETE — Orphan modules (Jesse to delete manually)

- [x] **P4-15: Delete `src/tools/system_monitor.py`** — Deleted by Jesse.
- [x] **P4-16: Delete `src/tools/system_health_logger.py`** — Deleted by Jesse.
- [x] **P4-17: Delete `src/tools/categorize_files.py`** — Deleted by Jesse.
- [x] **P4-29: Delete `src/web/` directory** — Deleted by Jesse.

#### FIX — Bugs and stale references (all applied session 26)

- [x] **P4-07: BUG — discord_bot.py brainstorm approval unreachable** — Moved approval logic from `except ImportError` block into `try` block.
- [x] **P4-01/02/03: Stale system prompt — local model references** — Removed "local model" from FREE list, changed strategy text, removed "prefer local model" from constraints.
- [x] **P4-04: Remove deleted files from protected list** — Removed system_monitor.py and system_health_logger.py (tool versions) from system prompt protected files.
- [x] **P4-18: cost_tracker.py stale recommendation** — Changed "consider using local model more" to "consider caching more or reducing non-essential API calls".
- [x] **P4-09 (P2-23): Rename web_chat_history.json** — Changed to chat_history.json with auto-migration from old name.
- [x] **P4-11: web_search_tool.py stale docstring** — Removed "for the local model".
- [x] **P4-26: archi_service.py stale comment** — Changed "web chat" to "Discord chat".
- [x] **P4-27: archi_service.py stale comment** — Removed "may fail if no local model" qualifier.

#### IMPROVE — Carry-forward and enhancements (all applied session 26)

- [x] **P4-25 (P1-25): Add workspace/ mkdir at startup** — archi_service.py now creates workspace/, workspace/reports/, workspace/images/, workspace/projects/ on startup.
- [x] **P4-21 (P2-16): Add API connectivity check to health_check.py** — Now pings openrouter/auto to validate key. Returns degraded if key present but unreachable.
- [x] **P4-19/20: Remove legacy pricing constants from cost_tracker.py** — Deleted DEFAULT_GROK_INPUT/OUTPUT_PER_1M, deleted PRICING["grok"] and PRICING["local"] entries, removed "grok" from API cost filter.
- [x] **P4-08: discord_bot.py print→logger** — Replaced print() with logger.info().
- [x] **P4-12: desktop_control.py unused import shlex** — Removed.
- [x] **P4-13: computer_use.py extra blank lines** — Fixed.
- [x] **P4-14: computer_use.py unused variables** — Removed resized_w, resized_h.
- [x] **P4-24: monitoring/system_monitor.py deprecated datetime.utcnow()** — Replaced with datetime.now(timezone.utc).
- [x] **P4-28: maintenance/timestamps.py deprecated datetime.utcnow()** — Same.
- [x] **P4-10: chat_history.py ambiguous comment** — Rephrased to "10 user + 10 assistant".

### Phase 5 action items (session 27)

Decision: Jesse reviewed the test audit findings and decided on a new direction — most script-style tests are dead weight and should be deleted rather than fixed. The proper pytest unit tests (~310 tests) are valuable and stay. Integration tests get updated to use free models. Diagnostic functionality from script-style tests gets folded into fix.py.

#### DELETE — Script-style tests (dead weight)

These files use `print()` / `sys.exit()` instead of pytest assertions. They aren't collected by pytest, aren't run by CI or fix.py, and have accumulated stale references. Delete them all. This makes P5-1 through P5-11 (broken `_root` paths), P5-2 (stale stats keys), P5-5 (stale env var), and P5-16 (stale comment) moot.

- [x] **P5-D1–D5: Deleted 5 script-style tests from `tests/unit/`** — test_router.py, test_openrouter_api.py, test_cache.py, test_vector_store.py, test_lancedb.py. Deleted by Jesse.
- [x] **P5-D6–D10: Deleted entire `tests/scripts/` directory** — 15 script-style test files + __init__.py. Deleted by Jesse.
- [x] **P5-D11: Deleted `tests/integration/test_full_system.py`** — Deleted by Jesse.
- [x] **P5-28: Moved `test_harness.py` from project root to `tests/integration/`** — Done by Jesse. (Resolves P1-24/P5-25.)

#### FIX — Kept integration tests

- [x] **P5-15: Fix `tests/integration/test_archi_full.py`** — DONE. (a) `GROK_API_KEY` → `OPENROUTER_API_KEY`. (b) Removed stale `SKIP_LOCAL=1` docstring. (c) Renumbered sections 1-13.
- [x] **P5-17: Fix `tests/integration/test_gate_a.py` `test_zero_api_costs`** — DONE. Renamed to `test_api_costs_tracked`, now asserts cost_usd fields exist (API-only). Updated standalone summary to match.
- [x] **P5-26: Update integration tests to use free model** — DONE. Both test_v2_pipeline.py and test_archi_full.py now default to `meta-llama/llama-3.1-8b-instruct:free` ($0). Override with `TEST_MODEL` env var.

#### IMPROVE — Update to public function names

- [x] **P5-13: Update `tests/unit/test_routing_classifiers.py` to use public names** — DONE. Imports changed from `_needs_multi_step`/`_is_coding_request_check` to `needs_multi_step`/`is_coding_request`. Backward-compat aliases removed from intent_classifier.py.

#### IMPROVE — Integrate diagnostics into fix.py

- [x] **P5-27: Add test/diagnostic capabilities to fix.py** — DONE. Added API connectivity check (live ping), router smoke test (init + stats + active model), and cost tracker summary (today/month/all-time) to `run_diagnostics()`.

#### NOTE — Carry-forward

- [x] **P1-24 / P5-25: Move `test_harness.py` to `tests/`** — DONE. Jesse moved it to `tests/integration/`.
- [ ] **P2-19: cache.py O(n) LRU eviction** — Low priority.

### Phase 6 action items (session 28)

#### REMOVE — Stale files (deleted by Jesse)

- [x] **P6-01: Delete 8 `.fuse_hidden*` files in `data/`** — Deleted by Jesse.
- [x] **P6-02: Delete `data/web_chat_history.json`** — Deleted by Jesse.
- [x] **P6-03: Delete `data/chat_history.txt`** — Deleted by Jesse.

#### FIX — Bugs and stale references (all applied session 28)

- [x] **P6-04: `scripts/reset.py` references stale `chat_history.txt`** — Changed to `chat_history.json`. Also added `file_manifest.json` and `cost_usage.json` to JSON resets.
- [x] **P6-05: Rename `data/synthesis_log.json` → `synthesis_log.jsonl`** — Renamed file, updated `dream_cycle.py` reference. Now caught by `*.jsonl` glob in `reset.py`.
- [x] **P6-06: `.env.example` missing `IMAGE_MODEL_PATH`, `ARCHI_VOICE_ENABLED`, `ARCHI_PIPER_VOICE`** — Added all three with placeholder values and comments.
- [x] **P6-10: Create `workspace/.gitkeep`** — Created. `workspace/` now tracked as empty dir in git.
- [x] **P6-13: Remove dead `!test_harness.py` from `.gitignore`** — Removed.

#### IMPROVE — Cleanup

- [x] **P6-11: Delete `models/piper/.cache/` tree** — Deleted by Jesse.

#### NOTE — Informational

- **P6-08: `file_manifest.json` purged** — Was referencing 4 nonexistent workspace files. Reset to `{"files": {}}`.
- **P6-09: `workspace/reports/` and `workspace/projects/` don't exist yet** — P4-25 creates them at startup. They'll appear on next Archi launch.
- **P6-12: `data/` gitignored implicitly** — Caught by root-level `/*` catch-all since it's not whitelisted. Works correctly.
- **P6-14: All runtime state files purged** — cost_usage.json, file_manifest.json, synthesis_log.jsonl, dream_log.jsonl, experiences.json, goals_state.json, idea_backlog.json, interesting_findings_queue.json, overnight_results.json, user_preferences.json all reset to empty defaults.

### Phase 7 done items (session 29)

Session 29: Documentation & Claude Docs audit. All docs verified against actual codebase state after Phase 1-6 changes.

- [x] **P7-01: README.md project structure incomplete** — Was missing 17 src/ files, 2 whole directories (utils/, maintenance/). Updated tree to show all 56 src/ files.
- [x] **P7-02: README.md `tests/scripts/` in tree** — Directory no longer exists (deleted Phase 5). Removed from tree.
- [x] **P7-03: README.md `archi.service` deployment** — File never existed in repo. Inlined the service file content in README deployment section.
- [x] **P7-04: README.md Discord bot permissions** — Added Message Content Intent, Send Messages, Embed Links, Attach Files, Read Message History. Closes P1-19.
- [x] **P7-05: ARCHITECTURE.md directory layout incomplete** — Same issues as README tree plus tools/ used `...` abbreviation. Fully expanded all directories.
- [x] **P7-06: ARCHITECTURE.md `test_harness.py` path wrong** — Referenced "at project root" but it's at `tests/integration/test_harness.py`. Fixed across ARCHITECTURE.md, WORKFLOW.md, AUDIT_PROMPT.md.
- [x] **P7-07: ARCHITECTURE.md `ARCHI_TODO.md` at root** — Showed at project root in tree, actually in `claude/`. Fixed in new layout.
- [x] **P7-08: CODE_STANDARDS.md code style placeholder** — "This section will be populated during the audit" was never populated. Filled in with patterns established across 7 audit phases.
- [x] **P7-09: workspace/.gitkeep missing** — Whitelisted in .gitignore but file didn't exist. Created.
- [x] **P7-10: .gitignore whitelist verified** — All whitelisted files confirmed to exist. No tracked files missing from whitelist.

### Future work (not audit items)

- [ ] **Direct API provider option** — Add ability to use model providers directly (e.g. xAI Grok API) as an alternative to OpenRouter. Would need a provider abstraction or a second client class alongside OpenRouterClient.

### Phase 2 done items (session 24)

- [x] **P1-21: heartbeat.yaml dead throttling fallbacks** — Removed `throttling` section.
- [x] **P2-01: Remove unused `get_recent_checkpoints()`** — Removed from git_safety.py.
- [x] **P2-03: Consolidate `strip_thinking`** — Merged enhanced logic into text_cleaning.py.
- [x] **P2-05: Replace `_strip_thinking_blocks()` in parsing.py** — Now imports from text_cleaning.
- [x] **P2-09: Strip `src/utils/__init__.py`** — Removed dead model_detector re-exports.
- [x] **P2-10: BUG — router.py `total_cost_usd` key mismatch** — Fixed to `"total_cost"`.
- [x] **P2-14: router.py extra blank lines** — Cleaned up.
- [x] **P2-15: Remove legacy `GROK_API_KEY` warning** — Removed from openrouter_client.py.
- [x] **P2-20: cache.py relative disk cache path** — Now uses `paths.data_dir()`.
- [x] **P2-R1: router.py — Remove all local model integration** — Full rewrite, 609→531 lines.
- [x] **P2-R2: image_gen.py — Remove LocalModel VRAM coordination**
- [x] **P2-R3: computer_use.py — Remove local vision model** — 514→377 lines.
- [x] **P2-R4: archi_service.py — Remove `_shared_local_model` and cuda_bootstrap**
- [x] **P2-R5: agent_loop.py — Remove `cuda_bootstrap` import, `local_model` param, stale comments**
- [x] **P2-R6: discord_bot.py — Remove `cuda_bootstrap` import**
- [x] **P2-R7: health_check.py — Remove local model checks** — Rewrote `_check_models()` for API-only.
- [x] **P2-R8: src/models/__init__.py — Update comment**
- [x] **P2-R9: .env.example — Remove local model env vars** — 59→40 lines.
- [x] **P2-R10: requirements.txt — Remove llama-cpp-python comments**
- [x] **P2-R11: .gitignore — Remove `!backends/` whitelist**
- [x] **P2-R13: scripts/install.py — Remove model download and CUDA build logic** — 616→331 lines.
- [x] **P2-R14: scripts/fix.py — Remove llama-cpp-python diagnostics** — 484→428 lines.
- [x] **P2-DOC1: README.md — Remove "Local Model Setup" section**
- [x] **P2-DOC2: claude/ARCHITECTURE.md — Update model routing**
- [x] **P2-DOC3: claude/SESSION_CONTEXT.md — Update description**
- [x] **P2-T2–T13: Clean up 15 test files** — Removed cuda_bootstrap, LocalModel, grok_client, backends imports and local_available references.
- [x] **P2-T14 (new): Clean up test_cost_tracking.py and test_budget_enforcement.py** — Replaced local model provider references with OpenRouter.

### Phase 1 done items

- [x] **P1-01: archi.service references nonexistent script** — Fixed: `start.py service`
- [x] **P1-02: fix.py references removed modules** — Removed all stale refs (grok_client, web_chat, ports)
- [x] **P1-03: fix.py web chat cache clearing** — Removed
- [x] **P1-04: fix.py checks GROK_API_KEY** — Updated to check OPENROUTER_API_KEY
- [x] **P1-05: reset.py references web_chat_history.json** — Removed
- [x] **P1-06: .env.example CUDA version** — Fixed: v12.8
- [x] **P1-07: Multi-instance boot bug** — Added PID lock (`data/archi.pid`) to start.py; stop.py clears lock on shutdown
- [x] **P1-08: Windows-only venv path** — Created `scripts/_common.py` with cross-platform venv detection; all 5 scripts updated
- [x] **P1-13: startup_archi.bat stale comment** — Removed `--web` reference
- [x] **P1-14: .gitignore add claude/** — Added `!claude/` and `!claude/*`; also added `!backends/`
- [x] **P1-15: .gitignore remove workspace/example.txt** — Replaced with `.gitkeep` pattern
- [x] **P1-16: scripts/README.md rewrite** — Updated to match current subcommands
- [x] **P1-17: fix.py modernize** — Rewrote diagnostics, removed all stale code, streamlined menus
- [x] **P1-18: stop.py WMIC deprecation** — Replaced with PowerShell `Get-Process` + `Get-CimInstance`
- [x] **P1-20: rules.yaml protect claude/ folder** — Changed to `claude/` prefix
- [x] **P1-22: requirements.txt huggingface_hub** — Removed (only used by install.py, models come from CivitAI)
- [x] **P1-23: Shared utility for scripts** — Created `scripts/_common.py`; all scripts refactored to use it
- [x] **P1-09: Delete forge.py** — Deleted by Jesse
- [x] **P1-10: Delete API_REFERENCE.md** — Deleted by Jesse
- [x] **P1-11: Delete DEPLOYMENT.md** — Deleted by Jesse
- [x] **P1-12: Delete MISSION_CONTROL.md** — Deleted by Jesse

---

## Audit Progress Tracker

Phase 1 — Config & Project Root:
- [x] Project root files (.env.example, .gitignore, requirements.txt, pytest.ini, forge.py, archi.service, README.md, etc.)
- [x] config/ (identity, heartbeat, rules, prime directive + example files)
- [x] scripts/ (start, stop, install, fix, reset, startup bat, README)

Phase 2 — Utilities & Models:
- [x] src/utils/ (paths, config, git_safety, text_cleaning, parsing, model_detector) — reviewed session 24
- [x] src/models/ (router, openrouter_client, local_model, cache) — reviewed session 24
- [x] backends/ (base, llamacpp, hf_transformers) — reviewed session 24
- [x] Carry-forward: P1-21 — RESOLVED. No heartbeat-only mode exists; throttling values in heartbeat.yaml are dead. Removing.
- [x] Apply approved Phase 2 changes — all code changes applied (session 24). 5 files pending manual deletion by Jesse.

Phase 3 — Core Engine:
- [x] src/core/ part 1 (agent_loop, heartbeat, safety_controller, logger, resilience) — reviewed session 25, all clean
- [x] src/core/ part 2 (plan_executor, goal_manager, dream_cycle, autonomous_executor, idea_generator) — reviewed session 25
- [x] src/core/ part 3 (learning_system, user_preferences, interesting_findings, reporting, file_tracker) — reviewed session 25. cuda_bootstrap already deleted in Phase 2.
- [x] Apply approved Phase 3 changes — all code changes applied (session 25)
- Carry-forward: P1-25 — ensure workspace/ dir created at startup (add mkdir in archi_service.py) — deferred to Phase 4 (archi_service.py)

Phase 4 — Interfaces & Tools:
- [x] src/interfaces/ — reviewed session 26. Bug: brainstorm approval dead code (P4-07). Stale local-model refs in system prompt (P4-01/02/03). chat_history.py rename (P4-09/P2-23).
- [x] src/tools/ — reviewed session 26. 3 orphan modules to delete (P4-15/16/17). Clean: tool_registry, web_search, image_gen, browser_control, ui_memory.
- [x] src/memory/ — reviewed session 26. Clean.
- [x] src/monitoring/ — reviewed session 26. Stale local-model rec in cost_tracker (P4-18). Legacy pricing constants to remove (P4-19/20). health_check API check (P4-21/P2-16).
- [x] src/service/ — reviewed session 26. Stale comments (P4-26/27). workspace mkdir (P4-25/P1-25).
- [x] src/maintenance/ — reviewed session 26. deprecated utcnow() (P4-28).
- [x] src/web/ — reviewed session 26. Delete entirely (P4-29).
- [x] Apply approved Phase 4 changes — all done session 26
- Carry-forward: P1-25 — RESOLVED (P4-25). P2-16 — RESOLVED (P4-21). P2-23 — RESOLVED (P4-09).

Phase 5 — Tests (session 27):
- [x] tests/unit/ — reviewed session 27
- [x] tests/integration/ — reviewed session 27
- [x] tests/scripts/ — reviewed session 27
- [x] test_harness.py + test_results.json — reviewed session 27
- Carry-forward: P1-24 — RESOLVED. Jesse moved test_harness.py to tests/integration/

Phase 6 — Data & Workspace (session 28):
- [x] data/ — reviewed. 8 .fuse_hidden to delete, web_chat_history.json + chat_history.txt stale, synthesis_log.json is JSONL, reset.py stale ref.
- [x] workspace/ — reviewed. Missing .gitkeep, reports/ and projects/ created at startup (P4-25). Health_Optimization is user content, properly gitignored.
- [x] models/ — reviewed. SDXL and Piper paths correct. piper/.cache/ stale. .env.example missing IMAGE_MODEL_PATH + voice env vars.

Phase 7 — Documentation & Claude Docs (session 29):
- [x] README.md — Project structure updated (17 missing files added, 2 dirs added, tests/scripts/ removed, archi.service inlined, Discord permissions added)
- [x] claude/ docs — ARCHITECTURE.md layout corrected, test_harness.py path fixed in 3 files, CODE_STANDARDS.md code style populated, SESSION_CONTEXT.md updated
- [x] .gitignore — Whitelist verified, workspace/.gitkeep created
- [x] Carry-forward P1-19 closed, P6-11 confirmed closed

---

## Completed Items Archive

<details>
<summary>Click to expand completed items (sessions 7-19)</summary>

### API-First Migration (Session 7-9)

- [x] **Switch all model calls to Grok default** — DONE (session 8). Flipped 19 `prefer_local=True` to `False`. Set `DEFAULT_MODEL = "x-ai/grok-4.1-fast"`.
- [x] **Evaluate removing local reasoning models** — DONE (session 8). Decision: keep installed but opt-in only.
- [x] **Simplify router.py for API-first** — DONE (session 8). Removed automatic local→API escalation path.
- [x] **Simplify history tier system** — DONE (session 9). Collapsed to single tier.
- [x] **Stop loading local LLM on startup** — DONE (session 9). Saves ~6GB VRAM.
- [x] **Add system role message to API calls** — DONE (session 9). Enables OpenRouter prompt caching.
- [x] **Fix .env model routing** — DONE (session 9). Removed `OPENROUTER_MODEL=openrouter/auto` override.
- [x] **Route startup test to free model** — DONE (session 9). $0 per startup.
- [x] **Update budget expectations** — DONE (session 13). $5/day and $100/month confirmed appropriate.

### V2 Architecture Refactor (Sessions 10-13)

- [x] **Extract shared utilities** — `src/utils/text_cleaning.py`
- [x] **Build response_builder.py, action_dispatcher.py, intent_classifier.py, message_handler.py** — Full v2 pipeline
- [x] **Wire discord_bot.py** — Direct imports from message_handler
- [x] **Simplify router.py** — Removed dead code paths
- [x] **Fix multi-turn conversation context** — Proper messages array
- [x] **Split dream_cycle.py** — DONE (session 11). 1,701→4 modules.
- [x] **Simplify plan_executor.py** — DONE (session 11). Removed semantic loop detection + think-loop circuit breaker.
- [x] **Delete action_executor.py legacy** — DONE (session 13).
- [x] **V2 pipeline integration tests** — DONE (session 12). 36 pytest tests.

### Interface Cleanup (Session 7-9)

- [x] **Remove web chat, CLI, dashboard** — DONE (session 8). Discord is sole interface.
- [x] **Update README, scripts** — DONE (sessions 8, 16).
- [x] **Delete deprecated file stubs** — DONE (session 9).

### Computer Use & Desktop Automation (Session 15-16)

- [x] **Auto-escalate to Claude for computer use** — DONE (session 15).
- [x] **Implement screenshot sending** — DONE (session 16). Zero-cost fast-path.

### Goal System & Dream Cycle (Sessions 9, 14-17)

- [x] **Configurable dream cycle interval via Discord** — DONE (session 9).
- [x] **Track completed research topics in memory** — DONE (session 15). LanceDB integration.
- [x] **Link reports to actual projects** — DONE (session 16).
- [x] **Goal relevance filter** — DONE (session 14).
- [x] **Require goals to reference concrete artifacts** — DONE (session 14).
- [x] **Cap self-generated goals aggressively** — DONE (session 14). MAX_PROACTIVE_GOALS=1, MAX_FOLLOW_UP_DEPTH=2.
- [x] **Review completed work before generating more** — DONE (session 14).
- [x] **Eliminate fictional/hallucinated data** — DONE (session 14). DATA VERIFICATION rule.
- [x] **Purpose-driven brainstorming** — DONE (session 17).
- [x] **Proactive Discord notifications** — DONE (session 17).
- [x] **Stale file cleanup with approval** — DONE (session 17).

### Multi-Step Chat (Session 17)

- [x] **Cancel/interrupt for multi-step chat** — DONE (session 17).
- [x] **Smart step estimates in progress messages** — DONE (session 17).

### Behavioral / Personality (Sessions 18-19)

- [x] **Epistemic humility in responses** — DONE (session 19).
- [x] **Proactive follow-up on user requests** — DONE (session 18). Deferred request handling.

### Bugs (Session 19)

- [x] **Dream cycle config commands not recognized** — DONE (session 19).
- [x] **Notify user when tasks are recovered after restart** — DONE (session 19).

### Housekeeping (Session 14)

- [x] **Update MISSION_CONTROL.md** — DONE (session 14).
- [x] **Automated production testing** — DONE (session 14). `/test` and `/test full` commands.

</details>

<details>
<summary>Click to expand completed items (sessions 1-6)</summary>

### Conversation & Interaction Fixes
- [x] Fix greeting handler override
- [x] Confirm goal acceptance
- [x] Multi-step reasoning during live chat
- [x] Fix hourly report verbosity

### Classifier Refinements
- [x] Fix "hey," pattern gap
- [x] Fix "what's up" false positive
- [x] Narrow `"install "` in coding classifier
- [x] Fix stale assertion in test_greeting_costs.py

### Goal System & Dream Cycle
- [x] Goal/research deduplication (active AND completed)
- [x] Prevent report stacking
- [x] Break "think" loops
- [x] Semantic loop detection for searches

### Model Routing (OLD DIRECTION — local-first, superseded by API-first)
- [x] Strategic OpenRouter usage
- [x] Classify task complexity
- [x] Use stronger models for prompt/goal generation
- [x] Fix PlanExecutor always escalating to OpenRouter
- [x] Force local model for all autonomous work (REVERSED by API-first migration)

### Multi-Step Chat
- [x] Progress feedback during multi-step chat
- [x] Unit tests for routing classifiers

### Context Window & History
- [x] Expand history window for API-routed requests
- [x] Session-aware conversation history
- [x] Add conversation history to coding fast-path

### Dream Cycle — Busywork Prevention
- [x] Throttle proactive goal generation

### Safety & Autonomy Controls (session 3)
- [x] Source code approval gate
- [x] Force-abort on loop detection
- [x] Protect claude/ docs and identity config
- [x] Busywork throttle

### Bug Fixes (sessions 2-5)
- [x] Fix dream cycle not pausing during Discord chat
- [x] Fix `prefer_local` TypeError
- [x] Fix approval listener ignoring natural language
- [x] Fix RuntimeError: dict changed size during iteration
- [x] Fix PlanExecutor success=True despite verification failure
- [x] Fix learning system logging failures as success
- [x] Prevent follow-up goals from low-quality work
- [x] Cloud model escalation on think-loop
- [x] Fix approval-denial loop freezing dream cycle
- [x] Fix force-aborted tasks recorded as success
- [x] Make send_notification resilient to Discord disconnection
- [x] Detect system sleep / large time gaps
- [x] Deferred approval support
- [x] Brainstorm approval gate

### Scripts & Documentation
- [x] Update README for GitHub
- [x] Runtime model switching via Discord
- [x] Temporary model switching with auto-revert

</details>
