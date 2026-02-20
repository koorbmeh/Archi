# Archi Architecture Map

Reference document for understanding and modifying Archi's codebase.
Generated 2026-02-14, updated 2026-02-20 (session 58) by Jesse + Claude (Cowork).
For the original evolution design spec, see `claude/archive/ARCHITECTURE_PROPOSAL.md`.

---

## System Overview

Archi is an autonomous AI agent running on Windows, communicating via Discord. **API-only architecture:** Grok 4.1 Fast (Reasoning) via xAI direct is the default model for all reasoning, Claude Haiku 4.5 for computer use tasks, and local SDXL for image generation. All local LLM infrastructure was removed in session 24 — there is no local reasoning or vision model. Web chat, CLI, and dashboard interfaces have been removed — Discord is the sole interface. It operates in two modes: **chat mode** (single-shot responses to user messages) and **dream mode** (autonomous multi-step background work when idle 1+ min).

## Directory Layout

```
Archi/
├── config/
│   ├── archi_identity.yaml    # Static identity (name, role, timezone, working hours)
│   ├── heartbeat.yaml         # Sleep timing + dream cycle config (idle_threshold, check_interval)
│   ├── prime_directive.txt    # Core operational guidelines
│   └── rules.yaml             # Safety: budgets, protected files, blocked commands, risk levels
├── src/
│   ├── core/
│   │   ├── agent_loop.py      # Main tick loop (heartbeat, throttle, goal discovery)
│   │   ├── dream_cycle.py     # Dream cycle orchestrator (delegates to modules below)
│   │   ├── autonomous_executor.py  # Task execution loop + follow-up task extraction (within-goal)
│   │   ├── idea_generator.py  # Work suggestion (suggest_work), goal hygiene, scanner integration
│   │   ├── opportunity_scanner.py  # Structured work discovery: project gaps, errors, capabilities, user context
│   │   ├── reporting.py       # Morning report + hourly summary notifications
│   │   ├── notification_formatter.py  # Model-based conversational message generation (all notification types)
│   │   ├── discovery.py       # Phase 5: Project context scanning before goal decomposition
│   │   ├── goal_manager.py    # Goal/task CRUD, Architect decomposition with specs, state persistence
│   │   ├── plan_executor.py   # Multi-step task execution (research→file→verify→done) + context compression, error recovery
│   │   ├── output_schemas.py  # Structured output contracts: schema validation for PlanExecutor actions
│   │   ├── qa_evaluator.py    # Post-task + post-goal quality gate: deterministic checks + model semantic eval
│   │   ├── integrator.py      # Phase 6: Post-completion cross-task synthesis, glue detection, summary generation
│   │   ├── critic.py          # Adversarial per-goal evaluation + User Model preferences (Phase 6)
│   │   ├── heartbeat.py       # Adaptive sleep: 2-tier (command 10s / idle 60s) + night mode
│   │   ├── safety_controller.py  # Action authorization by risk level
│   │   ├── learning_system.py # Experience recording, pattern extraction, insights
│   │   ├── conversational_router.py  # Phase 4: Single model call per message (intent + easy answer)
│   │   ├── user_model.py            # Phase 4: Structured JSON store (preferences, corrections, patterns, style)
│   │   ├── user_preferences.py   # Preference extraction from conversations (legacy, pre-Phase 4)
│   │   ├── interesting_findings.py  # Queue notable research for user delivery
│   │   ├── file_tracker.py    # Workspace file tracking (goal→file mapping)
│   │   ├── logger.py          # Logging configuration
│   │   └── resilience.py      # Circuit breakers and retry logic
│   ├── interfaces/
│   │   ├── message_handler.py  # v2 entry point: pre-process → classify → dispatch → respond
│   │   ├── intent_classifier.py # 3 fast-paths (datetime/commands/greeting) + model intent
│   │   ├── action_dispatcher.py # Handler registry: 10 action handlers
│   │   ├── response_builder.py  # Trace logging, conversation logging, response assembly
│   │   ├── discord_bot.py       # Discord DM interface, notification sending, dream cycle commands
│   │   ├── chat_history.py      # Multi-turn conversation history management
│   │   └── voice_interface.py   # Text-to-speech via Piper
│   ├── models/
│   │   ├── router.py          # Multi-provider routing, model switching via Discord
│   │   ├── fallback.py        # Phase 8: Provider fallback chain with per-provider circuit breakers
│   │   ├── openrouter_client.py  # Universal LLM client (any OpenAI-compatible provider)
│   │   ├── providers.py       # Provider registry, model aliases, pricing
│   │   └── cache.py           # Query cache (dedup identical prompts)
│   ├── tools/
│   │   ├── tool_registry.py   # MCP-aware tool dispatch: execute(action_name, params) → result
│   │   ├── mcp_client.py      # MCP client: connects to servers via stdio, lifecycle mgmt
│   │   ├── local_mcp_server.py # Wraps built-in tools as local MCP server (FastMCP)
│   │   ├── image_gen.py       # SDXL local image generation (direct-only, never MCP)
│   │   ├── desktop_control.py # pyautogui: click, type, screenshot
│   │   ├── browser_control.py # Playwright: navigate, click, fill
│   │   ├── computer_use.py    # Vision-guided orchestrator
│   │   ├── web_search_tool.py # DuckDuckGo web search
│   │   └── ui_memory.py       # UI element position cache for desktop automation
│   ├── memory/
│   │   ├── memory_manager.py  # 3-tier: short-term (deque), working (SQLite), long-term (LanceDB vectors)
│   │   └── vector_store.py    # LanceDB vector storage backend
│   ├── monitoring/
│   │   ├── system_monitor.py  # CPU, memory, disk, temperature
│   │   ├── cost_tracker.py    # Budget enforcement (daily $5, monthly $100)
│   │   ├── health_check.py    # Component health (models, cache, storage)
│   │   └── performance_monitor.py  # Response times, throughput stats
│   ├── utils/
│   │   ├── paths.py           # base_path resolution + project_root alias
│   │   ├── config.py          # rules.yaml + heartbeat.yaml loading (get_dream_cycle_config, etc.)
│   │   ├── git_safety.py      # Git checkpoint/rollback for source modifications
│   │   ├── text_cleaning.py   # Shared: strip_thinking, sanitize_identity, extract_json
│   │   ├── parsing.py         # JSON extraction helpers
│   │   └── project_context.py # Dynamic project context: load/save/scan (data/project_context.json)
│   ├── maintenance/
│   │   └── timestamps.py      # Timestamp utilities
│   └── service/
│       └── archi_service.py   # Production service wrapper
├── data/
│   ├── goals_state.json       # All goals and tasks (persistent)
│   ├── dream_log.jsonl        # Dream cycle summaries (append-only)
│   ├── synthesis_log.jsonl    # Cross-goal synthesis insights (append-only)
│   ├── overnight_results.json # Task results for morning report (cleared daily)
│   ├── idea_backlog.json      # Brainstormed ideas queue
│   ├── project_context.json   # Dynamic: active projects, interests, focus areas (auto-populated from workspace/projects/)
│   ├── user_preferences.json  # Learned user preferences
│   ├── cost_usage.json        # API cost tracking (per-model, daily, monthly)
│   ├── file_manifest.json     # Workspace file tracking (goal→file mapping)
│   ├── experiences.json       # Learning system experience log
│   ├── memory.db              # SQLite working memory
│   ├── metrics.db             # System health metrics (CPU, memory, disk)
│   ├── ui_memory.db           # UI element positions for desktop automation
│   ├── vectors/               # LanceDB vector embeddings (long-term memory)
│   └── plan_state/            # PlanExecutor crash-recovery state per task
├── workspace/                  # User-facing output (reports, projects, images)
├── logs/
│   ├── conversations.jsonl    # Every user↔Archi exchange
│   ├── chat_trace.log         # Detailed chat flow debugging
│   └── actions/               # Daily action logs (YYYY-MM-DD.jsonl)
├── scripts/
│   ├── install.py, start.py, fix.py, stop.py, reset.py
│   ├── startup_archi.bat      # Windows auto-start wrapper
│   └── _common.py             # Shared script utilities
├── claude/                     # Claude session docs (this directory)
└── tests/
    ├── unit/                   # Unit tests (classifiers, history, cache, etc.)
    └── integration/            # Full system, gate tests, and test harness
```

