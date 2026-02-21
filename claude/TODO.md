# Archi — Todo List

Last updated: 2026-02-21 (session 63)

---

## Open Items

- [x] **Fix PlanExecutor _step_history crash** — (Added 2026-02-20, session 47. Fixed 2026-02-20, session 47.) `_build_step_prompt()` and the edit_file read-before-edit guard referenced `self._step_history` but it was never initialized — the step history was only stored in a local variable `steps_taken`. Crashed on step 1 of every task with `AttributeError`. Fix: added `self._step_history = steps_taken` after crash recovery (so the reference survives reassignment). Touches: `plan_executor.py`.
- [x] **Fix suggestion pick not recognizing affirmative replies** — (Added 2026-02-20, session 47. Fixed 2026-02-20, session 47.) When Archi sent one suggestion and Jesse replied "That's fine, go ahead" or "I have no idea what that is, but go ahead I guess", `_parse_suggestion_pick()` didn't recognize it. First fix was exact-match only with 40-char limit. Second fix: split into exact matches (short phrases) + substring matching (longer messages containing "go ahead", "do it", "sounds good", etc.). No length limit on substring path. Touches: `discord_bot.py`.
- [x] **Fix doubled create_goal response** — (Added 2026-02-20, session 47. Fixed 2026-02-20, session 47.) When the intent classifier returned `create_goal`, the model's conversational prefix (e.g. "Got it—queued up...") was prepended to the action_dispatcher's hardcoded response ("On it — I'll work on that in the background"), producing doubled messages. Fix: skip `action_prefix` for `create_goal` and `generate_image` actions since they generate complete responses. Touches: `message_handler.py`.
- [x] **Second/third pass on Discord message tone** — (Added 2026-02-20, session 47. Fixed 2026-02-20, session 47.) Despite session 46 rewrite, messages still had emoji prefixes, bold markdown formatting, verbose goal-description echoes, and structured layouts. Removed all of these from: goal completion notifications, morning report, hourly summary, ask_user, source approval, initiative announcements, interrupted task recovery, suggestion list, and create_goal response. Touches: `discord_bot.py`, `action_dispatcher.py`, `dream_cycle.py`, `goal_worker_pool.py`, `reporting.py`.
- [x] **Make goal completion notifications explain what was built** — (Added 2026-02-20, session 47. Fixed 2026-02-20, session 47.) Completion messages listed bare filenames ("Files: health_tracker.py, core_context.json") without explaining what they do. Fix: (1) Goal notifications now show PlanExecutor "Done:" task summaries instead of filenames (falls back to filenames if no summaries). (2) PlanExecutor "done" action prompt now instructs Grok to say what each file does and how to use it. Touches: `goal_worker_pool.py`, `reporting.py`, `plan_executor.py`.
- [x] **Fix empty vector store / memory not persisting across restarts** — (Session 41) Vector store data WAS persisting (LanceDB table at data/vectors/archi_memory.lance survived restarts). The 0-entries count was because nothing ever wrote to it — the only write path was gated behind `_learning_success`, and all tasks had failed. Fixed in autonomous_executor.py to store both successes and failures.
- [x] **Re-evaluate loops, heartbeat, dream cycle** — (Session 57, Phase 9 Cleanup) Heartbeat simplified from 3-tier to 2-tier (command 10s / idle 60s + night mode). Deep sleep tier and evening multiplier removed. Intent classifier legacy model-classify path removed. Stale loop-detection artifacts cleaned from PlanExecutor (`_force_aborted` → `_schema_retries_exhausted`). Dream cycle structure confirmed adequate — no changes needed.
- [ ] **Startup on boot (visible terminal)** — Get Archi auto-starting on laptop reboot again. Must launch in a visible terminal window, not as a background service — if Jesse logs in he needs to see it running.
- [x] **Fix Ctrl+C shutdown responsiveness & ghost processes** — (Session 41) Root cause: GoalWorkerPool.shutdown() blocked forever on `executor.shutdown(wait=True)` while PlanExecutor finished its full task. Fix: Ctrl+C now prints status to console, triggers PlanExecutor cancellation (`signal_task_cancellation`) so running tasks bail at next step boundary, worker pool has real timeout with visible countdown. Discord bot explicitly closed on shutdown via `_bot_client.close()`. stop.py rewritten as nuclear kill — uses `proc.kill()` / `taskkill /F /T`, matches by cmdline + cwd + project path, double-taps survivors.
- [x] **Fix loop detection — two complementary fixes** — (Session 41) Two separate problems: (1) Old code counted TOTAL occurrences per action type across the whole task, so legitimate patterns like read→append→read→fetch→read (3 total reads on same file) got falsely killed. Fix: switched to CONSECUTIVE identical action key counting, so intervening actions break the chain. Added write-then-read exemption (`read_file:X` after `create_file:X`/`append_file:X` tagged as `read_file_verify:X`). (2) When model IS genuinely stuck (consecutive identical actions), old code killed silently with no chance to recover. Fix: escalating warnings injected into prompt — repeat 2 → nudge, repeat 3 → strong warning, repeat 4 → force abort (model saw both warnings and still looped).
- [x] **Architecture Evolution Phase 1: PlanExecutor Internals + Security** — (Added 2026-02-20, session 48. Fixed 2026-02-20, session 48.) Implemented 5 systems: Context Compression (older steps compressed after step 8, recent 5 full fidelity), Structured Output Contracts (schema validation + auto re-prompt, 2 retries), Mechanical Error Recovery (transient/mechanical/permanent classification, retry with backoff for transient, targeted hints for mechanical), Reflection prompt (self-check before done), File Security Hardening (symlink resolution via realpath + system directory blocking). New file: `src/core/output_schemas.py`. Touches: `plan_executor.py`, `tool_registry.py`.
- [x] **Architecture Evolution Phase 2: QA + Critic** — (Added 2026-02-20, session 48. Fixed 2026-02-20, session 49.) Built QA Evaluator (`qa_evaluator.py`) with deterministic checks (file exists, parseable, not empty/truncated) + model-based semantic evaluation. On rejection, task retries once with QA feedback as hints. Built Critic (`critic.py`) — adversarial per-goal evaluation that finds real problems and adds remediation tasks. Removed ~120 lines of loop detection (consecutive detector, rewrite-loop tracker, warning injection) from PlanExecutor — QA replaces this with targeted feedback instead of blunt force-abort. Hard step cap of 50 retained. New files: `src/core/qa_evaluator.py`, `src/core/critic.py`. Touches: `autonomous_executor.py`, `goal_worker_pool.py`, `plan_executor.py`.
- [x] **Architecture Evolution Phase 3: Notifications + Feedback** — (Added 2026-02-20, session 50. Fixed 2026-02-20, session 50.) Created `notification_formatter.py` — single model call per notification via Grok 4.1 Fast, producing conversational messages matching Archi's warm persona. All notification paths (goal completion, morning report, hourly summary, suggestions, findings, initiative announcements, idle prompts, interrupted tasks, decomposition failures) now route through the formatter with deterministic fallbacks. Added reaction-based feedback: Discord `on_raw_reaction_add` handler tracks 👍/👎 reactions on tracked notifications and records them via `learning_system.record_feedback()`. Significant goals (3+ tasks or 10+ min) append "Anything you'd change?" prompt. New file: `src/core/notification_formatter.py`. Touches: `discord_bot.py`, `goal_worker_pool.py`, `reporting.py`, `dream_cycle.py`.
- [x] **Architecture Evolution Phase 4: Conversational Router + User Model** — (Added 2026-02-20, session 51. Implemented 2026-02-20, session 51.) Single model call per inbound message replaces all heuristic routing (suggestion picks, approval parsing, question reply detection, cancel detection, reply topic inference). Router classifies intent AND generates easy-tier answers in one shot. Input accumulation support for multi-message answers. User Model: structured JSON store of Jesse's preferences, corrections, patterns, and style — extracted as a side effect of Router processing (no dedicated model call). Local fast-paths (slash commands, datetime, screenshot, image gen) run BEFORE the Router call at zero cost. Discord-specific commands (model switch, dream cycle, deferred approval) stay in discord_bot.py. Message handler accepts optional RouterResult to skip redundant intent classification; legacy classify() path retained for internal callers. New files: `src/core/conversational_router.py`, `src/core/user_model.py`. Touches: `discord_bot.py` (removed 6 heuristic functions, rewired on_message), `message_handler.py` (added _map_router_result bridge), `intent_classifier.py` (deprecation notes), `response_builder.py` (Phase 4 notes).
- [x] **Architecture Evolution Phase 5: Planning + Scheduling** — (Added 2026-02-20, session 53. Implemented 2026-02-20, session 53.) Four components: (1) Discovery Phase (`src/core/discovery.py`) scans project files before Architect runs — enumerates files, ranks by relevance, reads selectively, compresses into structured brief via one model call. (2) Architect enhancement in `decompose_goal()` — richer prompt with Discovery brief and User Model context, produces concrete task specs (files_to_create, inputs, expected_output, interfaces). (3) Event-driven DAG Scheduler rewrites `task_orchestrator.py` — replaces wave-based batching with as_completed() event loop that submits newly unblocked tasks immediately when dependencies clear. (4) Request Prioritization — `submit_goal()` and `kick()` accept `reactive` parameter; user-requested goals tagged reactive at all call sites (action_dispatcher, message_handler, discord_bot), proactive goals (dream cycle initiatives) stay default. New file: `src/core/discovery.py`. Touches: `goal_manager.py` (Task spec fields, Architect prompt), `task_orchestrator.py` (full rewrite), `goal_worker_pool.py` (Discovery + priority), `autonomous_executor.py` (spec hints), `dream_cycle.py` (kick reactive param), `action_dispatcher.py`, `message_handler.py`, `discord_bot.py` (reactive=True).
- [x] **Architecture Evolution Phase 6: Integration** — (Added 2026-02-20, session 54. Implemented 2026-02-20, session 54.) Three components: (1) Integrator (`src/core/integrator.py`) — post-completion cross-task synthesis via one model call per multi-task goal. Checks pieces fit together (imports, entry points, interfaces), produces human-readable summary of what was built. Summary replaces individual task summaries in notification messages. (2) Goal-level QA — new `evaluate_goal()` in `qa_evaluator.py`. Conformance check after Integrator: do all task outputs together satisfy the original goal? Catches dangling references and missing pieces that per-task QA misses. (3) Critic + User Model wiring — `critic.py` now queries `user_model.get_user_model()` for preferences, corrections, and patterns. Injected into adversarial prompt so Critic flags style/approach mismatches. Pipeline in `_execute_goal()` is now: Orchestrator → Integrator → Goal QA → Critic → Notify. New file: `src/core/integrator.py`. Touches: `qa_evaluator.py` (goal-level eval), `critic.py` (User Model integration), `goal_worker_pool.py` (pipeline wiring + notifications).
- [x] **Architecture Evolution Phase 8: Graceful Degradation** — (Added 2026-02-20, session 56. Implemented 2026-02-20, session 56.) When the primary LLM provider fails, the router cascades through a priority-ordered chain of backup providers with per-provider circuit breakers and exponential backoff. Four components: (1) Provider Fallback Chain (`src/models/fallback.py`) — priority-ordered chain (xai → openrouter → deepseek → openai → anthropic → mistral), only providers with API keys included. Per-provider CircuitBreaker reusing `resilience.py` (3 failures → open, 30s→60s→120s→5min recovery backoff). Auto-recovery on success. (2) Router integration — `_use_api()` tries fallback chain on failure. Cache as last resort during total outage. Friendly error message for users. (3) Degraded mode visibility — Discord "status"/"what model" shows provider health icons (🟢/🔴/🟡). Discord notifications on entering/exiting degraded mode. (4) Dream cycle pause — skips dream cycles when all providers are down to avoid burning budget on retries. New file: `src/models/fallback.py`. Touches: `src/models/router.py`, `src/interfaces/discord_bot.py`, `src/core/dream_cycle.py`.
- [x] **Architecture Evolution Phase 7: MCP Tool Integration** — (Added 2026-02-20, session 55. Implemented 2026-02-20, session 55.) MCP becomes the transport layer for tool execution. Six components: (1) MCP Client (`src/tools/mcp_client.py`) — async client connecting to servers via stdio transport, on-demand lifecycle (start on first use, stop after idle timeout), background event loop for sync callers. (2) Local MCP Server (`src/tools/local_mcp_server.py`) — wraps existing tools (file ops, web search, desktop, browser) as a FastMCP server. Image gen excluded (privacy). (3) Tool Registry refactor (`src/tools/tool_registry.py`) — MCP-aware: `initialize_mcp()` discovers tools from all configured servers, `execute()` routes through MCP when available, falls back to direct. New `_DIRECT_ONLY` set for tools that must never go through MCP. (4) MCP Server Config (`config/mcp_servers.yaml`) — declarative config for MCP servers (command, args, env, idle_timeout). Adding a new server = adding config, not code. (5) GitHub MCP server (`@modelcontextprotocol/server-github`) wired as first external server — provides list_issues, create_pull_request, get_file_contents, etc. (6) PlanExecutor fallback — unknown actions route to tool registry instead of erroring, enabling automatic support for MCP-provided tools. Also: GitHub tool risk levels added to `rules.yaml`, `mcp_servers.yaml` added to protected files, `mcp>=1.0.0` added to requirements.txt, `.env.example` updated with `GITHUB_PERSONAL_ACCESS_TOKEN`. End-to-end tested: 41 tools discovered (local + GitHub), tool calls succeed through MCP bridge. New files: `src/tools/mcp_client.py`, `src/tools/local_mcp_server.py`, `config/mcp_servers.yaml`. Touches: `src/tools/tool_registry.py`, `src/core/plan_executor.py`, `src/core/agent_loop.py`, `config/rules.yaml`, `.env.example`, `requirements.txt`.
- [x] **Review architecture for better approaches** — (Added pre-session 48. Completed across sessions 48–58.) The 9-phase architecture evolution (sessions 48–57) was a comprehensive redesign: Discovery, Architect, DAG scheduler, QA, Integrator, Critic, Notifications, Graceful Degradation, MCP, and Cleanup. Session 58 verification audit reviewed the full implementation against the spec. ARCHITECTURE_PROPOSAL.md and VERIFICATION_REPORT.md archived.
- [x] **Help Archi make progress on current goals** — (Session 42) Root cause: `suggest_work()` brainstormed generic "research X, write markdown" topics. Fixed with Opportunity Scanner that reads real project files, error logs, and unused capabilities to propose typed work (build/ask/fix/connect/improve). Type-aware decomposition hints and "FUNCTIONAL OUTPUT PRIORITY" in PlanExecutor prompt. Needs live testing to confirm goals actually produce useful deliverables now.
- [x] **Fix shutdown not cancelling in-flight PlanExecutor tasks** — (Added 2026-02-20, session 59. Fixed 2026-02-20, session 59.) Root cause: `check_and_clear_cancellation()` used a single global flag with clear-on-read semantics, so with 2+ concurrent PlanExecutors, only the first to check saw the cancellation — the second kept running. Also `archi_service.stop()` never called `signal_task_cancellation()` directly (relied on the GoalWorkerPool.shutdown() call chain). Fix: (1) Added sticky "shutdown" mode — when `signal_task_cancellation("shutdown")` or `signal_task_cancellation("service_shutdown")` is called, the flag stays set so ALL concurrent executors see it. User cancels remain single-shot. (2) Added `signal_task_cancellation("service_shutdown")` call at the top of `archi_service.stop()` before dream cycle shutdown. (3) Added `clear_shutdown_flag()` at service startup so a previous shutdown doesn't block new tasks. Touches: `plan_executor.py`, `archi_service.py`.
- [x] **Fix recurring Unicode cp1252 encoding errors on Windows** — (Added 2026-02-20, session 59. Fixed 2026-02-20, session 59.) Fix: Set `PYTHONUTF8=1` in the subprocess environment for `_do_run_python()`. Python will now use UTF-8 for all file I/O in generated scripts regardless of Windows locale. Touches: `plan_executor.py`.
- [x] **Fix local MCP server failing to start** — (Added 2026-02-20, session 59. Fixed 2026-02-20, session 59.) Two likely causes: (1) bare `"python"` command in mcp_servers.yaml could resolve to the wrong interpreter or Windows Store stub, (2) no working directory set so `python -m src.tools.local_mcp_server` couldn't find the module. Fix: `_start_server()` now resolves `"python"`/`"python3"` to `sys.executable`, passes `cwd=project_root` when the MCP SDK supports it, and always sets `PYTHONUTF8=1` in the subprocess env. Also added try/except wrapper to `local_mcp_server.py`'s `__main__` block so startup errors go to stderr instead of silently dying. Touches: `mcp_client.py`, `local_mcp_server.py`.
- [x] **Fix tool registry re-initialization on every create_file call** — (Added 2026-02-20, session 59. Fixed 2026-02-20, session 59.) Every `ToolRegistry()` call created a fresh instance, re-initializing desktop, browser, image gen, and MCP connections. This happened in PlanExecutor, all 6 action handlers in action_dispatcher.py, and agent_loop.py — triggering full MCP server restarts mid-task (observed at step 22 in live logs). Fix: Added `get_shared_registry()` singleton accessor to `tool_registry.py` (thread-safe via lock). Replaced all 7 `ToolRegistry()` call sites with `get_shared_registry()`. One initialization, shared across all concurrent executors. Touches: `tool_registry.py`, `plan_executor.py`, `action_dispatcher.py`, `agent_loop.py`.
- [x] **Fix PlanExecutor task thrashing / file rewrite loops** — (Added 2026-02-20, session 59. Fixed 2026-02-20, session 59.) Per-path write counting with escalating intervention: at 3 writes → NOTE injected into prompt ("move on"), at 5 writes → WARNING ("stop rewriting"), at 7 writes → hard abort (loop detected, task stopped with partial work saved). Counted per step-history scan before each model call. Catches the exact pattern from the test run where supplement_info.json was rewritten 10+ times. Touches: `plan_executor.py`.
- [x] **Fix router misclassifying user statements as multi_step tasks** — (Added 2026-02-20, session 59. Fixed 2026-02-20, session 59.) When Jesse said "I'll see if we can figure out why it failed", the conversational router classified it as complex/multi_step instead of conversational. Archi spent 12 PlanExecutor steps re-reading files and running broken Python instead of just acknowledging. Fix: Added "USER STATEMENTS vs. REQUESTS" guidance to the router prompt distinguishing first-person statements about the user's own plans ("I'll look into that", "let me check") from requests directed at Archi ("look into why it failed", "can you figure out..."). Touches: `conversational_router.py`.
- [x] **Fix run_python creating files outside workspace/** — (Added 2026-02-20, session 59. Fixed 2026-02-20, session 59.) `_do_run_python` ran code with `cwd=base_path()` (Archi root), so LLM-generated scripts using relative paths like `projects/Health_Optimization/` created directories at the Archi root instead of inside `workspace/`. Fix: Changed cwd to `workspace/` and added project root to `PYTHONPATH` so `import src.*` still works. Updated run_python prompt to explain that relative paths resolve inside workspace/. Deleted stray `projects/` and `pytest-cache-files-*` directories from root. Touches: `plan_executor.py`.
- [x] **Artifact reuse: Archi can't find/use what it creates** — (Added 2026-02-21, session 60. Fixed 2026-02-21, session 60.) Three fixes: (1) `file_tracker.py` now stores `goal_description` in manifest entries and has `get_files_by_keywords(text)` for keyword search across tracked files. (2) `autonomous_executor.py` queries file tracker + scans project directory before each task, injecting "EXISTING FILES" and "FILES IN THIS PROJECT" hints. (3) Fixed critical bug in `plan_executor.py` where `ask_user` step history showed `[ask_user] -> done` instead of the actual reply text — model was literally forgetting what Jesse said. Touches: `file_tracker.py`, `autonomous_executor.py`, `plan_executor.py`.
- [x] **Initiative announcements don't explain what projects are** — (Added 2026-02-21, session 60. Fixed 2026-02-21, session 60.) Archi proposed work on projects (like "vision") without explaining what they were. Root cause: `_try_proactive_initiative()` in `dream_cycle.py` discarded `reasoning`, `user_value`, and `source` from opportunity scanner suggestions, only passing `description` and a generic `why`. Fix: extract these fields and pass them to `format_initiative_announcement()`, which now includes them in its LLM prompt so the model can explain the project context. Touches: `dream_cycle.py`, `notification_formatter.py`.
- [x] **Archi ignores "I need more time" replies to ask_user** — (Added 2026-02-21, session 60. Fixed 2026-02-21, session 60.) When Jesse replied "that'll take a few hours" to an ask_user question, Archi acknowledged it but then treated it as a timeout after 5 minutes. Four fixes: (1) `_do_ask_user()` in `plan_executor.py` detects temporal deferral signals ("later", "few hours", "tomorrow", etc.) and returns `{"deferred": True}`. (2) `Task` in `goal_manager.py` has new `deferred_until: Optional[datetime]` field (serialized/deserialized). (3) `autonomous_executor.py` parks deferred tasks by setting `deferred_until` and resetting to PENDING. (4) `get_ready_tasks()` filters out tasks where `deferred_until > now`. Touches: `plan_executor.py`, `goal_manager.py`, `autonomous_executor.py`.
- [x] **Test opportunity scanner live** — (Added session 42. Validated through live use across sessions 45, 59, 60.) Scanner has been running in production across multiple overnight runs. Session 45 found the relevance filter too aggressive (fixed). Sessions 59–60 fixes (initiative announcements, artifact reuse, deferred replies) were all based on observing scanner-driven goals running live. Build/ask/fix types confirmed working; formal fallback test not done separately but scanner is validated through real use.
- [x] **Test concurrent goals** — (Confirmed session 45 log analysis) 4 goals created in ~2 min, worker pool ran them concurrently ("Submitting goal X to worker pool" for each). Chat ("What time is it?") still routed correctly mid-execution. Shutdown was clean (Ctrl+C at 06:48, graceful signal received). goals_state.json not corrupted (goals resumed correctly after overnight restart at 06:38).
- [x] **Test wave-based parallelism** — (Confirmed session 45 log analysis) goal_2 showed "WAVE 1: 2 tasks in PARALLEL" with task_1 and task_2 fanning out across 2 threads. goal_1 ran waves sequentially (task_4 first, then task_5/task_6 in wave 2).
- [x] **Test ask_user tool** — (Confirmed session 45 log analysis) Multiple ask_user calls sent Discord questions successfully, blocked waiting for reply, and correctly timed out with "Jesse didn't respond" after ~5 min. Remaining issue: cross-goal dedup (see new TODO item).
- [x] **Test proactive initiative** — (Confirmed session 45 log analysis) At 22:45, Archi self-initiated "Write optimizer.py script..." with notification "I decided to work on something" and cost estimate. Stayed within budget. Task failed (run_python errors), but the initiative mechanism itself worked correctly.
- [x] **Fix PLAN_MAX_TOKENS truncation killing all tasks** — (Session 43) Root cause: `PLAN_MAX_TOKENS = 1000` was too low for Grok's reasoning model, which spends tokens on `<think>` blocks before producing JSON. Responses hitting exactly 1000 output tokens got truncated mid-JSON, `extract_json()` failed, retry also hit 1000, task force-aborted. 27 instances in one run. Fix: raised to 4096. Cost impact: ~$0.003/step max → ~$0.012/step max, well within per-cycle budget.
- [x] **Fix write_source/edit_file not tracked by path in loop detection** — (Session 43) `write_source` and `edit_file` action keys didn't include the file path, so writing to different files looked like the same action to the loop detector. Added path-based keying (matching `create_file`/`append_file`). Also extended write-then-read exemption to cover `write_source` and `edit_file` so the healthy verify pattern (write → read back) isn't penalized.
- [x] **Add SSL cert diagnostic logging** — (Session 43) arxiv.org still failing with CERTIFICATE_VERIFY_FAILED despite session 41 fix. Added diagnostic logging to show whether certifi loaded successfully or fell back to system default. Will help diagnose on next run.
- [x] **Fix rewrite-loop detection (total writes per path)** — (Session 43) After max_tokens fix, Grok's new failure mode was rewriting the same output file 5-10+ times without calling `done`, breaking the consecutive detector by inserting reads/searches between writes. Added total-writes-per-path counter: nudge at 3, strong at 5, kill at 7.
- [x] **Fix ask_user consuming unrelated chat messages** — (Session 44) "What time is it?" was eaten by pending `ask_user` listener and got "👍 Got it, thanks!" instead of the time. Root cause: `_check_pending_question()` greedily consumed ANY non-empty message when a question was pending. Fix: added `_is_likely_new_command()` heuristic that checks for datetime patterns, slash commands, goal-creation phrases, image gen, stop/cancel, etc. These now fall through to normal message processing.
- [x] **Fix duplicate ask_user spam across concurrent tasks** — (Session 44) 4 near-identical "What's your supplement stack?" messages sent because concurrent tasks under the same goal independently called `ask_user`. Fix: if another task already has a pending question, new `ask_user` callers piggyback on the existing one (wait for the same answer) instead of sending another Discord message.
- [x] **Fix write_source producing incomplete/truncated code** — (Session 44) Tasks asking Grok to write complex Python scripts (CLI tools, parsers, report generators) produced code that cut off mid-function. Quality 4-5/10, verification correctly failed them. Root cause: task decomposer created tasks too large for a single write_source call. Fix: added "CODE SIZE — KEEP write_source SMALL" to decomposition prompt (under 80 lines, break into multiple tasks), and "KEEP SCRIPTS SHORT" + "use edit_file/append_file to continue" guidance in PlanExecutor system prompt.
- [x] **Cross-goal ask_user dedup** — (Added 2026-02-19, session 45. Fixed 2026-02-19, session 45.) Session 44 piggyback dedup only worked within one goal's tasks. Fix: added `_recent_questions` history with 10-min cooldown and Jaccard word-overlap similarity (threshold 0.5). If a similar question was already asked (even by a different goal, even if it timed out), `ask_user()` returns None immediately. Touches: `discord_bot.py`.
- [x] **PlanExecutor needs OS/shell awareness** — (Added 2026-02-19, session 45. Fixed 2026-02-19, session 45.) Grok tried Unix commands on Windows. Fix: added "ENVIRONMENT: Windows. Do NOT use Unix commands" to PlanExecutor system prompt, updated `run_command` description to say Unix commands WILL FAIL — use run_python with os/pathlib. Touches: `plan_executor.py`.
- [x] **Opportunity scanner relevance filter too aggressive** — (Added 2026-02-19, session 45. Fixed 2026-02-19, session 45.) `is_goal_relevant()` rejected all 7 ideas (literal substring match on project keys). Fix: word-level matching against project metadata, auto-pass self-improvement ideas, populated `focus_areas` in project_context.json. Touches: `idea_generator.py`, `data/project_context.json`.
- [x] **Strengthen "move on from failed fetches" enforcement** — (Added 2026-02-19, session 45. Fixed 2026-02-19, session 45.) Grok ignored the soft prompt rule. Fix: scan step history for failed domains and repeated similar searches, inject hard warnings ("BLOCKED DOMAINS: ..." and "STOP searching — use what you have"). Touches: `plan_executor.py`.
- [x] **edit_file fails when Grok guesses file contents** — (Added 2026-02-19, session 45. Fixed 2026-02-19, session 45.) Grok didn't read before editing. Fix: (1) added read-first rule to prompt, (2) enforced in step handler — edit_file rejected if target file not read in last 8 steps. Touches: `plan_executor.py`.
- [x] **Make Archi's Discord messages less spammy and more conversational** — (Added pre-session 46. Fixed 2026-02-19, session 46.) Consolidated notifications from per-task to per-goal (one batched message). Removed intermediate progress spam from `autonomous_executor.py`. Rewrote all notification templates to conversational tone: morning report, hourly summary, proactive findings, initiative announcements, ask_user, source approval requests, interrupted task recovery. Updated chat system prompt in `message_handler.py`. Touches: `goal_worker_pool.py`, `autonomous_executor.py`, `reporting.py`, `message_handler.py`, `discord_bot.py`, `dream_cycle.py`.

- [x] **Store conversation context in long-term memory** — (Added pre-session 42. Fixed 2026-02-21, session 61.) Two write paths added: (1) Notable chat messages — message_handler stores conversations to vector store when the Router extracts user signals (notability filter). Uses shared MemoryManager injected via `set_memory()` from archi_service. (2) Dream cycle synthesis insights — stored to long-term memory after synthesis logging. Memory type tags: "conversation" and "dream_summary". New: `message_handler.set_memory()`. Touches: `message_handler.py`, `archi_service.py`, `dream_cycle.py`.
- [x] **Wire user_preferences into project_context** — (Added pre-session 42. Fixed 2026-02-21, session 61.) Lightweight keyword-matching hook runs after `extract_user_signals()` in the Conversational Router. Detects project-related preferences (deactivate/boost/new interest) by matching signal text against active project names and intent phrases. Updates `project_context.json` atomically — deactivates, boosts priority, or adds interests. No extra model call. New file: `src/utils/project_sync.py`. New test: `tests/unit/test_project_sync.py` (16 tests). Touches: `conversational_router.py`.
- [x] **Tiered model routing — Claude Sonnet 4.6 escalation** — (Added 2026-02-21, session 62. Fixed 2026-02-21, session 62.) Two escalation triggers: (1) QA rejection retry escalates entire retry to Claude Sonnet 4.6 via OpenRouter, with prior attempt summary (searches, file writes, files created) + QA feedback as hints. (2) Schema retry exhaustion makes one final Claude attempt before failing. Implementation: `router.escalate_for_task()` context manager (snapshot/restore), updated `providers.py` with claude-sonnet-4.6 alias + pricing ($3/$15 per 1M tokens), increased hints-per-step from 2→5. 12 new tests. Touches: `router.py`, `providers.py`, `autonomous_executor.py`, `plan_executor.py`. New: `tests/unit/test_tiered_escalation.py`.
- [x] **Idea History + Retry-with-Feedback** — (Added 2026-02-21, session 63. Fixed 2026-02-21, session 63.) Persistent idea ledger tracking every suggestion Archi has ever floated, with outcomes: auto-filtered (and why), user-accepted, user-rejected, user-ignored. Three components: (1) `data/idea_history.json` — append-only ledger of all ideas with status, filter reason, timestamps. Updated when ideas are auto-filtered, when user picks a suggestion, and when suggestions expire without response. (2) Idea filtering consults history — before presenting ideas, check if similar ones were already tried and what happened ("tried 3x, never accepted", "user said no", "already built this"). Invalidate query cache when all ideas rejected so next cycle gets fresh LLM output. (3) Retry-with-feedback loop — when all ideas are rejected (either auto-filtered or user-ignored/declined), next brainstorm prompt includes what was rejected and why, nudging the LLM toward genuinely new ideas. Up to 2 retries before escalating to Claude for a more creative pass. Also: multi-pick support (user can approve multiple suggestions at once via "do 1 and 3" or "all of them"), old suggestion recovery (user can come back and accept previously ignored suggestions), QA rejection feedback (failed goals feed back into idea history). New file: `src/core/idea_history.py`, `tests/unit/test_idea_history.py` (21 tests). Touches: `idea_generator.py`, `dream_cycle.py`, `discord_bot.py`, `cache.py`, `conversational_router.py`, `goal_worker_pool.py`, `reset.py`.
- [x] **Adaptive suggestion cooldown with exponential backoff** — (Added 2026-02-21, session 63. Fixed 2026-02-21, session 63.) When user doesn't respond to suggestions, Archi increases the delay between suggestion messages exponentially: 10min → 20min → 40min → 80min → ... up to 4-hour cap. Any user message resets the cooldown back to 10 minutes. State tracked in dream_cycle: `_suggest_cooldown`, `_unanswered_suggest_count`, `_suggest_cooldown_base` (600s), `_suggest_cooldown_max` (14400s). Backoff formula: `base * 2^unanswered_count`. Reset wired into discord_bot's `on_message` handler via `reset_suggest_cooldown()` called alongside `mark_activity()`. Touches: `dream_cycle.py`, `discord_bot.py`, `idea_generator.py` (accepts `cooldown_secs` param).
- [ ] **Discord command to add/remove projects** — Let Jesse manage active_projects via chat instead of editing JSON manually.
- [ ] Add more direct provider tests (Anthropic, DeepSeek, etc.)

---

## Completed Work

<details>
<summary>Session 62 (Cowork) — Tiered model routing: Claude Sonnet 4.6 escalation on failure</summary>

- [x] **Tiered model routing** — Added Claude Sonnet 4.6 via OpenRouter as automatic escalation tier. Two trigger points: (1) QA rejection retry runs entirely on Claude with full prior attempt context. (2) Schema retry exhaustion makes one final Claude attempt. Context manager `router.escalate_for_task()` snapshots/restores model state. Prior attempt summary (key actions + files created) injected as hints so Claude doesn't redo work. Hints-per-step increased from 2→5.
- [x] **Claude context gap analysis** — Traced both escalation paths to verify Claude gets full context. Schema path: full step prompt (good). QA retry path: had gaps — Claude started with empty step history and only 2 hints visible. Fixed both.
- [x] **Documentation update** — Updated ARCHITECTURE.md (tiered routing section, QA section, deferred systems table, design decisions, testing count), SESSION_CONTEXT.md (session 62 summary), TODO.md.

**Files created:** `tests/unit/test_tiered_escalation.py`
**Files modified:** `src/models/router.py`, `src/models/providers.py`, `src/core/autonomous_executor.py`, `src/core/plan_executor.py`

</details>

<details>
<summary>Session 61 (Cowork) — User preferences → project context + conversation long-term memory</summary>

- [x] **Wire user_preferences into project_context** — New `src/utils/project_sync.py` (~60 lines). Hooks into Conversational Router after `extract_user_signals()`. Keyword-matches preference signals against active project names + intent phrases (deactivate: "done with", "not doing"; boost: "focus on", "prioritize"; new interest: "interested in", "want to try"). Updates `project_context.json` atomically via existing `save()`. No extra model call. 16 unit tests.
- [x] **Store conversation context in long-term memory** — Two write paths: (1) Notable chat messages stored to vector store when Router extracts user signals (notability filter). Shared MemoryManager injected via new `message_handler.set_memory()`, called from `archi_service.py` after memory init. (2) Dream cycle synthesis insights stored after synthesis logging with type="dream_summary".
- [x] **TODO cleanup** — Marked "test opportunity scanner live" (validated through live use across sessions 45/59/60) and "review architecture for better approaches" (completed across 9-phase evolution + session 58 audit) as done. Promoted 4 future ideas to open items.

**Files created:** `src/utils/project_sync.py`, `tests/unit/test_project_sync.py`
**Files modified:** `src/core/conversational_router.py`, `src/interfaces/message_handler.py`, `src/service/archi_service.py`, `src/core/dream_cycle.py`

</details>

<details>
<summary>Session 60 (Cowork) — Artifact reuse, project explanations, deferred replies</summary>

- [x] **Artifact reuse** — FileTracker stores `goal_description` in manifest, has `get_files_by_keywords()` for keyword search. autonomous_executor queries file tracker + scans project directory before each task, injecting file awareness hints. Fixed ask_user step history to show actual reply text.
- [x] **Project explanations** — Initiative announcements now pass `reasoning`, `user_value`, and `source` from opportunity scanner through to the notification formatter. LLM prompt updated to explain project context.
- [x] **Deferred replies** — Temporal signal detection in ask_user returns `{"deferred": True}`. Task.deferred_until field with serialization. Task parking in autonomous_executor. get_ready_tasks() filters deferred tasks until resume time.

**Files modified:** `src/core/file_tracker.py`, `src/core/autonomous_executor.py`, `src/core/plan_executor.py`, `src/core/dream_cycle.py`, `src/core/notification_formatter.py`, `src/core/goal_manager.py`

</details>

<details>
<summary>Session 58 (Cowork) — Verification Patch-Up</summary>

- [x] **File Security: blacklist → whitelist** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) `_validate_path_security()` in `tool_registry.py` changed from blocking known system directories to whitelist approach: resolves canonical path via `os.path.realpath()` and verifies it starts with `paths.base_path()`. Rejects everything else. ~10 lines changed.
- [x] **DAG priority preemption** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) Added dedicated `_reactive_executor` (1 thread) to `GoalWorkerPool` for user-requested goals. Proactive goals use the main executor. Reactive goals start immediately without waiting for background work to release a slot. Shutdown updated to clean up both executors.
- [x] **User Model → Notification Formatter** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) Added `get_context_for_formatter()` to `user_model.py` (returns style notes and preferences). Injected into `_call_formatter()` in `notification_formatter.py` so all notifications adapt to Jesse's communication style.
- [x] **User Model → Discovery** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) Added `get_context_for_discovery()` to `user_model.py` (returns project preferences and patterns). Wired into `_rank_files()` in `discovery.py` — user preference keywords boost file relevance scores.
- [x] **Response builder prefix documented** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) Grepped codebase: `build_response()` with `action_prefix` is only called from `message_handler.py` (3 paths: multi_step, coding, non-chat actions). No non-Discord callers. Updated docstring to document why prefix logic is retained and which callers use it.
- [x] **Integrator glue surfacing** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) `integrate_goal()` now surfaces `missing_glue` in its return dict instead of silently discarding it. Added logging for detected glue items. Auto-creation evaluated and deferred — detection + reporting is sufficient since workers handle file creation.
- [x] **Step cap spec alignment** — (Added 2026-02-20, session 58. Fixed 2026-02-20, session 58.) Spec originally said 30. Implementation uses 50/25/12 — deliberate choice from session 32 (budget and time caps are primary constraints). Updated `ARCHITECTURE_PROPOSAL.md` to document tiered caps.
- [x] **Zero new test failures** — 490 pass, same pre-existing failures. All changes verified.

**Files modified:** `src/tools/tool_registry.py`, `src/core/goal_worker_pool.py`, `src/core/task_orchestrator.py` (docstring), `src/core/user_model.py`, `src/core/notification_formatter.py`, `src/core/discovery.py`, `src/interfaces/response_builder.py` (docstring), `src/core/integrator.py`, `claude/ARCHITECTURE_PROPOSAL.md`, `claude/VERIFICATION_REPORT.md`, `claude/SESSION_CONTEXT.md`, `claude/TODO.md`

</details>

<details>
<summary>Session 57 (Cowork) — Architecture Evolution Phase 9: Cleanup</summary>

- [x] **Heartbeat simplified to 2-tier** — Removed deep sleep tier (monitoring_mode), evening multiplier, `_time_of_day_multiplier()`, and work_hours/evening config sections. Two tiers remain: command (10s for 2 min after user interaction) and idle (60s). Night mode override (1800s) preserved. `heartbeat.yaml` simplified from ~40 lines to ~25. `heartbeat.py` rewritten from ~160 lines to ~110.
- [x] **Intent classifier legacy model-classify removed** — Removed `_model_classify()` function (~50 lines) and `_INTENT_INSTRUCTION` prompt string (~20 lines). These were superseded by the Conversational Router (Phase 4) but kept as fallback. `classify()` now returns `IntentResult(action="chat_fallback")` for non-fast-path messages instead of making a redundant model call. All fast-paths (datetime, greeting, screenshot, image gen, deferred requests, slash commands) preserved unchanged. Two tests removed (`TestScreenshotInIntentClassifier` — tested removed `_INTENT_INSTRUCTION`).
- [x] **Discord stub comments removed** — Three Phase 4 "Removed in Phase 4" breadcrumb comments deleted from `discord_bot.py` (~lines 773, 784, 987).
- [x] **Router legacy params removed** — `force_api` and `use_reasoning` parameters removed from `ModelRouter.generate()` signature. One call site updated (`message_handler.py` line 285). Stale `_temp_previous` comment fixed (`force_api` → `force_api_override`).
- [x] **`_force_aborted` renamed to `_schema_retries_exhausted`** — Renamed throughout `plan_executor.py` (5 references) and `qa_evaluator.py` (1 reference). Result dict key changed from `"force_aborted"` to `"schema_retries_exhausted"`. Stale "loop detection, think-loop" comment updated to accurately describe the current trigger (JSON schema retries exhausted).
- [x] **Dead code sweep** — Removed 7 unused imports: `re` from `message_handler.py`, `time` and `List` from `router.py`, `Path` from `dream_cycle.py`, `os`/`queue`/`field` from `goal_worker_pool.py`.
- [x] **Zero new test failures** — 475 pass, 11 fail (same pre-existing failures). 2 tests removed (tested deleted code). All spot-checks pass.

**Files modified:** `src/core/heartbeat.py`, `config/heartbeat.yaml`, `src/interfaces/intent_classifier.py`, `src/interfaces/discord_bot.py`, `src/models/router.py`, `src/interfaces/message_handler.py`, `src/core/plan_executor.py`, `src/core/qa_evaluator.py`, `src/core/dream_cycle.py`, `src/core/goal_worker_pool.py`, `tests/unit/test_screenshot.py`

</details>

<details>
<summary>Session 56 (Cowork) — Architecture Evolution Phase 8: Graceful Degradation</summary>

- [x] **Provider Fallback Chain** (`src/models/fallback.py`, ~230 lines) — When the primary provider fails, cascades through a priority-ordered chain of backup providers. Default chain: xai → openrouter → deepseek → openai → anthropic → mistral (only providers with API keys in .env are included). Per-provider CircuitBreaker instances (3 consecutive failures → circuit OPEN). Exponential backoff on recovery attempts: 30s → 60s → 120s → 300s cap. Auto-recovery: successful call resets breaker and restores primary. Thread-safe with threading.Lock. State-change callback fires on degradation events (degraded, recovered, total_outage). Manual reset via `reset_provider()`.
- [x] **Router integration** — `_use_api()` in `router.py` now routes through the fallback chain via `call_with_fallback()`. If user has force-overridden to a specific model, tries that first then falls through to chain. On total outage (all providers down), checks cache as last resort (returns cached response with `degraded: True` flag). If nothing works, returns friendly user-facing error message. New helper `_record_success()` consolidates stats tracking. New methods: `get_provider_health()`, `is_degraded()`, `all_providers_down()`, `_get_or_create_client()`.
- [x] **Degraded mode visibility** — Discord "what model"/"status"/"api status" now shows provider health with icons: 🟢 closed (healthy), 🔴 open (down), 🟡 half_open (testing recovery). Shows "⚠️ Running in degraded mode" when operating on a non-primary provider. Default degradation handler sends Discord notifications (⚠️ degraded, ✅ recovered, 🔴 total outage) via `send_notification()`.
- [x] **Dream cycle pause** — `_should_run_cycle()` checks `_all_providers_down()` before running. When all providers are down, skips the cycle entirely (logs "All LLM providers down — skipping dream cycle"). `get_status()` now includes `all_providers_down` field for monitoring. Prevents burning budget on retries during outages.
- [x] **Zero new test regressions** — 477/488 tests pass (same 11 pre-existing failures). All imports verified clean.

**Files created:** `src/models/fallback.py`
**Files modified:** `src/models/router.py`, `src/interfaces/discord_bot.py`, `src/core/dream_cycle.py`

</details>

<details>
<summary>Session 55 (Cowork) — Architecture Evolution Phase 7: MCP Tool Integration</summary>

- [x] **MCP Client** (`src/tools/mcp_client.py`, ~320 lines) — Async client that connects to MCP servers via stdio transport. Manages server lifecycle: start on first use, stop after configurable idle timeout. Background event loop in dedicated daemon thread bridges sync callers (PlanExecutor) to the async MCP SDK. `MCPClientManager` handles multiple concurrent servers, tool discovery via `list_all_tools()`, and tool execution via `call_tool()`. Graceful cleanup catches `CancelledError` from anyio task groups.
- [x] **Local MCP Server** (`src/tools/local_mcp_server.py`, ~230 lines) — Wraps Archi's existing tools as a FastMCP server using `@mcp.tool()` decorators. Exposes: `read_file`, `create_file`, `list_files`, `web_search`, `desktop_click/type/screenshot/hotkey/open`, `browser_navigate/click/fill/screenshot/get_text`, `desktop_click_element`. Each tool returns JSON-serialized results matching the existing `{success: bool, ...}` contract. Image generation excluded (privacy — NSFW prompts stay local). Runs via `python -m src.tools.local_mcp_server` with stdio transport.
- [x] **Tool Registry refactor** (`src/tools/tool_registry.py`) — MCP-aware execution layer. `initialize_mcp()` loads `config/mcp_servers.yaml`, starts configured servers, and discovers tools. `execute()` routing: (1) direct-only tools (generate_image) always direct, (2) MCP-backed tools route through MCP client, (3) direct tools as fallback, (4) MCP-only tools (no direct equivalent). Background event loop via `_get_mcp_loop()` in daemon thread — `_run_async()` bridges sync→async with 60s timeout. `shutdown_mcp()` for clean teardown.
- [x] **MCP Server Config** (`config/mcp_servers.yaml`) — Declarative config for MCP servers: `command`, `args`, `env` (with `${VAR}` resolution from os.environ), `idle_timeout`, `enabled`, `exclude_tools`. Local server (always-on) and GitHub server (5min idle timeout) configured. Adding a new MCP server = adding a config entry.
- [x] **GitHub MCP server** — `@modelcontextprotocol/server-github` wired as first external server. Provides 20+ tools: list_issues, get_issue, create_issue, create_pull_request, get_file_contents, search_code, etc. Requires `GITHUB_PERSONAL_ACCESS_TOKEN` in `.env`.
- [x] **PlanExecutor MCP fallback** — `_execute_action()` now routes unknown actions to `self.tools.execute()` instead of returning "Unknown action" error. Enables automatic support for any MCP-provided tool without explicit action handlers.
- [x] **Safety updates** — GitHub read-only tools (list_issues, get_file_contents, etc.) added to L1_LOW in `rules.yaml`. GitHub write tools (create_issue, create_pull_request, etc.) added to L2_MEDIUM. `config/mcp_servers.yaml` added to protected files list.
- [x] **End-to-end verified** — 41 tools discovered from 2 MCP servers (local + GitHub). `read_file` call through MCP bridge succeeded. 477/488 tests pass (11 pre-existing failures unrelated to MCP).

**Files created:** `src/tools/mcp_client.py`, `src/tools/local_mcp_server.py`, `config/mcp_servers.yaml`
**Files modified:** `src/tools/tool_registry.py`, `src/core/plan_executor.py`, `src/core/agent_loop.py`, `config/rules.yaml`, `.env.example`, `requirements.txt`

</details>

<details>
<summary>Session 53 (Cowork) — Architecture Evolution Phase 5: Planning + Scheduling</summary>

- [x] **Discovery Phase** (`src/core/discovery.py`, ~280 lines) — Scans project files before the Architect runs. Matches goal keywords to active projects in project_context.json, enumerates files (skipping .git, __pycache__, etc.), ranks by relevance (entry points, READMEs, keyword matches, recency), reads selectively (Python structure extraction for code files, full content for docs), compresses into a structured brief via one model call. Deterministic fallback if model fails. Feeds into decompose_goal().
- [x] **Architect Enhancement** — Rewrote decompose_goal() in `goal_manager.py`. Now accepts `discovery_brief` (from Discovery) and `user_prefs` (from User Model). Prompt upgraded to "You are the Architect" — requires concrete specs per task: `files_to_create`, `inputs`, `expected_output`, `interfaces`. Task class extended with these 4 fields, serialized/deserialized in to_dict/_load_state. max_tokens raised from 1000→1500 for richer specs.
- [x] **Event-Driven DAG Scheduler** — Full rewrite of `task_orchestrator.py`. Replaced wave-based batching (gather all ready → run wave → gather again) with persistent ThreadPoolExecutor + as_completed() event loop. When a task finishes, immediately checks which pending tasks are now unblocked and submits them — no waiting for wave boundaries. Stops on 3 consecutive failures (more nuanced than "all tasks in wave failed"). _submit_ready_tasks() respects max_parallel slots and snapshots sibling context at submission time.
- [x] **Request Prioritization** — `submit_goal()` and `kick()` accept `reactive: bool` parameter. All user-initiated call sites (action_dispatcher.py, message_handler.py deferred requests, message_handler.py auto-escalation, discord_bot.py suggestion picks) pass `reactive=True`. Dream cycle self-initiatives stay `reactive=False` (default). Reactive flag logged and tracked in GoalWorkerState for monitoring.
- [x] **Spec hints in PlanExecutor** — `autonomous_executor.py` now injects Architect spec fields (files_to_create, inputs, expected_output, interfaces) as hints into PlanExecutor, so workers execute against concrete specs instead of discovering what to build mid-execution.
- [x] **Discovery wiring** — `goal_worker_pool._execute_goal()` runs Discovery→User Model→Architect pipeline before the DAG scheduler. Discovery cost tracked in GoalWorkerState.

**Files created:** `src/core/discovery.py`
**Files modified:** `src/core/goal_manager.py`, `src/core/task_orchestrator.py`, `src/core/goal_worker_pool.py`, `src/core/autonomous_executor.py`, `src/core/dream_cycle.py`, `src/interfaces/action_dispatcher.py`, `src/interfaces/message_handler.py`, `src/interfaces/discord_bot.py`

</details>

<details>
<summary>Session 50 (Cowork) — Architecture Evolution Phase 3: Notifications + Feedback</summary>

- [x] **Notification Formatter** (`src/core/notification_formatter.py`, ~370 lines) — Single model call per notification via Grok 4.1 Fast (~$0.0002/call). Takes structured data (event type, results, stats) and produces a conversational message matching Archi's persona (warm, concise, varied phrasing, no markdown/bullet formatting). Every notification type has a deterministic fallback string so notifications still send if the model call fails.
- [x] **Migrated all notification paths** — Goal completion, morning report, hourly summary, work suggestions, idle prompt, finding notifications, initiative announcements, interrupted task recovery, and decomposition failure messages all route through the formatter. Removed ~100 lines of hardcoded template strings from `reporting.py`, `goal_worker_pool.py`, and `dream_cycle.py`.
- [x] **Reaction-based feedback** — Added `on_raw_reaction_add` handler to Discord bot. Tracked notification messages (stored by message ID) detect 👍/👎/❤️/🎉/🔥/😕/😞 reactions from Jesse and record them as learning feedback via `learning_system.record_feedback()`. `send_notification()` now accepts optional `track_context` dict to register messages for tracking. Added `dm_reactions` and `reactions` intents.
- [x] **Significant goal feedback prompt** — Goals with 3+ tasks or 10+ minutes of work append "Anything you'd change?" to completion messages, prompting Jesse to provide directed feedback.
- [x] **Router passthrough** — `send_morning_report()`, `send_hourly_summary()`, and `send_finding_notification()` now accept an optional `router` parameter. Dream cycle passes its router to these calls so the formatter can make model calls.

**Files created:** `src/core/notification_formatter.py`
**Files modified:** `src/interfaces/discord_bot.py`, `src/core/goal_worker_pool.py`, `src/core/reporting.py`, `src/core/dream_cycle.py`

</details>

<details>
<summary>Session 49 (Cowork) — Architecture Evolution Phase 2: QA + Critic</summary>

- [x] **QA Evaluator** (`src/core/qa_evaluator.py`, ~200 lines) — Post-task quality gate. Layer 1: deterministic checks (files exist, Python syntax valid, not empty/truncated, done summary present). Layer 2: model-based semantic evaluation (does output actually accomplish the task?). Returns accept/reject/fail. On rejection, task retries once with QA feedback injected as hints so the model knows exactly what to fix.
- [x] **Critic** (`src/core/critic.py`, ~150 lines) — Adversarial per-goal evaluation after all tasks complete. Prompts model to find real problems: output that just looks busy, code that won't run, wrong approach, things Jesse wouldn't use. Severity levels: none, minor (logged only), significant (adds up to 2 remediation tasks and re-runs orchestrator for fix-up pass).
- [x] **QA wiring** — Inserted into `autonomous_executor.execute_task()` after PlanExecutor returns but before learning system recording. On QA rejection, creates new PlanExecutor with QA feedback as hints, retries task once. On QA fail, marks task as failure. QA cost tracked in total task cost.
- [x] **Critic wiring** — Inserted into `goal_worker_pool._execute_goal()` after orchestrator finishes but before notification. Gathers all task results and files for the goal, runs critique. On significant severity, adds remediation tasks and re-runs orchestrator within remaining budget.
- [x] **Loop detection removal** — Removed ~120 lines from PlanExecutor: consecutive action key tracking (`_recent_action_keys`, `_WARN_AT`/`_STRONG_WARN_AT`/`_KILL_AT`), rewrite-loop tracking (`_path_write_counts`, `_PATH_WRITE_WARN`/`_PATH_WRITE_STRONG`/`_PATH_WRITE_KILL`), all warning injection logic, and both force-abort code paths. Kept hard step cap of 50 and `_force_aborted` flag (now only set on JSON retry exhaustion). Mechanical error recovery hints now stored in step record's `error_hint` field and injected by `_build_step_prompt`.

**Files created:** `src/core/qa_evaluator.py`, `src/core/critic.py`
**Files modified:** `src/core/autonomous_executor.py`, `src/core/goal_worker_pool.py`, `src/core/plan_executor.py`

</details>

<details>
<summary>Session 48 (Cowork) — Architecture Evolution Phase 1: PlanExecutor Internals + Security</summary>

- [x] **Context Compression** — After step 8, older steps are compressed to one-liners (action + outcome only, no snippets). Most recent 5 steps retain full fidelity. Saves ~200-500 tokens per compressed step, preventing prompt bloat on long tasks. New `_compress_step()` static method.
- [x] **Structured Output Contracts** — New `src/core/output_schemas.py` with `ACTION_SCHEMAS` dict mapping every action to required fields+types and `validate_action()` function. Wired into PlanExecutor step handler: validates model JSON before dispatch, auto re-prompts with targeted schema error messages, caps at 2 retries (up from 1).
- [x] **Mechanical Error Recovery** — New `_classify_error()` function classifies action failures as transient (network/timeout → retry with 2s backoff, no step burned), mechanical (file not found, syntax error → targeted fix hint injected into next prompt), or permanent (protected file, blocked command → fail immediately). ~60 lines, rule-based, no model call.
- [x] **Reflection Prompt** — Added self-check checklist to the "done" action description in the step prompt: re-read task, verify files exist with correct content, test code, check for gaps. Model must confirm all checks pass before calling done.
- [x] **File Security Hardening** — Added `os.path.realpath()` symlink resolution to `_resolve_workspace_path()` and `_resolve_project_path()` (boundary check now uses resolved real paths). Added `_validate_path_security()` in `tool_registry.py` blocking system directories (/etc/, /usr/, C:\Windows, etc.) with defense-in-depth checks on FileReadTool and FileWriteTool.

**Files created:** `src/core/output_schemas.py`
**Files modified:** `src/core/plan_executor.py`, `src/tools/tool_registry.py`

</details>

<details>
<summary>Session 47 (Cowork) — Fix _step_history crash, suggestion pick, doubled responses, descriptive notifications</summary>

- [x] **Fixed PlanExecutor _step_history crash** — `self._step_history` was never initialized, causing `AttributeError` on step 1 of every task. Added `self._step_history = steps_taken` after crash recovery block.
- [x] **Fixed suggestion pick affirmative routing (two rounds)** — First: added exact-match affirmatives with 40-char limit. Second: added substring matching for longer messages ("I have no idea what that is, but go ahead I guess") — checks for phrases like "go ahead", "do it", "sounds good" anywhere in the message.
- [x] **Fixed doubled create_goal response** — Model prefix + hardcoded response produced doubled messages. Skip prefix for `create_goal` and `generate_image` actions.
- [x] **Made goal notifications descriptive** — Completion messages now show task "Done:" summaries (what was built, how to use it) instead of bare filenames. Falls back to filenames if no summaries available. Updated PlanExecutor "done" prompt to instruct Grok to explain files and usage.
- [x] **Third pass on Discord message tone** — Removed remaining emoji prefixes, bold markdown, verbose echoes from all notification paths.

**Files modified:** `src/core/plan_executor.py`, `src/interfaces/discord_bot.py`, `src/interfaces/action_dispatcher.py`, `src/interfaces/message_handler.py`, `src/core/dream_cycle.py`, `src/core/goal_worker_pool.py`, `src/core/reporting.py`

</details>

<details>
<summary>Session 46 (Cowork) — Discord message spam reduction & conversational tone</summary>

- [x] **Consolidated goal notifications** — Replaced per-task notifications in `goal_worker_pool.py` with a single `_notify_goal_result()` that sends ONE message per goal covering successes, failures, and budget status. Removed separate failure/budget-pause messages.
- [x] **Removed per-task spam from autonomous_executor** — Removed per-task `❌ Task failed` notifications and per-goal completion notifications (now handled by goal_worker_pool).
- [x] **Rewrote reporting.py** — Morning report: natural greeting summarizing the night instead of rigid template. Hourly summary: conversational headline. Finding notifications: "Hey — came across something" instead of system alert. User goal completion: warmer phrasing.
- [x] **Updated chat system prompt** — Rewrote Communication section: "Talk like a person, not a bot", don't restate what user said, match their energy, skip filler phrases.
- [x] **Rewrote discord_bot.py messages** — ask_user: "Quick question — ..." not "❓ **I have a question:**". Source approval: "I want to modify a source file — need your OK." Interrupted task recovery: "Looks like I was in the middle of something before the restart."
- [x] **Rewrote proactive initiative message** — One-liner format instead of multi-line system notification.

**Files modified:** `src/core/goal_worker_pool.py`, `src/core/autonomous_executor.py`, `src/core/reporting.py`, `src/core/dream_cycle.py`, `src/interfaces/message_handler.py`, `src/interfaces/discord_bot.py`

</details>

<details>
<summary>Session 44 (Cowork) — Third live test round: ask_user routing, dedup spam, write_source truncation</summary>

- [x] **Log analysis: 8/12 tasks completed** — Major improvement from prior runs (0% → 67% success). Max_tokens fix working perfectly (zero JSON truncation). Rewrite-loop detection firing correctly. Total cost ~$0.17 for 5 goals.
- [x] **Fixed ask_user consuming chat messages** — Added `_is_likely_new_command()` heuristic to `_check_pending_question()` in `discord_bot.py`. Checks for datetime patterns, slash commands, goal-creation phrases, image gen starters, stop/cancel. Messages matching these patterns fall through to normal routing instead of being eaten by the ask_user listener.
- [x] **Fixed duplicate ask_user spam** — Added piggyback dedup to `ask_user()` in `discord_bot.py`. When a question is already pending (another task asked first), new callers wait for the existing answer instead of sending a duplicate Discord message. Thread-safe with double-checked locking.
- [x] **Fixed write_source incomplete code** — Two-part fix: (1) Decomposition prompt (`goal_manager.py`) now includes "CODE SIZE — KEEP write_source SMALL" section: under 80 lines per call, break into multiple tasks for larger programs. Also "ask_user — DON'T DUPLICATE QUESTIONS" section. (2) PlanExecutor system prompt (`plan_executor.py`) now includes "KEEP SCRIPTS SHORT" guidance: use append_file/edit_file to add features incrementally, never rewrite entire file when code is truncated.

**Files modified:** `src/interfaces/discord_bot.py`, `src/core/goal_manager.py`, `src/core/plan_executor.py`

</details>

<details>
<summary>Session 43 (Cowork) — Fix task failures: max_tokens truncation, loop detection, SSL diagnostics</summary>

- [x] **Fixed PLAN_MAX_TOKENS truncation (1000 → 4096)** — The #1 cause of task failures. Grok's reasoning model uses `<think>` blocks that consumed most of the 1000-token budget, leaving the JSON action truncated and unparseable. 27 out of ~80 API responses in one run hit exactly 1000 output tokens. Every "JSON retry failed" in the logs was caused by this. Raising to 4096 gives ample room for reasoning + JSON output.
- [x] **Added write_source/edit_file to path-based loop detection** — These action types fell through to bare `action_type` keys (no path), so writing to different files looked like repeating. Now keyed by path like `create_file` and `append_file`. Extended write-then-read exemption to cover all four write actions.
- [x] **Added SSL cert diagnostic logging** — certifi may not be loading correctly (arxiv.org still failing despite session 41 fix). Added logging at module load to show whether certifi CA bundle or system default is in use. Will diagnose on next run.
- [x] **Confirmed progress_callback fix already in place** — The image gen `progress_cb` signature mismatch (1 arg vs 3) was from an older code version; current code at line 249 correctly passes `(step_num, count, status_text)`.
- [x] **Added total-writes-per-path rewrite-loop detection** — After live test confirmed max_tokens fix eliminated JSON truncation (zero "JSON retry failed"), the new #1 failure was Grok rewriting the same output file 5-10+ times without calling `done`. The consecutive detector missed this because the model breaks chains with reads/searches in between. New tracker counts total writes per path across the whole task: nudge at 3, strong warning at 5, force-abort at 7. Complements the existing consecutive detector.

**Files modified:** `src/core/plan_executor.py`

</details>

<details>
<summary>Session 42 (Cowork) — Opportunity Scanner: make Archi actually useful</summary>

- [x] **Created Opportunity Scanner** (`src/core/opportunity_scanner.py`, ~350 lines) — Replaced brainstorm prompt in `suggest_work()` with structured scanners that read real data. Four scanners: `scan_projects()` reads vision/overview files and compares to existing files to find build gaps; `scan_errors()` reads 3 days of error logs grouped by module; `scan_capabilities()` checks which tools (ask_user, run_python, write_source) have never been used; `scan_user_context()` reads recent conversations and vector memory. Combiner deduplicates by word overlap >0.5, ranks by value/effort, caps to top 7. Returns typed `Opportunity` dataclass objects (build/ask/fix/connect/improve).
- [x] **Populated project_context.json** — Was empty `{}`, meaning Archi had zero awareness of actual projects. Populated with Health Optimization and Archi Self-Improvement project data including real file paths, descriptions, focus areas, and autonomous tasks.
- [x] **Auto-populate project context** — Added `auto_populate()` to `src/utils/project_context.py`. Scans `workspace/projects/` subdirectories, reads vision files, builds structured context, merges with existing (preserving user customizations). Called from `dream_cycle._load_project_context()` when context is empty.
- [x] **Type-aware goal decomposition** — `goal_manager.py` now detects opportunity type via `infer_opportunity_type()` and injects type-specific hints: build→"First task MUST create a working .py script", ask→"First task MUST use ask_user", fix→"read source and error logs first", connect→"read existing code first".
- [x] **Builder prompts in PlanExecutor** — Added "FUNCTIONAL OUTPUT PRIORITY" block to MINDSET section: test code after writing, use ask_user for data Jesse already has, write_source + run_python is the power combo, working 30-line script beats 200-line report.
- [x] **Scanner integration in idea_generator.py** — `suggest_work()` now calls `scan_all()` first, converts Opportunities to backward-compatible idea dicts, falls back to `_brainstorm_fallback()` (preserved old brainstorm prompt) if scanner returns nothing. All existing filtering (dedup, relevance, purpose-driven, memory dedup) still applies downstream.
- [x] **Dream cycle auto-populate** — `_load_project_context()` checks if loaded context has `active_projects`, calls `auto_populate()` if empty.

**Files created:** `src/core/opportunity_scanner.py`
**Files modified:** `src/core/idea_generator.py`, `src/utils/project_context.py`, `src/core/goal_manager.py`, `src/core/plan_executor.py`, `src/core/dream_cycle.py`, `data/project_context.json`

</details>

<details>
<summary>Session 41 (Cowork) — SDXL batch efficiency, SSL cert fix, timing tuning</summary>

- [x] **SDXL batch pipeline reuse** — Pipeline was being loaded and unloaded for every single image in a batch (~4s overhead each). Added `keep_loaded` parameter to `generate()`, pipeline reuse via `_loaded_model` tracking, and explicit `unload()` method. Router now persists `_image_gen` instance across batch calls. Dispatcher wraps batch loop in try/finally with `finish_image_batch()` cleanup. **Verified in logs: 10-image batch at 08:16 shows "Reusing loaded pipeline" 9 times, saving ~36s.**
- [x] **SSL certificate fix for web research** — `urllib.request.urlopen` on Windows doesn't use certifi's CA bundle, causing `CERTIFICATE_VERIFY_FAILED` on arxiv.org and other sites. Added `ssl.create_default_context(cafile=certifi.where())` and passed to all `urlopen` calls. **Verified in logs: arxiv.org and lilianweng.github.io fetch successfully after fix. No more SSL errors.**
- [x] **Reduced idle/cooldown timers** — `idle_threshold` from 300s → 60s (Archi starts working after 1 minute of idle instead of 5). `check_interval` from 30s → 15s. `SUGGEST_COOLDOWN_SECS` from 3600 → 600 (10 min between suggestion prompts instead of 1 hour). **Verified in logs: dream cycle started ~3 min after last activity instead of waiting 5+.**
- [x] **Log review & new issue identification** — Identified loop detection still too aggressive for read_file (3 repeats triggers abort during legitimate verify-your-work patterns), memory persistence broken (0 entries on every startup), and Brave search 429 rate limiting.
- [x] **Shutdown hardening (ghost process fix)** — Archi sent a Discord message minutes after Ctrl+C because GoalWorkerPool.shutdown() called `executor.shutdown(wait=True)` which blocked forever while PlanExecutor finished its API calls. Four fixes: (1) Ctrl+C signal handler now prints to console and triggers `signal_task_cancellation("shutdown")` so PlanExecutor bails at next step. (2) GoalWorkerPool.shutdown() uses real timeout with console progress, calls `shutdown(wait=False)` + per-future deadlines. (3) archi_service.stop() explicitly closes Discord bot via `asyncio.run_coroutine_threadsafe(_bot_client.close(), _bot_loop)`. (4) stop.py rewritten as nuclear kill — `proc.kill()` / `taskkill /F /T`, triple detection (cmdline identifiers + cwd + project path), double-tap survivors.

- [x] **Memory storage fix** — Vector store was always 0 entries because the only write path (`store_long_term`) was gated behind `_learning_success`, and every task had failed. Now stores BOTH successes (type: `research_result`) and failures (type: `task_failure`) so Archi builds memory regardless. Failure memories include error info, last actions, and summary to avoid repeating mistakes.
- [x] **Loop detection rewrite — consecutive counting + escalating warnings** — Two complementary fixes: (1) Switched from total-occurrence counting to CONSECUTIVE identical action key counting. Old code counted all `read_file:path` across the entire task (3 total = killed), which falsely killed legitimate patterns like read→append→read→fetch→read. New code only counts consecutive identical keys, so any different action in between resets the counter. (2) Added escalating warnings instead of silent kill: repeat 2 → nudge, repeat 3 → strong warning, repeat 4 → force abort. Also added write-then-read exemption (`read_file:X` after `create_file:X`/`append_file:X` tagged as `read_file_verify:X`). Verified against all 4 log kills: kills #1/#2 (genuine loops) still caught, kill #3 (false positive) no longer triggers, kill #4 (borderline) gets warnings first.

**Files modified:** `src/tools/image_gen.py`, `src/models/router.py`, `src/interfaces/action_dispatcher.py`, `src/core/plan_executor.py`, `src/tools/web_search_tool.py`, `src/utils/config.py`, `src/core/idea_generator.py`, `src/core/agent_loop.py`, `src/core/goal_worker_pool.py`, `src/service/archi_service.py`, `scripts/stop.py`, `src/core/autonomous_executor.py`

</details>

<details>
<summary>Session 40 (Cowork) — SDXL diagnostics, duplicate MemoryManager, image gen fast-path & multi-image</summary>

- [x] **Diagnosed real SDXL failure** — The `except ImportError` in `_load_pipeline()` was catching the actual error but logging a misleading "diffusers not installed" message. Changed to `except ImportError as e` with the real error in the log. Added `check_dependencies()` method that tests each package individually (torch, diffusers, transformers, accelerate, safetensors, StableDiffusionXLPipeline) — runs automatically on pipeline failure and logs per-package status.
- [x] **Improved CUDA detection** — `_detect_device()` now distinguishes between CPU-only torch builds (no `torch.version.cuda`) and CUDA builds with runtime issues. Logs the GPU name on success. Tells you exactly how to reinstall with CUDA when it's a CPU-only build.
- [x] **Fixed duplicate MemoryManager** — `agent_loop.py` was creating its own `MemoryManager()` independently of the dream_cycle's background-initialized one. Two separate VectorStore instances, two embedding model loads. Fix: added `memory` parameter to `run_agent_loop()`, `archi_service.py` now waits for the dream_cycle's memory init thread and passes the shared instance.
- [x] **Image generation fast-path** — Added zero-cost fast-path in `intent_classifier.py` for obvious image requests ("generate an image of...", "draw me...", "paint..."). Skips the LLM call entirely. Also handles multi-image: regex pattern catches "generate 3 images of X" and extracts both prompt and count.
- [x] **Multi-image support** — `_handle_generate_image()` in `action_dispatcher.py` now accepts a `count` parameter (max 10), generates sequentially, stops on first failure, reports all paths. Updated the intent instruction to show the count parameter and explicitly tell Grok to ALWAYS use the action (never describe generating without calling it).
- [x] **Image hallucination detection** — Added image-related claim phrases to `_is_chat_claiming_action_done()`: "i've generated", "images are saved", "saved to workspace/images", "here are your pictures", etc. Catches Grok hallucinating image generation instead of calling the tool.

**Files modified:** `src/tools/image_gen.py`, `src/core/agent_loop.py`, `src/service/archi_service.py`, `src/interfaces/intent_classifier.py`, `src/interfaces/action_dispatcher.py`

</details>

<details>
<summary>Session 39 (Cowork) — Loop detection fix, double router init, primp warning, startup speed</summary>

- [x] **Fixed create_file loop detection false positives** — `create_file` and `append_file` action keys now include the file path (previously they were just the bare action name, so creating 3 different files in a row triggered the loop detector). Also raised the repeat threshold from 3 to 5 for file-write actions, since overwrite-to-refine (draft → read back → rewrite better) is legitimate behavior.
- [x] **Fixed double ModelRouter initialization** — `archi_service.py` called `enable_autonomous_mode()` before `set_router()`, causing the dream cycle to lazy-initialize a duplicate router. Swapped the call order so the shared router is available when `enable_autonomous_mode` calls `_get_router()`.
- [x] **Noted primp/ddgs version mismatch** — The `chrome_120` impersonation warning comes from a version mismatch between `ddgs` and `primp`. Added upgrade instructions to `requirements.txt`. Fix: `pip install --upgrade ddgs primp`.
- [x] **Fixed slow startup (36s gap from sentence-transformers import)** — Moved `lancedb` and `sentence_transformers` imports from module-level to inside `VectorStore.__init__()` so the delay appears in logs instead of being invisible. More importantly, moved the entire `MemoryManager()` initialization to a background thread in `DreamCycle.__init__()`. Startup no longer blocks on torch/ML imports — memory comes online asynchronously and the `GoalWorkerPool` reference is updated when ready.

**Files modified:** `src/core/plan_executor.py`, `src/service/archi_service.py`, `src/memory/vector_store.py`, `src/core/dream_cycle.py`, `requirements.txt`

</details>

<details>
<summary>Session 38 (Cowork) — Identity config split + reset.py hardening</summary>

- [x] **Split identity config into static + dynamic** — `config/archi_identity.yaml` now holds only static config (name, role, timezone, working hours). Dynamic project data moved to `data/project_context.json`, which Archi can update at runtime. Created `src/utils/project_context.py` as centralized load/save/scan module with fallback to legacy identity yaml.
- [x] **File scanning in idea generator** — `suggest_work()` now calls `scan_project_files()` to inject actual file listings into Grok's brainstorming prompt. Prevents hallucination of nonexistent files like `gaps.md`.
- [x] **Rewrote autonomous_tasks** — Changed from hallucination-prone phrases ("Identify gaps in current protocol") to grounded ones ("Review existing files in the project and suggest improvements").
- [x] **Removed dead identity config keys** — `requires_approval`, `absolute_rules`, `communication`, `constraints.prefer_local`, `full_name`, `prime_directive` path — none were read by code. Removed ~90 lines of decorative config.
- [x] **Fixed reset.py: initiative_state.json** — Added to cleanup list (was missing since session 36 added initiative tracking).
- [x] **Fixed reset.py: monthly cost preservation** — Monthly cost totals now survive resets so budget enforcement isn't bypassed by resetting mid-month.
- [x] **Interactive project context reset** — `reset.py` now asks "Also clear project context? [y/N]" separately. Default preserves it. New `--clear-context` flag for automation.
- [x] **Updated all consumers** — `idea_generator.py`, `dream_cycle.py`, `autonomous_executor.py`, `message_handler.py` all switched from reading `archi_identity.yaml` to `project_context.load()`.

**Files created:** `src/utils/project_context.py`, `data/project_context.json`
**Files modified:** `config/archi_identity.yaml`, `src/core/idea_generator.py`, `src/core/dream_cycle.py`, `src/core/autonomous_executor.py`, `src/interfaces/message_handler.py`, `scripts/reset.py`

</details>

<details>
<summary>Session 37 (Cowork) — Reliability & quality-of-life fixes from first real-world run</summary>

- [x] **Fixed `project_root` ImportError** — `initiative_tracker.py` and `time_awareness.py` imported `project_root` from `src.utils.paths` but it didn't exist. Added `project_root = base_path_as_path` alias in `paths.py`. Was crashing every dream cycle.
- [x] **Fixed verbose Discord notifications** — Added `_humanize_task()` helper to `reporting.py` that parses raw PlanExecutor commands into readable summaries (e.g., `web_search('NMN efficacy')` → `Researched NMN efficacy`). Applied to morning reports, hourly summaries, and goal completions. Report size dropped from ~5,500 to ~720 chars.
- [x] **Fixed false task success marking** — PlanExecutor now sets `_force_aborted = True` when JSON retry fails, so the existing success-determination logic correctly marks the task as failed instead of silently succeeding.
- [x] **Fixed orchestrator ignoring task success status** — `task_orchestrator.py` now checks `result.get("executed", False)` to distinguish real success from force-aborted tasks. Softened wave failure logic to only stop if ALL tasks in a wave fail.
- [x] **Goal completion notifications surface actual findings** — `send_user_goal_completion()` now extracts PlanExecutor's `Done:` summary and includes the actual answer (e.g., "Embrace Pet Insurance recommended for Rat Terrier") in the notification instead of just listing filenames.
- [x] **Search rate limiting** — Added thread-safe 1.5s minimum interval between web searches across all threads in `web_search_tool.py` to prevent 429s/403s from parallel tasks.
- [x] **PlanExecutor efficiency rules** — Updated system prompt with "EFFICIENCY RULES" section: 2-4 searches max before writing, synthesize into one `create_file`, move on from failed fetches. Changed `append_file` description to discourage incremental appending.
- [x] **Discord reply context extraction** — `discord_bot.py` now extracts `message.reference` content when users reply to a specific notification, prepending `[Replying to Archi's message: "..."]` so the model knows which topic the user is responding to.
- [x] **Keyword-inferred reply topic** — When users type without using Discord's reply feature, `_infer_reply_topic()` does keyword overlap matching against recent back-to-back notifications. Only triggers when one notification clearly matches better (≥2 keywords, ≥2 advantage over runner-up).
- [x] **Suggest cooldown reset on initiative failure** — When a self-initiated goal fails, `_maybe_clear_suggest_cooldown()` in `goal_worker_pool.py` resets the dream cycle's `_last_suggest_time` so Archi can try something else instead of sitting idle for an hour.
- [x] **Idle-state visibility logging** — Changed the dream cycle's cooldown sleep log from DEBUG to INFO level, now shows "Idle — no work, suggest cooldown has Xmin left. Sleeping Ys."

**Files modified:** `src/utils/paths.py`, `src/core/reporting.py`, `src/core/plan_executor.py`, `src/core/task_orchestrator.py`, `src/tools/web_search_tool.py`, `src/interfaces/discord_bot.py`, `src/core/goal_worker_pool.py`, `src/core/dream_cycle.py`

</details>

<details>
<summary>Session 36 (Cowork) — Companion personality, ask-user, proactive initiative</summary>

- [x] **Companion personality** — Replaced "Professional digital symbiont" with "Helpful AI teammate and companion" across identity config, prime directive, and system prompt. Warmer notification messages. Removed "Companion personality" from open items.
- [x] **Time awareness utility** (`src/utils/time_awareness.py`) — Reads timezone and working_hours from `archi_identity.yaml`. Functions: `is_quiet_hours()`, `is_user_awake()`, `time_until_awake()`, `get_user_hour()`. Foundation for ask-user and initiative.
- [x] **Ask-user tool** — New `ask_user()` function in `discord_bot.py` following the existing `request_source_approval()` blocking pattern. Sends a Discord DM, blocks the worker thread until Jesse replies or timeout (5 min). Time-aware: returns None during quiet hours. New `_do_ask_user()` tool in PlanExecutor so tasks can call `{"action": "ask_user", "question": "..."}` mid-execution.
- [x] **Proactive initiative** — New `InitiativeTracker` (`src/core/initiative_tracker.py`) with $0.50/day budget (configurable in `rules.yaml`). Dream cycle now tries self-initiating work before asking the user. Picks top suggestion from idea generator, creates goal at priority 4 (below user work), notifies Jesse what it's working on and why. Capped at 2 initiatives/day, respects quiet hours.
- [x] **Builder mindset nudges** — Strengthened code-writing emphasis in PlanExecutor MINDSET block ("CODE IS YOUR SUPERPOWER"), goal decomposition prompt ("BUILD THINGS, DON'T DESCRIBE THEM"), and prime directive ("Builder Mindset" principle).

</details>

<details>
<summary>Session 35 (Cowork) — Layered orchestration / parallel task execution</summary>

- [x] **Created TaskOrchestrator** (`src/core/task_orchestrator.py`) — Wave-based parallel task execution within a single goal. Identifies independent tasks (no mutual dependencies) and fans them out to `ThreadPoolExecutor` threads. Tasks are grouped into "waves": Wave 1 runs all tasks with no unmet deps in parallel, Wave 2 runs tasks whose deps were all in Wave 1, etc. Same API cost, faster wall-clock time. Configurable via `rules.yaml` (`task_orchestrator.max_parallel_tasks_per_goal`, default 2, hard cap 4).
- [x] **Parallelism-aware decomposition** — Updated the goal decomposition prompt in `goal_manager.py` to teach the LLM about parallelism: prefer empty dependency arrays for independent tasks, use dependencies only when one task truly needs another's output. Added good/bad examples.
- [x] **Added `Goal.get_execution_waves()`** — Helper method on `Goal` class that returns the wave structure as a list of lists for logging/debugging. Uses the same dependency logic as `get_ready_tasks()`.
- [x] **Integrated orchestrator into GoalWorkerPool** — Replaced the sequential main task loop in `_execute_goal()` with a single `TaskOrchestrator.execute_goal_tasks()` call. Kept crash-recovery resume loop and goal completion notifications.
- [x] **Orchestrator config** — Added `task_orchestrator` section to `rules.yaml` with `enabled: true` and `max_parallel_tasks_per_goal: 2`.

</details>

<details>
<summary>Session 34 (Cowork) — Concurrent worker pool architecture</summary>

- [x] **Thread safety for GoalManager** — Added `threading.RLock()` protecting all public methods (`create_goal`, `decompose_goal`, `add_follow_up_tasks`, `get_next_task`, `start_task`, `complete_task`, `fail_task`, `get_status`, `save_state`). Lock released during `model.generate()` calls in `decompose_goal` to avoid blocking. Added `get_next_task_for_goal(goal_id)` so workers only pick tasks from their own goal.
- [x] **Thread safety for ModelRouter** — Added `threading.Lock()` protecting `_stats` dict updates in `_use_api()`, `chat_with_image()`, and `get_stats()`.
- [x] **Thread safety for LearningSystem** — Added `threading.Lock()` protecting all mutable state (experiences, patterns, metrics, action_stats). Lock released during `model.generate()` calls in `extract_patterns()` and `get_improvement_suggestions()`.
- [x] **Created GoalWorkerPool** (`src/core/goal_worker_pool.py`) — `ThreadPoolExecutor`-backed pool (default 2 workers, configurable, hard cap 4). Each worker independently decomposes and executes one goal. Per-goal budget cap ($1.00 default). Tracks `GoalWorkerState` per worker for monitoring. Discord notifications on completion/failure. Graceful shutdown with timeout.
- [x] **Refactored DreamCycle to dispatcher** — `_run_dream_cycle()` now submits goals to the worker pool instead of calling `process_task_queue()`. Falls back to old sequential executor if pool unavailable. Pool created in `enable_autonomous_mode()` or lazily via `set_router()`.
- [x] **Direct pool submission** — `kick(goal_id)` now submits directly to the worker pool for zero-latency start. Updated all 4 call sites: `action_dispatcher.py`, `discord_bot.py` (suggestion picks), `message_handler.py` (deferred requests + auto-escalation).
- [x] **Worker pool config** — Added `worker_pool` section to `config/rules.yaml` with `max_workers: 2` and `per_goal_budget: 1.00`.
- [x] **Removed architecture review from open items** — Completed by this session's concurrent architecture overhaul.

</details>

<details>
<summary>Session 33 (Cowork) — Unthrottle & make Archi do real work</summary>

- [x] **Rewrote task decomposition prompt** — Replaced "Focus on research + file creation" with explicit instructions to produce real deliverables, not gap reports or summaries of what needs to be done. Added good/bad task examples. In `goal_manager.py`.
- [x] **Raised dream cycle time cap to 120 min** — Old 10-minute cap was from local model era and forced goals to be spread across multiple cycles with idle gaps. Budget cap ($0.50/cycle) is the real safety net. In `autonomous_executor.py`.
- [x] **Eliminated idle dream cycle churn** — Added `_should_run_cycle()` check in `dream_cycle.py`. When no goals exist and suggest cooldown is active, the monitor loop now sleeps until the cooldown expires instead of spinning up empty dream cycles every 5 minutes. Chunked sleep (5s intervals) ensures `kick()` and `stop` are still responsive.
- [x] **Immediate goal execution** — Added `kick()` method to `DreamCycle` that back-dates `last_activity` so work starts within seconds of goal creation instead of waiting 5 minutes. Called from `action_dispatcher.py`, `discord_bot.py` (suggestion picks), and `message_handler.py` (deferred + auto-escalation). Updated user-facing messages: "Starting on it now" instead of "I'll get to it during my next dream cycle."
- [x] **Discord file attachments** — `send_notification()` now accepts optional `file_path` to attach files via `discord.File`. New `send_file` action + handler in `action_dispatcher.py`. Added to intent classifier so "send me the file" routes correctly.
- [x] **Audit loops & heartbeat (partial)** — Diagnosed the idle churn problem (dozens of 0.0s dream cycles doing nothing). Fixed via `_should_run_cycle()`. Heartbeat itself is still useful for adaptive sleep tiers.
- [x] **Fixed intent classifier routing** — `multi_step` (12-step chat) was capturing project-scale work that should go to `create_goal` (50-step dream cycle). Rewrote action descriptions: `create_goal` is now the default for any non-trivial project work; `multi_step` is explicitly scoped to quick tasks the user is waiting for. In `intent_classifier.py`.
- [x] **Added "build, don't report" mindset to PlanExecutor** — Inserted a MINDSET block in the system prompt that tells Archi to produce real deliverables (working code, complete protocols, functional systems) instead of summaries and gap analyses. Changed workspace file descriptions from "reports, research output" to "project deliverables, code, content." In `plan_executor.py`.

</details>

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
