# Archi Architecture Evolution — Implementation Spec

v2: 2026-02-20 (sessions 47-48). Status: **approved for implementation**.

All model calls use Grok 4.1 Fast via direct xAI API ($0.20/M input, $0.50/M output).

---

## Thesis

Replace hand-tuned heuristics with model calls, introduce specialized pipeline stages instead of one generalist executor, and fix root causes (context bloat, vague specs, no quality feedback) rather than symptoms (loop detection, escalating warnings, tone passes). The core engine (goal manager, worker pool, PlanExecutor step loop, opportunity scanner) is solid and stays. The layers around it get rebuilt.

---

## Pipeline

```
Jesse (Discord)
    │
    ▼
Local Fast-Paths ─── slash commands, image gen, datetime ──► handle directly
    │ (not matched)                     ┌──────────────────────────────────┐
    ▼                                   │ User Model                       │
Conversational Router + Assessor ◄──────┤ Cross-cutting resource:          │
    │   returns: intent +               │ preferences, decision patterns,  │
    │     complexity tier               │ domain knowledge, style.         │
    │   accumulates chunked input       │ Queryable by any pipeline stage. │
    │                                   │ Accumulates from interactions.   │
    ├── Easy ──► answer in same call    │                                  │
    │             ──► respond           └──────┬───┬───┬───┬──────────────┘
    │                                          │   │   │   │
    └── Complex                                │   │   │   │
         │                                     │   │   │   │
         ├── References project/files?         │   │   │   │
         │   ──► Discovery ◄───────────────────┘   │   │   │
         │         ──► project brief                │   │   │
         │                │                         │   │   │
         │◄───────────────┘                         │   │   │
         ▼                                          │   │   │
    Goal Decomposer + Architect ◄───────────────────┘   │   │
         │   receives: goal + discovery brief           │   │
         │   returns: task list with specs, deps        │   │
         │                                              │   │
         ▼                                              │   │
    DAG Scheduler ─── fires tasks as deps complete      │   │
         │   (reactive tasks preempt background work)   │   │
         ▼                                              │   │
    Workers (PlanExecutor) ◄────────────────────────────┘   │
         │   Context Compression                            │
         │   Structured Output Contracts                    │
         │   Mechanical Error Recovery                      │
         │   Reflection (self-check before "done")          │
         │   Tools via MCP (local + external servers)       │
         │   File Security (path validation)                │
         │   │                                              │
         │   └──► QA Evaluator (per-task)                   │
         │         accept / reject-with-feedback / fail     │
         ▼                                                  │
    Integrator ─── assemble pieces, check fit, create glue  │
         │                                                  │
         ▼                                                  │
    QA Evaluator (goal-level conformance)                   │
         │                                                  │
         ▼                                                  │
    Critic (adversarial) ◄──────────────────────────────────┘
         │   what's wrong? what breaks?
         │   would Jesse actually use this?
         │   significant concerns → route back
         │
         ▼
    Notification Formatter + Feedback (👍/👎)
```

---

## Systems to Build (13)

### 1. Conversational Router + Assessor (merged)
**Replaces:** `_parse_suggestion_pick`, `_is_likely_new_command`, `_check_pending_question`, `_check_pending_approval`, `_is_cancel_request`, `_infer_reply_topic`, greeting/datetime/deferred fast-paths in intent_classifier, prefix-assembly logic in response_builder.

Single model call per inbound message. Receives message + context state (pending suggestions/approval/question/active goal). Returns: intent classification (affirmation, new request, clarification, etc.) + complexity tier (easy/complex). For easy requests, includes the answer directly — one call total.

**Input accumulation:** For list-type questions (e.g. supplements one at a time), keeps question open and collects items. Checkpoints intent alongside items (which task needs this, why). Confirms each item, finalizes when user signals done or silence timeout. Designed to plug into Suspendable Tasks later.

**Local fast-paths (no API call):** Slash commands, image gen + model selection (privacy — NSFW prompts stay local), datetime, cancel/stop.

**Files:** `src/interfaces/discord_bot.py` (remove heuristic functions), `src/interfaces/intent_classifier.py` (replace fast-paths), `src/interfaces/response_builder.py` (remove prefix logic), new `src/core/conversational_router.py`.

