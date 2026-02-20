# Architecture Evolution Verification Prompt

Copy everything below this line and paste it to start a new session.

---

You are auditing the implementation of Archi's architecture evolution. The design was done in sessions 47-48, and implementation was done across sessions 48-57. Your job is to verify every item was actually implemented by reading the code and checking each box below.

## Read These First

1. `claude/ARCHITECTURE_PROPOSAL.md` — The spec that was supposed to be implemented.
2. `claude/SESSION_CONTEXT.md` — Current project status.
3. `claude/CODE_STANDARDS.md` — Coding conventions.

## How To Verify

For each item below, read the relevant source files and confirm the feature exists and works as described. Mark each as:
- ✅ **Implemented** — code exists and matches the spec
- ⚠️ **Partial** — some aspects implemented, others missing
- ❌ **Missing** — not found in the codebase
- 🔄 **Changed** — implemented differently than spec'd (explain how)

Report your findings for every single item. Don't skip any.

---

## CHECKLIST: 13 Systems to Build

### System 1: Conversational Router + Assessor
**Expected file:** `src/core/conversational_router.py` (new)
**Files modified:** `discord_bot.py`, `intent_classifier.py`, `response_builder.py`, `message_handler.py`

- [ ] Single model call per inbound message (replaces all heuristic routing)
- [ ] Receives message + context state (pending suggestions/approval/question/active goal)
- [ ] Returns intent classification (affirmation, new request, clarification, etc.)
- [ ] Returns complexity tier (easy/complex)
- [ ] Easy requests: answer included directly in same call (one call total, no second call)
- [ ] **Input accumulation:** List-type questions keep open, collect items across messages
- [ ] Input accumulation checkpoints intent alongside items (which task needs this, why)
- [ ] Confirms each item, finalizes on user signal or silence timeout
- [ ] **Local fast-paths (no API call):** Slash commands handled locally
- [ ] Local fast-path: Image gen + model selection stays local (privacy — NSFW prompts don't go to API)
- [ ] Local fast-path: Datetime handled locally
- [ ] Local fast-path: Cancel/stop handled locally
- [ ] Old heuristic functions removed from discord_bot.py: `_parse_suggestion_pick`, `_is_likely_new_command`, `_check_pending_question`, `_check_pending_approval`, `_is_cancel_request`, `_infer_reply_topic`
- [ ] Intent classifier fast-paths (greeting/datetime/deferred) removed or replaced
- [ ] Response builder prefix-assembly logic removed

### System 2: Goal Decomposer + Architect
**Expected enhancement to:** `src/core/goal_manager.py` (`decompose_goal()`)

- [ ] Single model call produces both task list AND specs
- [ ] Each task spec includes: description, files to create, inputs, outputs, dependencies, interfaces with other tasks
- [ ] Receives Discovery brief when available
- [ ] Receives User Model preferences when available
- [ ] Separates thinking from doing — workers execute against a spec

### System 3: QA Evaluator + Critic (3 layers)
**Expected files:** `src/core/qa_evaluator.py` (new), `src/core/critic.py` (new)

- [ ] **Layer 1 — Reflection:** Prompt instruction in PlanExecutor: "Before calling done, review your output against the spec." No API call.
- [ ] **Layer 2 — QA (per-task):** Separate model call reads output against Architect spec
- [ ] QA returns: accept / reject-with-feedback / fail
- [ ] On reject, task retries with specific feedback
- [ ] Deterministic checks first (file exists? parses? not empty?), then model for semantic quality
- [ ] **Layer 3 — Critic (per-goal, adversarial):** Runs after Integrator + conformance QA
- [ ] Critic adversarial prompt includes: "What's wrong? What edge cases fail? What assumptions are bad? Would Jesse use this?"
- [ ] Significant Critic concerns route back for remediation
- [ ] Hard step cap (30) stays as safety net
- [ ] Loop detection machinery removed (~120 lines)
- [ ] `_verify_output` quality scoring removed
- [ ] Escalating warning injection removed
- [ ] `_force_aborted` flag removed or renamed

### System 4: Context Compression
**Expected modification to:** `src/core/plan_executor.py` (`_build_step_prompt`)

- [ ] After step 8, older steps compressed into summaries
- [ ] Recent 3-5 steps stay full fidelity
- [ ] Compression method implemented (truncation, sliding window, or selective retention)

### System 5: Structured Output Contracts
**Expected files:** `src/core/output_schemas.py` (new)
**Expected modification to:** `src/core/plan_executor.py`

- [ ] Schema dict mapping action names to expected fields/types
- [ ] Every model JSON response validated before dispatch
- [ ] On validation failure, auto re-prompt with specific error
- [ ] Cap at 2 retries

### System 6: Integrator
**Expected file:** `src/core/integrator.py` (new)
**Wired into:** `src/core/goal_worker_pool.py`

- [ ] Post-completion model call across all task outputs
- [ ] Checks pieces fit together
- [ ] Creates missing glue (entry points, config)
- [ ] Produces human-readable summary of what was built and how to use it

### System 7: Notification Formatter + Feedback Collection
**Expected file:** `src/core/notification_formatter.py` (new)

- [ ] Single model call per notification
- [ ] Takes structured data, produces conversational message matching Archi's persona
- [ ] **All** hand-built message strings replaced in: reporting.py, goal_worker_pool.py, dream_cycle.py, discord_bot.py
- [ ] 👍/👎 reactions added to completion messages
- [ ] Discord reaction handler watches for Jesse's reaction
- [ ] Reactions recorded via `record_feedback()` (was dead code — now wired up)
- [ ] For significant goals (3+ tasks or 10+ min), appends "Anything you'd change?"
- [ ] Feedback feeds into User Model
- [ ] Feedback feeds into Learning System

### System 8: Self-Healing Error Recovery + Graceful Degradation
**Expected modification to:** `src/core/plan_executor.py`, provider layer

- [ ] Error classification: transient (retry with backoff, no step burned)
- [ ] Error classification: mechanical (targeted fix, inject specific error)
- [ ] Error classification: permanent (fail immediately)
- [ ] Rule-based classifier (~50-80 lines, no model call)
- [ ] **API-level degradation:** Retry → cached responses → rule-based handling → notify Jesse
- [ ] Provider fallback chain when Grok goes down
- [ ] Degraded mode visible via `/status`
- [ ] Archi recovers automatically when API returns

### System 9: MCP Tool Integration
**Expected files:** `src/tools/mcp_client.py` (new), `src/tools/local_mcp_server.py` (new)
**Modified:** `src/tools/tool_registry.py`, `src/core/plan_executor.py`

- [ ] Archi is an MCP client
- [ ] Existing tools wrapped as local MCP server
- [ ] GitHub MCP server connected (first external server)
- [ ] New integrations require only config, not code changes
- [ ] Image gen routing stays local (privacy)
- [ ] On-demand server lifecycle (start when needed, stop after idle)

### System 10: DAG Task Scheduler + Request Prioritization
**Expected modification to:** `src/core/task_orchestrator.py`

- [ ] Event-driven: task completes → check `can_start()` on all pending → submit unblocked
- [ ] Replaces wave-based batch loop
- [ ] Existing dependency infrastructure (`Task.dependencies`, `can_start()`, `get_ready_tasks()`) preserved
- [ ] Two priority tiers: reactive (user Discord messages) preempts proactive (background work)

### System 11: Discovery Phase
**Expected file:** `src/core/discovery.py` (new)
**Wired into:** `src/core/goal_worker_pool.py`

- [ ] Runs before Architect for complex goals referencing existing projects
- [ ] Step 1: Enumerate files in project directory
- [ ] Step 2: Rank by relevance (entry points, READMEs, import graph, goal keywords)
- [ ] Step 3: Read selectively (signatures/structure for code, full for docs)
- [ ] Step 4: Compress into structured project brief via model call
- [ ] Brief passed to Architect/Decomposer

### System 12: User Model (Digital Clone)
**Expected file:** `src/core/user_model.py` (new)

- [ ] Structured JSON store of Jesse's preferences, decision patterns, domain knowledge, communication style
- [ ] Accumulates from: corrections, feedback reactions, stated preferences, observed patterns
- [ ] Extraction happens as side effect of Router processing (no dedicated model call)
- [ ] Queryable by Router (interpret ambiguous messages)
- [ ] Queryable by Architect (shape specs)
- [ ] Queryable by Critic ("would Jesse use this?")
- [ ] Queryable by Discovery (rank files)
- [ ] Queryable by Notification Formatter (tone)
- [ ] Queryable by Workers (code style)
- [ ] Stored locally, never sent externally

### System 13: File Security Hardening
**Expected file:** `src/tools/security_validator.py` (new) or modification to `tool_registry.py`

- [ ] Canonical path resolution on all file operations
- [ ] Resolves symlinks
- [ ] Verifies target is within workspace boundaries
- [ ] Logs operations

---

## CHECKLIST: 6 Systems to Remove

- [ ] Loop detection removed from `plan_executor.py` (~120 lines)
- [ ] Heuristic routing removed from `discord_bot.py` (~200 lines)
- [ ] Intent classifier fast-paths removed from `intent_classifier.py` (~80 lines)
- [ ] Response prefix logic removed from `response_builder.py` (~40 lines)
- [ ] Hardcoded notification strings removed from `reporting.py`, `goal_worker_pool.py`, `dream_cycle.py` (~150 lines)
- [ ] Anti-pattern prompt injections removed from `plan_executor.py`, `goal_manager.py` (~60 lines)

---

## CHECKLIST: Simplification

- [ ] Heartbeat simplified from 3-tier to 2-tier
- [ ] Command tier: 10s interval
- [ ] Idle tier: 60s interval
- [ ] Deep sleep tier removed
- [ ] Night mode multiplier removed
- [ ] Evening multiplier removed

---

## CHECKLIST: Pipeline Flow (end-to-end)

Verify the full pipeline works as designed:

- [ ] Jesse sends Discord message
- [ ] Local fast-paths checked first (slash commands, image gen, datetime, cancel)
- [ ] If not matched: Conversational Router receives message + context
- [ ] Router classifies intent + complexity tier
- [ ] Easy path: answer returned in same Router call → response sent (one total API call)
- [ ] Complex path: Goal created
- [ ] If goal references project/files → Discovery runs first, produces brief
- [ ] Goal Decomposer + Architect receives goal + discovery brief + User Model preferences
- [ ] Architect produces task list with specs, deps, interfaces
- [ ] DAG Scheduler fires tasks as dependencies complete (not wave batches)
- [ ] Reactive tasks (user messages) preempt background work
- [ ] Workers execute against Architect specs
- [ ] Workers have: Context Compression, Structured Output Contracts, Error Recovery, Reflection
- [ ] Workers use tools via MCP (local + external servers)
- [ ] File operations go through security validation
- [ ] Per-task QA evaluates output (accept/reject/fail)
- [ ] After all tasks: Integrator assembles pieces, checks fit, creates glue
- [ ] Goal-level QA conformance check
- [ ] Critic adversarial review (queries User Model: "would Jesse use this?")
- [ ] Significant Critic concerns route back for remediation
- [ ] Notification Formatter produces conversational message
- [ ] 👍/👎 reactions added to completion message
- [ ] Feedback recorded and feeds into User Model + Learning System

---

## CHECKLIST: Specific Design Decisions from Sessions 47-48

These are specific decisions Jesse made during the design sessions. Verify they were honored:

- [ ] **All model calls use Grok 4.1 Fast via direct xAI API** (no OpenRouter for primary, no tiered routing)
- [ ] **OpenRouter NOT used as primary** (Jesse found it cost more in practice: $0.45 in hours vs $0.20 in much longer with Grok direct)
- [ ] **MCP is core, not deferred** (Jesse: "I feel like I would like Archi to be able to pick those things up right away")
- [ ] **DAG, not waves** (Jesse: "I don't know why we don't just make that simple change")
- [ ] **Input accumulation uses intent checkpointing** (inspired by LangGraph interrupt pattern)
- [ ] **Discovery uses ranked file scanning** (inspired by Aider's repo map: import graph, reference count)
- [ ] **Discovery reads selectively** (signatures over full content, inspired by Cursor's multi-stage compression)
- [ ] **Critic is a dedicated adversarial pass**, separate from conformance QA (models prompted to confirm tend toward leniency)
- [ ] **User Model is cross-cutting resource**, not a pipeline stage (queryable by any stage)
- [ ] **User Model stores locally, never sent externally**
- [ ] **Feedback via reactions** (👍/👎 on completion messages) + **post-completion check** ("Anything you'd change?") for significant goals
- [ ] **`record_feedback()` is wired up** (was dead code — never called — in learning_system.py)
- [ ] **Heartbeat simplified, not removed** (Jesse decided to keep it as 2-tier, not eliminate it)
- [ ] **GitHub is first MCP server** (Jesse: "Probably github")
- [ ] **Image gen stays local for privacy** (NSFW prompts don't go to external APIs)
- [ ] **Graceful degradation includes `/status` visibility** for Jesse to see when Archi is in degraded mode

---

## CHECKLIST: Deferred Items (should NOT be implemented yet)

Verify these were correctly deferred:

- [ ] **Worker Skills** — NOT implemented (trigger: workers still underperform with good specs + QA)
- [ ] **Plan Learning** — NOT implemented (trigger: 20+ goal outcomes accumulated)
- [ ] **Suspendable Tasks** — NOT implemented (trigger: input accumulator + crash recovery proven stable)
- [ ] **Tiered Model Routing** — NOT implemented (trigger: cheaper models appear or heavy reasoning needed)

---

## Summary Instructions

After checking every item above, produce a summary with:

1. **Fully Implemented** — count and list
2. **Partially Implemented** — count, list, and explain what's missing
3. **Missing** — count, list, and explain impact
4. **Changed from Spec** — count, list, and explain the difference
5. **Incorrectly Implemented Deferred Items** — anything that was supposed to be deferred but got built anyway
6. **Recommendations** — prioritized list of what to fix/complete next