---

## Execution Flows

### Flow 1: Chat Mode (Discord Message) — Phase 4 Router Pipeline

```
User message → discord_bot.on_message()
  │
  ├─ Discord-level fast-paths (no model call):
  │   ├─ "approve <path>" → deferred approval
  │   ├─ "switch to X" / "use X" → model switching
  │   ├─ "what model" → status check
  │   ├─ "use X for images" → image model switch
  │   ├─ "set dream cycle to N" → interval change
  │   └─ "try again" / "retry" → re-process last message
  │
  ├─ Build ContextState (pending suggestions, approval, question)
  │
  ├─ conversational_router.route() — SINGLE MODEL CALL:
  │   ├─ Local fast-paths ($0.00, no model call):
  │   │   ├─ /commands → direct handlers
  │   │   ├─ datetime → system clock
  │   │   ├─ screenshot → take screenshot
  │   │   ├─ image generation → extract prompt + count
  │   │   └─ deferred request → create goal
  │   └─ Router model call → JSON {intent, tier, answer, complexity}:
  │       ├─ Classifies intent (new_request, suggestion_pick, approval, cancel, etc.)
  │       ├─ Determines tier: easy (answer included) or complex (needs goal/PlanExecutor)
  │       └─ Extracts user_signals → UserModel (side effect, no extra call)
  │
  ├─ Dispatch based on RouterResult.intent:
  │   ├─ cancel → signal_task_cancellation
  │   ├─ suggestion_pick → create goal from chosen suggestion
  │   ├─ approval → resolve pending approval (threading.Event)
  │   ├─ question_reply → resolve pending question (threading.Event)
  │   ├─ easy tier + answer → send directly (no message_handler call)
  │   └─ complex tier → process_with_archi(router_result=rr):
  │       └─ message_handler.process_message(router_result=rr)
  │           ├─ _map_router_result() → IntentResult (no classify() call)
  │           ├─ Routing: goal/multi_step/coding → PlanExecutor
  │           └─ Post-process: response_builder, logging
  │
  └─ Post: send Discord reply, persist chat history
```