### 2. Goal Decomposer + Architect (merged)
**Enhances:** Existing `goal_manager.decompose_goal()`.

Single model call that produces both task list and specs. For each task: description, files to create, inputs, outputs, dependencies, interfaces with other tasks. Separates thinking from doing — workers execute against a spec instead of discovering what to build mid-execution.

Receives Discovery brief (when available) and User Model preferences. Informed by Plan Learning outcomes (when available).

**Files:** `src/core/goal_manager.py` (enhance `decompose_goal()` prompt and parsing).

### 3. QA Evaluator + Critic (3 layers)
**Replaces:** Loop detection machinery (~120 lines), `_verify_output` quality scoring, all escalating warning injection, `_force_aborted` flag.

**Layer 1 — Reflection (free):** Prompt instruction in PlanExecutor: "Before calling done, review your output against the spec." No API call.

**Layer 2 — QA (per-task):** Separate model call reads output against Architect spec. Returns: accept / reject-with-feedback / fail. On reject, task retries with specific feedback. Deterministic checks first (file exists? parses? not empty?), then model for semantic quality.

**Layer 3 — Critic (per-goal, adversarial):** After Integrator assembles and conformance QA passes. Dedicated adversarial prompt: "What's wrong? What edge cases fail? What assumptions are bad? Would Jesse use this?" Significant concerns route back for remediation.

Hard step cap stays as safety net. Implementation uses tiered caps: 50 (general), 25 (coding), 12 (chat) — raised from the original 30 during session 32 because the budget and time caps are the primary constraints, and complex project work needs room to finish.

**Files:** `src/core/plan_executor.py` (Reflection prompt, remove loop detection), new `src/core/qa_evaluator.py`, new `src/core/critic.py`.

### 4. Context Compression
Manages PlanExecutor step history. After step 8, compress older steps into summaries. Recent 3-5 steps stay full fidelity. Options: simple truncation, sliding window, or selective retention (keep file-creation steps, compress think/fail steps).

**Files:** `src/core/plan_executor.py` (modify `_build_step_prompt`).

### 5. Structured Output Contracts
Schema dict mapping action names to expected fields/types. Validate every model JSON response before dispatch. On failure, auto re-prompt with specific error. Cap at 2 retries.

**Files:** `src/core/plan_executor.py` (add validation in step handler), new `src/core/output_schemas.py`.

### 6. Integrator
Post-completion model call across all task outputs. Checks pieces fit together, creates missing glue (entry points, config), produces human-readable summary of what was built and how to use it.

**Files:** New `src/core/integrator.py`, wire into `src/core/goal_worker_pool.py` after all tasks complete.

### 7. Notification Formatter + Feedback Collection
**Replaces:** All hand-built message strings in reporting.py, goal_worker_pool.py, dream_cycle.py, discord_bot.py.

Single model call per notification. Takes structured data, produces conversational message matching Archi's persona.

**Feedback:** Adds 👍/👎 reactions to completion messages. Watches for Jesse's reaction, records via `record_feedback()`. For significant goals (3+ tasks or 10+ min), appends "Anything you'd change?" Feeds into User Model and Learning System.

**Files:** New `src/core/notification_formatter.py`, `src/interfaces/discord_bot.py` (add reaction handling), `src/core/learning_system.py` (wire up `record_feedback()`), `src/core/reporting.py` (replace strings), `src/core/goal_worker_pool.py` (replace strings), `src/core/dream_cycle.py` (replace strings).

### 8. Self-Healing Error Recovery + Graceful Degradation
**Enhances:** PlanExecutor error handling.

Classify errors: transient (retry with backoff, no step burned) → mechanical (targeted fix, inject specific error) → permanent (fail immediately). Rule-based classifier, ~50-80 lines, no model call.

**API-level degradation:** When Grok goes down: retry → cached responses → rule-based handling → notify Jesse "degraded mode." Visible via `/status`.

**Files:** `src/core/plan_executor.py` (add error classifier), `src/core/model_router.py` or provider layer (add degradation cascade).

### 9. MCP Tool Integration
**Replaces:** Custom tool dispatch in `tool_registry.py`.

Archi becomes MCP client. Existing tools wrapped as local MCP server. New integrations = plug in MCP server. GitHub MCP server first.

Image gen routing stays local (privacy). On-demand server lifecycle (start when needed, stop after idle).

