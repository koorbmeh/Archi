# Archi Project — Session Context

**All Claude docs live in `claude/`. Read them all at the start of any session.**
**Follow the workflow routine in `claude/WORKFLOW.md`.**
**Follow the coding standards in `claude/CODE_STANDARDS.md` for ALL changes.**

---

## What This Is

Jesse is building **Archi**, an autonomous AI agent that runs on his Windows PC, communicates via Discord, and does background work autonomously in "dream cycles" when idle. Archi uses an **API-only architecture**: **Grok 4.1 Fast (Reasoning)** via xAI direct as the default model for all reasoning, **Claude Haiku 4.5** for computer use tasks, and **local SDXL** (diffusers) for uncensored image generation. Users can switch API models on-the-fly via Discord commands. Discord is the only interface. The project lives in the user's selected folder.

**Local models are dead** (decided session 24). All local LLM infrastructure (LocalModel, backends/, model_detector, cuda_bootstrap, llama-cpp-python) is being removed. SDXL image generation stays — it uses diffusers/torch directly with zero dependency on the local model stack. The "switch to local" command is gone. Future direction: direct API provider support (e.g. xAI Grok API) as an alternative to OpenRouter.

## Current Status

50+ items completed through session 58. API-first migration, interface cleanup, v2 architecture refactor, dream cycle quality improvements, multi-step chat features, concurrent architecture, identity config split, shutdown hardening, memory persistence, loop detection (now removed), opportunity scanner, task reliability fixes, Discord message tone overhaul, Phases 1-9 of the architecture evolution, and verification patch-up are all done. See `claude/TODO.md` for the full completed/open item list.

**Last session:** Session 58 (Cowork) — Verification Patch-Up. Fixed 7 items from the architecture verification audit: (1) File security changed from blacklist to whitelist — `_validate_path_security()` now resolves canonical path and verifies it's within workspace root. (2) DAG priority preemption — added dedicated `_reactive_executor` in GoalWorkerPool so user-requested goals start immediately without waiting for proactive tasks. (3) User Model → Notification Formatter — added `get_context_for_formatter()` to user_model.py, injected into `_call_formatter()` prompts. (4) User Model → Discovery — added `get_context_for_discovery()`, wired into `_rank_files()` for personalized relevance scoring. (5) Response builder prefix — verified only used by message_handler.py complex-tier paths, documented why retained. (6) Integrator glue — now surfaces `missing_glue` in output; auto-creation evaluated and deferred (detection sufficient). (7) Step cap — spec updated to document tiered caps (50/25/12) as deliberate choice. Zero new test failures (490 pass, same pre-existing failures).

**Open work:** Startup on boot, test opportunity scanner live, review architecture for better approaches. See `claude/TODO.md`.

## Claude Docs Index

- `claude/SESSION_CONTEXT.md` — This file. Project overview, current status, open work, constraints.
- `claude/WORKFLOW.md` — How to run a session: startup, doing work, wrapping up.
- `claude/CODE_STANDARDS.md` — Coding conventions, conciseness rules, quality attributes, logging standards. Apply to ALL changes.
- `claude/ARCHITECTURE.md` — Execution flows, file locations, config values, known issues.
- `claude/TODO.md` — The work queue (completed archive + open items).
- `claude/archive/` — Completed reference docs: ARCHITECTURE_PROPOSAL.md (original evolution spec), VERIFICATION_REPORT.md (audit results), AUDIT_PROMPT.md, PLAN.md, VERIFICATION_PROMPT.md, PATCH_PROMPT.md, IMPLEMENTATION_PROMPT.md.

## Open Work Items

### 1. Startup on Boot (Visible Terminal)

Get Archi auto-starting on laptop reboot. Must launch in a visible terminal window, not as a background service — if Jesse logs in he needs to see it running.

**Approach ideas:** Windows Task Scheduler task on logon, or shortcut in `shell:startup`. Needs to open a visible terminal running `python -m src.service.archi_service`. Consider venv activation.

**Key files:** `src/service/archi_service.py`, `scripts/start.py`

### 2. Test Opportunity Scanner Live

Start Archi, let it go idle, watch logs for scanner output. Verify suggestions include build/ask/fix types (not just "research X"). Verify first dream cycle produces actionable goals. Test fallback by disabling scanner.

**Key files:** `src/core/opportunity_scanner.py`, `src/core/idea_generator.py`, `src/core/dream_cycle.py`

### 3. Review Architecture for Better Approaches

Fresh eyes on the overall design. Is the Discovery → Architect → DAG pipeline the right abstraction? Is the QA → Integrator → Critic post-completion pipeline worth the cost? Are there simpler patterns for things that feel over-engineered?

## Future Ideas

Not committed work — just ideas for when the open items are done:

- **Store conversation context in long-term memory** — Conversations, corrections, and decisions are lost between sessions.
- **Wire user_preferences into project_context** — When Archi learns something from conversation, update `project_context.json` automatically.
- **Discord command to add/remove projects** — Let Jesse manage active_projects via chat instead of editing JSON.
- **More direct provider tests** — Anthropic, DeepSeek, etc. beyond xAI.

## Key Constraints

- `src/core/plan_executor.py` and `src/core/safety_controller.py` are **protected files** — Archi can't modify them autonomously, but we (Jesse + Claude) can
- Changes should be tested where possible (`pytest tests/`)
- The agent runs on Windows (PowerShell for shell commands)
- Local LLM infrastructure has been **removed entirely** (session 24). SDXL image gen works independently via diffusers.
- Daily OpenRouter budget: $5.00, monthly: $100.00
- **Cowork sessions** mount the Archi project folder, giving full read/write access to project files

## Jesse's Preferences

- Conversational tone, not formal. Archi is a companion, not a corporate bot.
- "Cry once" philosophy — build things right the first time rather than debug interim solutions.
- Keep code concise. Follow CODE_STANDARDS.md strictly.
- Explain what you're doing and why before doing it. Don't silently make large changes.

**Last updated:** 2026-02-20 (session 58, consolidated session 59)