**Phase 4 modules (session 51):**
- `conversational_router.py` (~500 lines) — Single model call routing + input accumulation
- `user_model.py` (~200 lines) — Structured preference/correction/pattern store
- `message_handler.py` (~380 lines) — Entry point, accepts optional RouterResult
- `intent_classifier.py` (~670 lines) — Legacy fallback for internal callers
- `action_dispatcher.py` (~480 lines) — Handler registry (10 handlers: chat, search, create_file, list_files, read_file, create_goal, generate_image [supports count], click, browser_navigate, fetch_webpage). Includes hallucination detector for chat responses falsely claiming actions were performed.
- `response_builder.py` (~115 lines) — Trace, logging, response assembly, findings
- `text_cleaning.py` (~110 lines) — Shared: strip_thinking, sanitize_identity, extract_json

**Progress feedback:** PlanExecutor accepts `progress_callback(step_num, max_steps, message)`. Discord shows live-updating status during multi-step chat.

**Production testing:** `/test` (quick, 5 prompts) and `/test full` (all harness tests) via Discord. Routes through `intent_classifier._handle_slash_command()` → `message_handler._run_production_tests()` which dynamically imports `tests/integration/test_harness.py` definitions and validators. Also runnable directly: `python tests/integration/test_harness.py --quick`.

**Routing classifiers (in `intent_classifier.py`, tested in `tests/unit/test_routing_classifiers.py`):**
- `_is_greeting_or_social(msg)` — pure greetings/social detection
- `needs_multi_step(msg)` — research, multi-file, multi-part tasks
- `is_coding_request(msg)` — code modification/creation detection

**Location:** `src/interfaces/message_handler.py` (entry point), `src/interfaces/intent_classifier.py`, `src/interfaces/action_dispatcher.py`, `src/interfaces/response_builder.py`

### Flow 2: Dream Mode (Autonomous Background Work)

```
_monitor_loop() [background thread, checks every 30s]
  → is_idle() [5 min threshold]
    → _run_dream_cycle()
       │
       ├─ Phase 0: Morning report (6-9 AM, once/day)
       ├─ Phase 1: Has pending work?
       │   ├─ YES → process_task_queue()
       │   │   ├─ Execute manual queue tasks
       │   │   └─ _execute_autonomous_tasks()
       │   │       ├─ Resume crashed IN_PROGRESS tasks
       │   │       ├─ Decompose undecomposed goals (up to 5/cycle)
       │   │       └─ Loop: get_next_task() → execute_task() → complete/fail
       │   │           │   Caps: 10 min, $0.50/cycle, 50 tasks
       │   │           │
       │   │           └─ execute_task() → PlanExecutor.execute()
       │   │               ├─ Inject sibling task context (completed tasks in same goal)
       │   │               ├─ Build step prompt (task + goal + history + hints + step budget)
       │   │               ├─ router.generate() → JSON {action: ...}
       │   │               ├─ Execute action (web_search, create_file, etc.)
       │   │               ├─ Step budget awareness (warns at halfway, urgent at 3 remaining)
       │   │               ├─ Mechanical error recovery (transient→retry, mechanical→hint)
       │   │               ├─ Crash recovery (state saved after each step)
       │   │               ├─ Self-verification (read back files, rate quality)
       │   │               ├─ QA Evaluator (deterministic checks + model semantic eval)
       │   │               │   └─ On reject: retry once with QA feedback as hints
       │   │               └─ extract_follow_up_tasks() [0-2 tasks added to SAME goal]
       │   │
       │   └─ NO → _try_proactive_initiative() or _ask_user_for_work()
       │       ├─ suggest_work() — opportunity scanner (10 min cooldown)
       │       │   ├─ scan_projects() → build/improve/ask opportunities from real project files
       │       │   ├─ scan_errors() → fix opportunities from error logs
       │       │   ├─ scan_capabilities() → connect opportunities from unused tools
       │       │   ├─ scan_user_context() → build/ask opportunities from conversations
       │       │   └─ Fallback: _brainstorm_fallback() if scanner returns nothing
       │       ├─ Send numbered suggestions via Discord
       │       ├─ Return immediately (user picks later, or not)
       │       └─ Cooldown auto-resets if a self-initiated goal fails (session 37)
       │
       ├─ Phase 2: _review_history() [learning, only if ≥5 experiences]
       ├─ Phase 3: _run_synthesis() [every 10th cycle, informational only — no goal creation]
       ├─ Phase 4: _run_file_cleanup() [every 10th cycle, offset by 5]
       │
       ├─ Post-cycle: Accumulate hourly summary
       └─ Post-cycle: Log to dream_log.jsonl
```