**Files:** `src/tools/tool_registry.py` (refactor to MCP client), `src/core/plan_executor.py` (update tool dispatch), new `src/tools/mcp_client.py`, new `src/tools/local_mcp_server.py`.

### 10. DAG Task Scheduler + Request Prioritization
**Replaces:** Wave-based batch loop in `task_orchestrator.py`.

Event-driven: task completes → check `can_start()` on all pending → submit unblocked tasks. Existing dependency infrastructure stays.

Two priority tiers: reactive (user Discord messages) preempts proactive (background work).

**Files:** `src/core/task_orchestrator.py` (~40-50 lines changed).

### 11. Discovery Phase
For complex goals referencing existing projects. Runs before Architect.

1. **Enumerate** files in project directory
2. **Rank** by relevance (entry points, READMEs, import graph, goal keywords)
3. **Read selectively** (signatures/structure for code, full for docs)
4. **Compress** into structured project brief via model call

Designed for future persistence (Code Memory pattern).

**Files:** New `src/core/discovery.py`, wire into goal pipeline in `src/core/goal_worker_pool.py`.

### 12. User Model (Digital Clone)
Cross-cutting resource. Structured JSON store of Jesse's preferences, decision patterns, domain knowledge, communication style.

Accumulates from: corrections, feedback reactions, stated preferences, observed patterns. Extracted as side effect of Router processing — no dedicated model call.

Queried by: Router (interpret ambiguous messages), Architect (shape specs), Critic ("would Jesse use this?"), Discovery (rank files), Notification Formatter (tone), Workers (code style).

Stored locally, never sent externally.

**Files:** New `src/core/user_model.py`, `src/core/conversational_router.py` (extraction hook).

### 13. File Security Hardening
Wraps all file operations with canonical path resolution. Resolves symlinks, verifies target is within workspace boundaries, logs operations. ~30-40 lines.

**Files:** `src/tools/tool_registry.py` or new `src/tools/security_validator.py`, wrap file tools.

---

## Systems to Remove (6)

| System | Location | ~Lines | Replaced By |
|--------|----------|--------|-------------|
| Loop detection | plan_executor.py | ~120 | QA + Reflection + Context Compression |
| Heuristic routing | discord_bot.py | ~200 | Conversational Router |
| Intent classifier fast-paths | intent_classifier.py | ~80 | Conversational Router |
| Response prefix logic | response_builder.py | ~40 | Conversational Router |
| Hardcoded notifications | reporting.py, goal_worker_pool.py, dream_cycle.py | ~150 | Notification Formatter |
| Anti-pattern prompt injections | plan_executor.py, goal_manager.py | ~60 | Architect specs + QA |

## Simplify (1)

| System | Change |
|--------|--------|
| Heartbeat 3-tier | → 2-tier: Command (10s) + Idle (60s). Drop deep sleep, night mode, evening multiplier. |

---

## Systems That Stay

- **Goal Manager** — CRUD, decomposition, state persistence
- **Worker Pool** — Concurrent goals, budget enforcement
- **PlanExecutor step loop** — Core read/write/search/done loop (enhanced by phases 1-2)
- **Opportunity Scanner** — Finds work from project files, error logs
- **Cost Tracker + Budget Enforcement**
- **Safety Controller** — Action authorization by risk level (enhanced by File Security)
- **Memory System** — 3-tier memory (enhanced by User Model + Plan Learning later)
- **Model Router + Provider System** — Grok direct for all calls currently
- **Discord Bot core** — DM interface, notification sending (routing logic replaced)

---

## Deferred (3)

| System | Why Deferred | Trigger to Build |
|--------|-------------|-----------------|
| Worker Skills | Optimization, not fix. Architect specs may provide enough focus. | Workers still underperform with good specs + QA |
| Plan Learning | Needs QA data to accumulate first. Log outcomes from Phase 2 onward. | 20+ goal outcomes accumulated |
| Suspendable Tasks | Most complex change. Needs stable pipeline first. | Input accumulator + crash recovery proven stable |

---

## Future Directions

- **Tiered Model Routing** — if cheaper models appear or Grok 3 needed for heavy reasoning
- **Richer Assessor handoffs** — once Skills exist, route to specialized handlers
- **Richer DAG features** — dynamic task injection, priority scheduling, dependency visualization

