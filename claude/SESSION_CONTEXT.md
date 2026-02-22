# Archi Project — Session Context

**All Claude docs live in `claude/`. Read them all at the start of any session.**
**Follow the workflow routine in `claude/WORKFLOW.md`.**
**Follow the coding standards in `claude/CODE_STANDARDS.md` for ALL changes.**

---

## What This Is

Jesse is building **Archi**, an autonomous AI agent that runs on his Windows PC, communicates via Discord, and does background work autonomously in "dream cycles" when idle. Archi uses an **API-only architecture**: **Grok 4.1 Fast (Reasoning)** via xAI direct as the default model for all reasoning, with **Claude Sonnet 4.6** via OpenRouter as an automatic escalation tier when Grok fails (QA rejections and schema failures — session 62). **Claude Haiku 4.5** for computer use tasks, and **local SDXL** (diffusers) for uncensored image generation. Users can switch API models on-the-fly via Discord commands. Discord is the only interface. The project lives in the user's selected folder.

**Local models are dead** (decided session 24). All local LLM infrastructure (LocalModel, backends/, model_detector, cuda_bootstrap, llama-cpp-python) is being removed. SDXL image generation stays — it uses diffusers/torch directly with zero dependency on the local model stack. The "switch to local" command is gone. Future direction: direct API provider support (e.g. xAI Grok API) as an alternative to OpenRouter.

## Current Status

60+ items completed through session 75. API-first migration, interface cleanup, v2 architecture refactor, dream cycle quality improvements, multi-step chat features, concurrent architecture, identity config split, shutdown hardening, memory persistence, loop detection (now removed), opportunity scanner, task reliability fixes, Discord message tone overhaul, Phases 1-9 of the architecture evolution, verification patch-up, project sync, conversation memory, tiered model routing, idea history, shutdown reliability, code review critical fixes, code review security fixes, code review logic & correctness fixes, code review performance fixes, code review dependencies & configuration fixes, security test coverage, plan_executor SRP refactor, test coverage expansion, singleton standardization, discord_bot state encapsulation, and ComputerUse God class split are all done. See `claude/TODO.md` for the full completed/open item list.

**Last session:** Session 75 (Cowork) — Architecture & code quality: resolved all 3 remaining Architecture items. (1) Standardized singletons on double-checked locking + `_reset_for_testing()` across 5 modules. (2) Eliminated cross-module private state access in discord_bot.py via `kick_dream_cycle()` and `close_bot()` public APIs. (3) Extracted `ImageAnalyzer` from `ComputerUse` into `src/tools/image_analyzer.py`. 800 tests passing.

**Open work:** 🔵 Improvements 2 (architecture diagram, IVF index), startup on boot, Discord project management, provider tests. See `claude/TODO.md`.

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

### 2. Discord Command to Add/Remove Projects

Let Jesse manage active_projects via chat instead of editing JSON manually.

### 3. More Direct Provider Tests

Anthropic, DeepSeek, etc. beyond xAI.

## Key Constraints

- `src/core/plan_executor/` (all 6 files) and `src/core/safety_controller.py` are **protected files** — Archi can't modify them autonomously, but we (Jesse + Claude) can
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

**Last updated:** 2026-02-22 (session 75)