---

## Quality Assurance (Sessions 49, 54)

**Files:** `src/core/qa_evaluator.py`, `src/core/integrator.py`, `src/core/critic.py`

Multi-layer quality system that replaced the old loop detection machinery:

**QA Evaluator — per-task** (runs in `execute_task()`):
- Layer 1 — Deterministic checks (free): files exist, Python files parse, not empty/truncated, done summary present.
- Layer 2 — Semantic evaluation (one model call): does the output actually accomplish the task? Is it substantive or just a summary?
- Verdicts: `accept` (pass), `reject` (retry with feedback), `fail` (unfixable).
- On rejection: task retried once with QA feedback injected as PlanExecutor hints.
- MAX_QA_RETRIES = 1 (configurable in module).

**Integrator** (per-goal, runs after orchestrator, before Critic — session 54):
- One model call per multi-task goal. Reads all task outputs and checks cross-task fit.
- Catches: mismatched imports, missing entry points, incompatible interfaces, missing glue.
- Produces human-readable summary of what was built and how to use it.
- Summary feeds into Notification Formatter so completion messages describe actual output.
- Skips single-task goals (no cross-task integration needed).

**QA Evaluator — goal-level** (runs after Integrator — session 54):
- Conformance check: do all task outputs together satisfy the original goal?
- Catches dangling references and missing pieces that per-task QA misses.
- One model call. Receives Integrator summary as additional context.

**Critic** (per-goal, adversarial — enhanced session 54 with User Model):
- Adversarial prompt after Integrator + Goal QA: "What's wrong? Would Jesse use this?"
- Phase 6: queries User Model for preferences, corrections, patterns. Can flag style/approach mismatches ("Jesse prefers X but this uses Y").
- Severity levels: `none`, `minor` (logged), `significant` (adds up to 2 remediation tasks).
- On significant: adds remediation tasks to the goal, re-runs orchestrator for fix-up pass.

**Post-completion pipeline in `_execute_goal()`:** Orchestrator → Integrator → Goal QA → Critic → Notify (with Integrator summary).

**Why this replaced loop detection:** The old system tracked consecutive identical actions with escalating warnings and force-abort. QA catches the actual problem (bad output) instead of the symptom (repetitive actions) and gives the model a chance to fix its work with specific feedback.

---

## Model Routing

**Files:** `src/models/router.py`, `src/models/fallback.py`, `src/models/providers.py`, `src/models/openrouter_client.py`

```
router.generate(prompt, force_api=False, messages=None, system_prompt=None, ...)
  │
  ├─ Cache check (single-turn only) → return at $0 if hit
  └─ Default path → active provider's API (OpenRouter + Grok 4.1 Fast by default)
```

**Multi-provider architecture (session 30):** Archi can route to multiple LLM providers. A provider is just config data (base_url, api_key_env, pricing) — no class hierarchies.

- `src/models/providers.py` — Provider registry (`PROVIDERS` dict), model aliases (`MODEL_ALIASES`), pricing (`MODEL_PRICING`), and helper functions (`resolve_alias()`, `get_pricing()`, etc.).
- `src/models/openrouter_client.py` — Universal LLM client. Despite the name (backward compat), works with any OpenAI-compatible provider. Accepts `provider` param in constructor.
- Adding a new provider = add one dict entry to `PROVIDERS` + API key to `.env`.

**Models available:**
- API (default): Grok 4.1 Fast Reasoning (`grok-4-1-fast-reasoning`) via xAI direct — all reasoning.
- API: Claude Haiku 4.5 — for computer use tasks (screenshot, click, browser)
- Direct providers: xAI, Anthropic, DeepSeek, OpenAI, Mistral — available when API key is set in `.env`.
- Local: SDXL (image generation via diffusers/torch) — runs independently, no LLM dependency.

**Runtime model switching (session 6, extended session 30):** Users can switch API models and providers via Discord:
- `"switch to grok"` — permanent switch via OpenRouter (default)
- `"switch to grok direct"` — permanent switch via xAI API directly
- `"use claude direct for this task"` — temporary, Anthropic direct, auto-reverts
- `"switch to xai/grok-2"` — provider/model path syntax
- `"switch to grok for 5 messages"` — temporary, reverts after 5 generate() calls
- `"switch to auto"` — restores OpenRouter with smart routing by complexity
- `"what model"` — shows current model, mode, and provider (if not OpenRouter)
- Implementation: `resolve_alias()` maps names to `(provider, model_id)`. `ModelRouter.switch_model()` creates a new client when the provider changes. Temp switches snapshot+restore provider alongside model state.