---

## Migration Path (9 Phases)

### Phase 1: PlanExecutor Internals + Security
**Build:** Context Compression, Structured Output Contracts, Mechanical Error Recovery, Reflection prompt, File Security Hardening.

**Scope:** All changes contained within `plan_executor.py` and tool layer. Zero risk to other systems. Run alongside existing loop detector as safety net.

**Files to modify:**
- `src/core/plan_executor.py` — add compression in `_build_step_prompt`, add schema validation in step handler, add error classifier, add Reflection instruction to "done" prompt, remove nothing yet
- `src/tools/tool_registry.py` — wrap file operations with path validation (or new `security_validator.py`)
- New: `src/core/output_schemas.py` — action schema definitions

**Done when:** Step counts on repeated goals measurably decrease. Schema validation catches malformed responses. Error recovery classifies errors correctly. File operations reject paths outside workspace.

**Test:** Run same goal 3x, compare step counts before/after. Deliberately trigger schema violations and path traversal attempts.

---

### Phase 2: QA + Critic
**Build:** QA Evaluator (per-task), Critic (per-goal adversarial).

**Scope:** Post-task and post-goal model calls. Independent of Phase 1 internals — reads outputs, doesn't change execution.

**Files to modify:**
- New: `src/core/qa_evaluator.py` — deterministic checks + model evaluation
- New: `src/core/critic.py` — adversarial prompt, concern routing
- `src/core/goal_worker_pool.py` — wire QA after task completion, Critic after goal completion
- `src/core/plan_executor.py` — remove loop detection once QA is proven (keep hard step cap)

**Done when:** QA catches real issues (reject + retry improves output). Critic identifies concerns that would have reached Jesse. Loop detection removed without regression.

**Test:** Run goals with intentionally vague specs. Verify QA rejects and retry produces better output. Verify Critic catches issues human review would catch.

---

### Phase 3: Notifications + Feedback ✅ (session 50)
**Build:** Notification Formatter, feedback collection (reactions + post-completion check).

**Implemented:** `notification_formatter.py` (~370 lines) — single model call per notification via Grok 4.1 Fast. All notification paths (goal completion, morning report, hourly summary, suggestions, findings, initiatives, idle, interrupted tasks, decomposition failures) route through the formatter with deterministic fallbacks. Discord `on_raw_reaction_add` handler tracks 👍/👎 reactions on tracked messages and records via `learning_system.record_feedback()`. Significant goals (3+ tasks or 10+ min) append "Anything you'd change?"

**Files created:** `src/core/notification_formatter.py`
**Files modified:** `src/interfaces/discord_bot.py`, `src/core/goal_worker_pool.py`, `src/core/reporting.py`, `src/core/dream_cycle.py`

---

### Phase 4: Inbound Routing + User Model Foundation
**Build:** Conversational Router (merged with Assessor), User Model basic store.

**Scope:** Single model call replaces intent classifier + all heuristic routing. Most impactful change for conversation quality.

**Files to modify:**
- New: `src/core/conversational_router.py`
- New: `src/core/user_model.py` — JSON store + preference extraction
- `src/interfaces/discord_bot.py` — remove `_parse_suggestion_pick`, `_is_likely_new_command`, etc.
- `src/interfaces/intent_classifier.py` — remove fast-paths folded into Router
- `src/interfaces/response_builder.py` — remove prefix logic
- `src/interfaces/message_handler.py` — route through new Router

**Done when:** All message types (affirmations, new requests, clarifications, ambiguous replies) route correctly via Router. Easy questions answered in single call. Input accumulation works for multi-message answers. User Model stores basic preferences.

**Test:** Replay past conversation logs through Router. Verify correct classification on messages that previously failed (e.g. "I have no idea what any of that is, but go ahead I guess"). Test chunked input collection.

---

### Phase 5: Planning + Scheduling
**Build:** Architect (merged into Decomposer), Discovery phase, DAG scheduler with request prioritization.

**Scope:** Biggest architectural change. Transforms how goals are planned and executed.

**Files to modify:**
- `src/core/goal_manager.py` — enhance `decompose_goal()` to produce specs + dependencies
- New: `src/core/discovery.py` — file enumeration, ranking, selective reading, compression
- `src/core/task_orchestrator.py` — replace wave loop with event-driven DAG (~40-50 lines)
- `src/core/goal_worker_pool.py` — wire Discovery before Architect, pass priority tiers

