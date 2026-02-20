# Implementation Session Prompt

Copy everything below this line and paste it to start a new session.

---

You are picking up development on **Archi**, an autonomous AI agent. The 9-phase architecture evolution is complete. Your job is to tackle the remaining open work items.

## Read These First (in order)

1. `claude/SESSION_CONTEXT.md` — Project overview, what Archi is, current status.
2. `claude/CODE_STANDARDS.md` — Coding conventions. Apply to ALL changes.
3. `claude/ARCHITECTURE.md` — Current execution flows and file map.
4. `claude/WORKFLOW.md` — Session routine.
5. `claude/TODO.md` — Full work history and open items.
6. `claude/ARCHITECTURE_PROPOSAL.md` — The evolution spec (all 9 phases done). Useful as reference for how systems were designed.

## What's Done

All 9 phases of the architecture evolution are complete (sessions 48-57). The proposal defined 13 systems to build across 9 migration phases. The pipeline is:

```
Jesse (Discord)
  → Local Fast-Paths (slash commands, image gen, datetime)
  → Conversational Router (intent + easy-tier answers in one call)
  → Message Handler dispatch
  → For goals: Discovery → Architect → DAG Scheduler → Workers
       → per-task QA → Integrator → Goal QA → Critic → Notify
  → MCP tool routing (local + GitHub servers)
  → Provider fallback chain (xai → openrouter → deepseek → openai → anthropic → mistral)
```

**The 13 systems built:**
1. **Conversational Router + Assessor** — Single model call per inbound message replaces all heuristic routing. Input accumulation for chunked answers.
2. **Goal Decomposer + Architect** — Produces task specs with deps, interfaces, files to create.
3. **QA Evaluator + Critic** — 3-layer quality: Reflection (free), per-task QA, adversarial per-goal Critic.
4. **Context Compression** — Step history management in PlanExecutor. Older steps compressed, recent 3-5 full fidelity.
5. **Structured Output Contracts** — Schema validation on all model JSON responses. Auto re-prompt on failure.
6. **Integrator** — Post-completion assembly, cross-task fit checking, glue creation.
7. **Notification Formatter + Feedback** — Model-generated notifications, 👍/👎 reaction tracking, post-completion check.
8. **Error Recovery + Graceful Degradation** — Error classification (transient/mechanical/permanent) + provider fallback chain with circuit breakers.
9. **MCP Tool Integration** — Local + GitHub MCP servers, tool registry refactor, on-demand lifecycle.
10. **DAG Scheduler + Request Prioritization** — Event-driven task firing, reactive preempts proactive.
11. **Discovery Phase** — Pre-Architect file scanning, ranking, selective reading, compressed project brief.
12. **User Model** — Cross-cutting JSON store of Jesse's preferences, decision patterns, domain knowledge, style.
13. **File Security Hardening** — Canonical path resolution + workspace boundary enforcement.

**Phase summary:**
- **Phase 1** (session 48): PlanExecutor internals — context compression, structured output contracts, mechanical error recovery, reflection prompt, file security hardening.
- **Phase 2** (session 49): QA Evaluator + Critic — post-task quality gate, adversarial per-goal evaluation, loop detection removed.
- **Phase 3** (session 50): Notification Formatter + reaction-based feedback collection.
- **Phase 4** (session 51): Conversational Router + User Model — single model call per inbound message replaces all heuristic routing.
- **Phase 5** (session 53): Discovery Phase + Architect specs + event-driven DAG Scheduler + request prioritization.
- **Phase 6** (session 54): Integrator + Goal-level QA + Critic/User Model wiring.
- **Phase 7** (session 55): MCP tool integration — local + GitHub MCP servers, tool registry refactor, PlanExecutor MCP fallback.
- **Phase 8** (session 56): Provider fallback chain with circuit breakers, degraded mode visibility, dream cycle pause on outage.
- **Phase 9** (session 57): Cleanup — heartbeat 2-tier simplification, intent classifier legacy removal, dead code sweep, legacy param cleanup.

**Also removed (Phase 9):**
- Loop detection (~120 lines)
- Heuristic routing (~200 lines)
- Intent classifier fast-paths (~80 lines)
- Response prefix logic (~40 lines)
- Hardcoded notification strings (~150 lines)
- Anti-pattern prompt injections (~60 lines)
- Heartbeat simplified to 2-tier (Command 10s / Idle 60s)

## Open Work Items

### 1. Startup on Boot (Visible Terminal)

Get Archi auto-starting on laptop reboot. Must launch in a visible terminal window, not as a background service — if Jesse logs in he needs to see it running.

**Approach ideas:**
- Windows Task Scheduler task triggered on logon
- Shortcut in the Startup folder (`shell:startup`)
- Either way, needs to open a visible `cmd.exe` or PowerShell window running `python -m src.service.archi_service`
- Consider: should it activate the venv first? The project uses `venv/` with a Windows venv.

**Key files:** `src/service/archi_service.py` (the entry point), `scripts/start.py` (current manual launcher)

### 2. Test Opportunity Scanner Live

Start Archi, let it go idle, watch logs for scanner output. Verify:
- Suggestions include build/ask/fix types (not just "research X")
- First dream cycle produces actionable goals like "Build supplement tracker" instead of "Research supplement timing"
- Test fallback by disabling scanner

This is a manual testing task — read Archi's logs, verify the opportunity scanner is producing good suggestions, and fix any issues found.

**Key files:** `src/core/opportunity_scanner.py`, `src/core/idea_generator.py`, `src/core/dream_cycle.py`

### 3. Review Architecture for Better Approaches

Step back and consider whether there's a better way to do any of the things we've already programmed Archi to do. Fresh eyes on the overall design. Areas to consider:
- Is the Discovery → Architect → DAG pipeline the right abstraction?
- Is the QA → Integrator → Critic post-completion pipeline worth the cost?
- Are there simpler patterns for things that feel over-engineered?
- Are there missing capabilities that would make Archi significantly more useful?

## Future Ideas (from TODO.md)

These aren't committed work — just ideas for when the open items are done:

- **Store conversation context in long-term memory** — Currently only task completions write to the vector store. Conversations, corrections, and decisions are lost between sessions.
- **Wire user_preferences into project_context** — When Archi learns something from conversation, update `project_context.json` automatically.
- **Discord command to add/remove projects** — Let Jesse manage active_projects via chat instead of editing JSON.
- **More direct provider tests** — Anthropic, DeepSeek, etc. beyond xAI.

## Key Constraints

- **Model:** Primary is Grok 4.1 Fast via direct xAI API. Fallbacks via the provider chain.
- **Platform:** Windows. Archi runs on Jesse's Windows PC. No Unix commands.
- **Budget:** Daily OpenRouter: $5.00, monthly: $100.00.
- **Protected files:** `src/core/plan_executor.py` and `src/core/safety_controller.py` — Archi can't modify them autonomously, but we (Jesse + Claude) can.
- **Tests:** Run `pytest tests/` to verify changes.

## Session Context Updates

When you finish a meaningful chunk of work, update:
- `claude/SESSION_CONTEXT.md` — Current status, last session summary
- `claude/TODO.md` — Mark completed items, add new open items
- `claude/ARCHITECTURE.md` — Add new files, update execution flow if needed

## Jesse's Preferences

- Conversational tone, not formal. Archi is a companion, not a corporate bot.
- "Cry once" philosophy — build things right the first time rather than debug interim solutions.
- Keep code concise. Follow CODE_STANDARDS.md strictly.
- Explain what you're doing and why before doing it. Don't silently make large changes.