**Graceful degradation (Phase 8, session 56):** When the primary provider fails, `ProviderFallbackChain` in `src/models/fallback.py` cascades through backup providers. Default chain: xai → openrouter → deepseek → openai → anthropic → mistral (only providers with API keys in .env are active). Each provider has its own `CircuitBreaker` (from `resilience.py`): 3 consecutive failures → circuit OPEN, exponential recovery backoff (30s → 60s → 120s → 5min cap). On total outage (all circuits open), `_use_api()` checks the query cache as a last resort. Dream cycle skips when all providers are down. Discord "status" command shows provider health (🟢🔴🟡). Notifications sent when entering/exiting degraded mode.

**Computer use escalation:** For browser/desktop automation, Archi should escalate to Claude Haiku 4.5 (`claude-haiku`) which has purpose-built computer use support. Cost: ~$0.003-0.005 per screenshot. Use temporary switch: `"use claude-haiku for this task"`.

---

## Goal System

**File:** `src/core/goal_manager.py`

**Goal lifecycle:**
```
create_goal(description, user_intent, priority)
  → decompose_goal(goal_id, model) → 2-4 tasks as JSON
    → Type-aware hints (session 42): build→code first, ask→ask_user first, fix→diagnose, connect→read existing
    → tasks: PENDING → IN_PROGRESS → COMPLETED / FAILED
      → goal: is_complete() when all tasks done
```

**Goal sources (where new goals come from — session 31 rework, extended session 32):**
1. User via /goal command or chat intent (priority 5)
2. User picks a brainstormed suggestion via Discord (priority 5)
3. `extract_follow_up_tasks()` — adds tasks to the SAME goal (not new goals)
4. Model-inferred goal creation — intent classifier recognizes large requests and creates goals with a conversational response (session 32)
5. Auto-escalation — chat PlanExecutor that exhausts its step limit mid-research promotes the request to a background goal (session 32)

**Removed autonomous goal sources (session 31):**
- `_plan_future_work()` — removed (merged into suggest_work)
- `_brainstorm_ideas()` — replaced with `suggest_work()` (presents ideas, never auto-creates goals)
- `_extract_follow_up_goals()` — replaced with `extract_follow_up_tasks()` (within-goal only)
- `_run_synthesis()` goal creation — synthesis is now informational only

**Quality gates:**
- `is_goal_relevant()` — rejects goals not connected to active projects or user interests (from `data/project_context.json`). Applied in suggest_work filtering.
- `is_duplicate_goal()` — exact match, substring, word overlap Jaccard > 0.6. Checks BOTH active AND completed goals.
- `is_purpose_driven()` — requires deliverable verb + file path.
- Memory dedup: skip ideas with semantic distance < 0.5 to existing memories.
- Data verification rule in PlanExecutor prompt: must verify data files exist before analyzing; report "blocked" if missing.
- Hard cap: 25 active goals.
- Task orchestrator (event-driven DAG, Phase 5) checks `result.get("executed")` to distinguish real success from force-aborted tasks. Stops on 3 consecutive failures instead of wave-level checks.

**Deferred request handling (session 18):**
- `_is_deferred_request()` in `intent_classifier.py` — zero-cost fast-path detecting "when you have time", "remind me to", "later" + action verb patterns.
- `_handle_deferred_request()` in `message_handler.py` — creates goal with `user_intent="User deferred request via {source}"`.
- `get_next_task()` in `goal_manager.py` — user-requested goals (intent starts with "User ") sort before auto-generated goals.
- `send_user_goal_completion()` in `reporting.py` — rich Discord notification on user goal completion.
- `_get_user_goal_progress()` in `reporting.py` — "Your requests" section in morning/hourly reports.

**Long-term research memory (session 15, shared instance session 40):**
- `DreamCycle` creates a `MemoryManager` (LanceDB + sentence-transformers all-MiniLM-L6-v2) in a background thread. The same instance is shared with `agent_loop` (passed via `archi_service.py`) to avoid loading the embedding model twice.
- `execute_task()` stores a summary of every successful task in long-term vector memory.
- `execute_task()` queries memory before running and injects related prior research as PlanExecutor hints.
- `suggest_work()` queries memory for previously researched topics and injects into prompt. Also rejects ideas with semantic distance < 0.5 to existing memories.
- `extract_follow_up_tasks()` rejects follow-ups already covered in memory (distance < 0.5).

**Prune mechanisms:**
- `prune_duplicates()` — runs on startup only
- `_prune_stale_goals()` — removes undecomposed >48h or all-failed

---

## PlanExecutor (Multi-Step Engine)

**File:** `src/core/plan_executor.py`