**Done when:** Architect produces concrete specs (not vague "write X and test it"). Discovery scans projects and produces useful briefs. Tasks fire as deps complete (not wave boundaries). User messages get immediate attention during background work.

**Test:** Compare Architect specs vs old decomposer output on same goal. Measure wall-clock time improvement from DAG vs waves on 3+ task goals. Verify reactive priority preemption.

---

### Phase 6: Integration
**Build:** Integrator, wire goal-level QA + Critic.

**Scope:** Post-completion synthesis. Makes the difference between "I made 4 files" and "I built X — run `python main.py` to start."

**Files to modify:**
- New: `src/core/integrator.py`
- `src/core/goal_worker_pool.py` — wire Integrator after all tasks, before notification
- `src/core/qa_evaluator.py` — add goal-level evaluation mode
- `src/core/critic.py` — wire User Model queries ("would Jesse use this?")

**Done when:** Integrator catches cross-task issues (mismatched imports, missing entry points). Produces coherent summaries. Critic catches issues using User Model context. Notification Formatter receives Integrator summary.

**Test:** Run multi-task goal. Verify Integrator checks cross-file references. Verify Critic uses User Model to flag style/approach mismatches.

---

### Phase 7: MCP
**Build:** MCP client, local MCP server wrapping existing tools, GitHub MCP server.

**Scope:** Tool system refactor. Contained to tool layer.

**Files to modify:**
- New: `src/tools/mcp_client.py`
- New: `src/tools/local_mcp_server.py` — wraps existing file/search/code tools
- `src/tools/tool_registry.py` — refactor to discover MCP servers
- `src/core/plan_executor.py` — update tool dispatch to use MCP
- Config: MCP server registry (which servers, how to start them)

**Done when:** Existing tools work through MCP. GitHub MCP server connects and Archi can check issues/read repos. Adding a new MCP server requires only config, not code.

**Test:** Run existing goals through MCP-wrapped tools. Verify identical behavior. Test GitHub operations. Test on-demand server start/stop.

---

### Phase 8: Graceful Degradation
**Build:** API-level fallback chain.

**Scope:** Extension of error recovery from Phase 1.

**Files to modify:**
- `src/core/model_router.py` or provider layer — add degradation cascade
- `src/interfaces/discord_bot.py` — add `/status` degradation visibility
- Cache layer for common responses (optional)

**Done when:** Simulated API outage triggers graceful cascade. Jesse sees degraded mode status. Archi recovers automatically when API returns.

**Test:** Block Grok API temporarily. Verify fallback behavior. Verify recovery on reconnect.

---

### Phase 9: Cleanup
**Remove:** Loop detection (~120 lines), heuristic routing (~200 lines), intent fast-paths (~80 lines), prefix logic (~40 lines), hardcoded notification strings (~150 lines), anti-pattern prompt injections (~60 lines).

**Simplify:** Heartbeat to 2-tier (Command 10s / Idle 60s).

**Done when:** Removed code doesn't break anything. Codebase is cleaner. Heartbeat uses 2 tiers.

---

### After Stabilization
- Worker Skills — if workers underperform with good specs
- Plan Learning — when 20+ goal outcomes accumulated
- Suspendable Tasks — when pipeline is stable
- Tiered Model Routing — if cheaper models become available
- User Model continues to accumulate automatically

---

## Resolved Questions

| Question | Answer |
|----------|--------|
| Cost impact | ~$0.06-0.10/day at 300-500 calls. Well within $5/day budget. |
| Latency | Router+Assessor is one call (~2s) vs old intent classifier (~3-6s). Net improvement. |
| Heartbeat | Keep, simplify to 2-tier. Phase 9. |
| Learning System | Keep as-is. Wire up `record_feedback()`. Plan Learning reads same store later. |
| Model selection | Grok 4.1 Fast direct for all calls. No OpenRouter. No tiered routing. |
| MCP lifecycle | On-demand. Start on first use, stop after idle timeout. |
| First MCP server | GitHub. |
| DAG vs waves | DAG. ~40-50 line change in task_orchestrator.py. |
| Feedback mechanism | Reactions (👍/👎) + post-completion check for significant goals. |