**Step limit:** 50 (regular), 25 (coding), 12 (interactive chat)
**Max tokens per step:** 4096 (raised from 1000 in session 43 — reasoning models need headroom for `<think>` blocks before JSON)
**Actions:** web_search, fetch_webpage, create_file, append_file, read_file, list_files, write_source, edit_file, run_python, run_command, think, done

**Step budget awareness:** The prompt tells the model its current step count and remaining budget. At the halfway point, it's told to start transitioning from research to output. At 3 steps remaining, it's urgently told to produce output now.

**Loop detection:** Removed in session 49 — replaced by QA Evaluator which catches bad output with targeted feedback instead of blunt force-abort. Hard step cap of 50 retained as safety net. `_schema_retries_exhausted = True` set on JSON retry failures (renamed from `_force_aborted` in session 57).

**Efficiency rules (session 37):** The system prompt includes "EFFICIENCY RULES" limiting research to 2-4 searches before writing, discouraging repeated `append_file` calls, and telling the model to move on from failed fetches.

**Source code approval gate:**
- `write_source` and `edit_file` on paths matching `approval_required_paths` (default: `src/`) require user approval
- Dream mode: calls `discord_bot.request_source_approval()` → sends DM → blocks until yes/no/timeout (5 min)
- Chat mode: auto-approves (user explicitly requested the work via `approval_callback=lambda: True`)
- No approval channel available: denied by default (safe for offline/unconnected operation)
- Enforcement is at Python level, not prompt level — modification physically cannot proceed without approval

**Crash recovery:** State saved to `data/plan_state/<task_id>.json` after each step; max age 24h
**Context Compression (session 48):** After step 8, older steps are compressed to one-liners in the prompt (action + outcome only). Most recent 5 steps retain full fidelity. Prevents prompt bloat on long tasks.

**Structured Output Contracts (session 48):** Schema validation via `src/core/output_schemas.py`. Every model JSON response is validated against `ACTION_SCHEMAS` before dispatch. On schema violation, auto re-prompts with specific error message (max 2 retries).

**Mechanical Error Recovery (session 48):** `_classify_error()` classifies action failures: transient (retry with 2s backoff, no step burned), mechanical (targeted fix hint injected), permanent (fail immediately). Rule-based, ~60 lines.

**Reflection (session 48):** Self-check checklist in the "done" action prompt. Model must verify: task was completed, files exist, code tested, no gaps.

**File Security (session 48):** Path validation uses `os.path.realpath()` to resolve symlinks before boundary checks. `tool_registry.py` blocks system directories as defense-in-depth.

**MCP Tool Integration (session 55):** Tool execution is MCP-aware. `tool_registry.py` connects to configured MCP servers on `initialize_mcp()`, discovers their tools, and routes `execute()` calls through MCP for MCP-backed tools. Direct tools are the fallback. Image gen stays direct-only (privacy). Server config in `config/mcp_servers.yaml` — adding a new MCP server requires only a config entry, no code. `mcp_client.py` manages server lifecycle: start on first use, stop after idle timeout. Background event loop bridges sync callers (PlanExecutor) to async MCP SDK. `local_mcp_server.py` wraps built-in tools as a FastMCP server — the bridge for existing capabilities. PlanExecutor's `_execute_action()` falls back to tool registry for unknown actions, giving automatic support for any MCP-provided tool (e.g. GitHub operations).

**Verification:** After "done", reads back files, rates quality 1-10, passes if ≥6

**Approval listener:** `_check_pending_approval()` uses three-tier matching: (1) exact match, (2) first-word extraction (handles "No, I don't think..." → "no"), (3) phrase detection for short messages (<80 chars).

---

## Greeting Handler

**File:** `src/interfaces/message_handler.py` (system prompt) and `src/interfaces/intent_classifier.py` (classifiers)

**Flow:**
```
message arrives
  → _is_greeting_or_social(message)
    ├─ len > 200 → False (not social)
    ├─ Contains "create ", "write ", ".txt", etc. → False
    ├─ Starts with "hello", "hi ", "hey ", etc. → True
    ├─ Contains social phrases → True
    └─ Exact match to praise words → True
  → if True: _build_contextual_greeting() or praise response
  → SKIP all model calls, return immediately
```

**KEY ISSUE:** "Hey Archi. Make it a goal to read all files..." starts with "hey " → matches social_start → returns canned greeting, drops the actual instruction. The check at line 122-125 tries to exclude file creation intent but doesn't catch goal-setting or general commands.

---

## Notification System

**Formatter:** `src/core/notification_formatter.py` (session 50)
**Delivery:** `src/core/reporting.py` (`_notify()`) + `src/interfaces/discord_bot.py` (`send_notification()`)

All outbound notifications route through the Notification Formatter — a single model call per notification via Grok 4.1 Fast (~$0.0002/call) that produces natural, varied messages matching Archi's persona (warm, conversational, concise). Every notification type has a deterministic fallback string for when the model call fails.

**Notification types:** goal completion, morning report, hourly summary, work suggestions, idle prompt, finding notifications, initiative announcements, interrupted task recovery, decomposition failures.

**Cooldown:** 60 seconds between DMs (bypass for goal completions). Finding notifications: 30 min cooldown.

**Schedules:** Hourly summary every 3600s. Morning report 6-9 AM from overnight_results.json.

**Feedback loop (session 50):** Discord `on_raw_reaction_add` handler. Completion notifications are "tracked" by message ID. When Jesse reacts with 👍/👎/❤️/🎉/🔥/😕/😞, the reaction is recorded via `learning_system.record_feedback()`. Significant goals (3+ tasks or 10+ min) append "Anything you'd change?" to completion messages. Tracked messages pruned at 100 entries.

**Reply context (session 37):** When the user replies to a Discord message, `_extract_reply_context()` fetches the referenced message's content and prepends it as `[Replying to Archi's message: "..."]`. When the user types without replying, `_infer_reply_topic()` does keyword overlap matching against recent back-to-back notifications to disambiguate which topic the user is responding to. Both in `discord_bot.py`.

---

## Key Config Values

| Setting | Value | File |
|---------|-------|------|
| Daily budget | $5.00 | rules.yaml |
| Monthly budget | $100.00 | rules.yaml |
| Per-cycle budget | $0.50 | rules.yaml |
| Max active goals | 25 | idea_generator.py MAX_ACTIVE_GOALS |
| Max steps per task | 50 (25 coding, 12 chat) | plan_executor.py lines 61-63 |
| Dream idle threshold | 60s default (configurable via heartbeat.yaml + Discord) | heartbeat.yaml → config.py → dream_cycle.py |
| Dream check interval | 15s default (configurable via heartbeat.yaml) | heartbeat.yaml → config.py → dream_cycle.py |
| Dream max time | 120 min/cycle | autonomous_executor.py _MAX_DREAM_MINUTES |
| Loop detection | Removed (session 49) — replaced by QA Evaluator. Hard step cap of 50 retained. | plan_executor.py |
| Suggest cooldown | 600s (10 min between suggestion prompts) | idea_generator.py SUGGEST_COOLDOWN_SECS |
| Scanner cache TTL | 3600s (1 hour vision file cache) | opportunity_scanner.py _CACHE_TTL |
| Heartbeat command mode | 10s for 120s | heartbeat.yaml |
| Heartbeat monitoring | 60s | heartbeat.yaml |
| Heartbeat deep sleep | 600s (max 1800s) | heartbeat.yaml |
| Night mode sleep | 1800s (11PM-6AM) | heartbeat.yaml |
| History (mid-convo) | 8 exchanges × 500 chars | message_handler.py process_message() |
| History (default) | 6 exchanges × 500 chars | message_handler.py process_message() |
| History (cold start) | 4 exchanges × 300 chars | message_handler.py process_message() |
| Session mid-convo threshold | 300s (5 min) | message_handler.py process_message() |
| Session cold-start threshold | 1800s (30 min) | message_handler.py process_message() |
| File read cap | 5KB | action_dispatcher.py |
| Duplicate Jaccard | > 0.6 | idea_generator.py is_duplicate_goal() |
| Suggest work cooldown | 1 hour between prompts | idea_generator.py SUGGEST_COOLDOWN_SECS |
| Search rate limit | 1.5s between searches (all threads) | web_search_tool.py _MIN_SEARCH_INTERVAL |
| Stale goal age | 48h | idea_generator.py prune_stale_goals() |
| Crash recovery max age | 24h | plan_executor.py line 67 |

---

## Known Issues & Fix Locations

### 1. Greeting handler edge case with short remainders
- **File:** `intent_classifier.py` `_is_greeting_or_social()`
- **Mostly fixed:** The original documented case ("Hey Archi. Make it a goal to...") is now caught by `_ACTION_KEYWORDS` (which includes "goal", "make it", "can you", etc.) before the greeting starts are checked. The remaining risk is messages where the greeting remainder is under 16 chars and contains no action keyword — e.g., "Good morning, I had an idea" → remainder "i had an idea" (13 chars) → falsely treated as social. Low priority since most real instructions are either longer or contain a keyword.

---

## Entry Points

- **Start agent:** `python scripts/start.py` → 3 options: service (full), discord-only, watchdog. Startup runs a "2+2" connectivity test via `openrouter/free` ($0).
- **Discord bot:** `src/interfaces/discord_bot.py` → on_message → mark_activity() + process_with_archi. On startup (`on_ready`), checks for crash-recovered tasks via `PlanExecutor.get_interrupted_tasks()` and notifies user.
- **Discord dream cycle commands:** `_parse_dream_cycle_interval()` handles "set/change/adjust dream cycle/delay/timeout to N minutes" with polite prefix stripping ("can you", "please", etc.) and compound phrases ("dream cycle delay"). Status query: "dream cycle?", "dream delay?", etc.
- **Shutdown flow (session 41):** Ctrl+C → signal handler prints to console + sets `stop_event` + calls `signal_task_cancellation("shutdown")` so PlanExecutor bails at next step boundary. `GoalWorkerPool.shutdown(timeout=30)` calls `executor.shutdown(wait=False, cancel_futures=True)` + per-future deadlines. `archi_service.stop()` explicitly closes Discord bot via `asyncio.run_coroutine_threadsafe(_bot_client.close(), _bot_loop)`. `scripts/stop.py` is a nuclear kill option: `proc.kill()` / `taskkill /F /T`, triple detection (cmdline + cwd + project path), double-tap survivors.

---

## Safety Boundaries

**Config split (session 38, enhanced session 42):** `config/archi_identity.yaml` holds only static identity (name, role, timezone, working hours). Dynamic project data (active projects, interests, focus areas, autonomous tasks) lives in `data/project_context.json`, which Archi can read and write. All consumers (`idea_generator`, `autonomous_executor`, `message_handler`, `dream_cycle`) load project context via `src/utils/project_context.py`. The `auto_populate()` function (session 42) scans `workspace/projects/` subdirectories, reads vision/overview files, and populates project_context.json automatically — called from dream cycle when context is empty. The opportunity scanner reads actual project files to identify build/fix/connect opportunities.

**Protected files** (cannot be modified by autonomous actions):
plan_executor.py, safety_controller.py, config.py, git_safety.py, prime_directive.txt, rules.yaml, archi_identity.yaml, claude/ARCHITECTURE.md, claude/TODO.md, claude/SESSION_CONTEXT.md, claude/WORKFLOW.md, system_monitor.py, health_check.py, performance_monitor.py

**Approval-required paths** (autonomous modifications need Discord approval):
`src/` — any write_source or edit_file targeting src/ triggers a Discord DM to the owner asking for yes/no approval. Denied by default if Discord is offline or times out (5 min).

**Blocked commands:** rm -rf, format, shutdown, reboot, fork bombs, dd, registry edits, etc.

**Budget enforcement:** CostTracker checks before every OpenRouter call; hard stop at daily/monthly limits.

**Git safety:** pre_modify_checkpoint() before source changes, syntax check after, rollback on failure.

---

## Testing

**Unit tests** (`tests/unit/`): Fast, isolated component tests. 310+ tests across routing classifiers, history context, approval listener, cache, deferred requests, etc.

**Integration tests** (`tests/integration/`):
- `test_v2_pipeline.py` — **36 live API tests** covering the full v2 message pipeline. 8 test classes: TestFastPaths, TestModelClassification, TestConversationContext, TestModelSwitching, TestDreamCycleFrequency, TestCache, TestCodeWriting, TestSafety, TestPortability. Costs ~$0.008/run. Marked `@pytest.mark.live` — skip with `pytest -m "not live"`.

**Standalone harness** (`tests/integration/test_harness.py`): Same v2 pipeline codepath with CLI flags (`--quick`, `--category`, `--dry-run`). Auto-cleans test goals.

**Portability & security guards** (run on every test suite execution):
- No hardcoded Windows paths in `src/`
- No API keys (OpenRouter, Discord tokens) in source or test files
- `.env` confirmed gitignored
- `base_path()` resolves via `ARCHI_ROOT` env var or directory discovery

**Running tests:**
```
pytest tests/unit/ -m "not live"          # Unit tests only (free)
pytest tests/integration/test_v2_pipeline.py -v  # V2 pipeline (~$0.008)
python tests/integration/test_harness.py --quick  # 5 smoke tests
```

---

## Deferred Systems (Not Yet Built)

These were identified during the architecture evolution (sessions 47-48) and intentionally deferred:

| System | Why Deferred | Trigger to Build |
|--------|-------------|-----------------|
| Worker Skills | Optimization, not fix. Architect specs may provide enough focus. | Workers still underperform with good specs + QA |
| Plan Learning | Needs QA data to accumulate first. | 20+ goal outcomes accumulated |
| Suspendable Tasks | Most complex change. Needs stable pipeline first. | Input accumulator + crash recovery proven stable |
| Tiered Model Routing | Single model (Grok 4.1 Fast) sufficient for now. | Cheaper models appear or heavy reasoning needed |

## Design Decisions (Sessions 47-48)

Key decisions made during the architecture evolution, for context:

- All model calls use Grok 4.1 Fast via direct xAI API (not OpenRouter as primary)
- MCP is core infrastructure, not deferred — GitHub was first external server
- DAG scheduling over wave-based batching (~40-50 line change for significant improvement)
- Critic is a dedicated adversarial pass, separate from conformance QA (models prompted to confirm tend toward leniency)
- User Model is a cross-cutting resource, not a pipeline stage (queryable by any stage)
- Image gen stays local for privacy (NSFW prompts never go to external APIs)
- Estimated cost: ~$0.06-0.10/day at 300-500 calls, well within $5/day budget
