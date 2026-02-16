# Archi Project — Session Context

**All Claude docs live in `claude/`. Read them all at the start of any session.**
**Follow the workflow routine in `claude/WORKFLOW.md`.**
**Follow the coding standards in `claude/CODE_STANDARDS.md` for ALL changes.**

---

## What This Is

Jesse is building **Archi**, an autonomous AI agent that runs on his Windows PC, communicates via Discord, and does background work autonomously in "dream cycles" when idle. Archi uses an **API-only architecture**: **Grok 4.1 Fast** via OpenRouter as the default model for all reasoning (~$0.52-1.04/day), **Claude Haiku 4.5** for computer use tasks, and **local SDXL** (diffusers) for uncensored image generation. Users can switch API models on-the-fly via Discord commands. Discord is the only interface. The project lives in the user's selected folder.

**Local models are dead** (decided session 24). All local LLM infrastructure (LocalModel, backends/, model_detector, cuda_bootstrap, llama-cpp-python) is being removed. SDXL image generation stays — it uses diffusers/torch directly with zero dependency on the local model stack. The "switch to local" command is gone. Future direction: direct API provider support (e.g. xAI Grok API) as an alternative to OpenRouter.

## Current Status

40+ items completed through session 19. API-first migration, interface cleanup, v2 architecture refactor, dream cycle quality improvements, and multi-step chat features are all done. See `claude/ARCHI_TODO.md` for the full completed/open item list.

**Last session:** Session 29 (Cowork) — Codebase audit Phase 7 complete. README.md project structure updated to match all 56 src/ files. Discord bot permissions checklist added (P1-19 closed). ARCHITECTURE.md directory layout corrected. test_harness.py path fixed across all docs. workspace/.gitkeep created. CODE_STANDARDS.md code style section populated. archi.service deployment instructions inlined (file never existed).

**Open work:** Audit complete. All 7 phases done. See `claude/ARCHI_TODO.md` for full archive.

## Claude Docs Index

- `claude/SESSION_CONTEXT.md` — This file. Project overview, current status, constraints.
- `claude/WORKFLOW.md` — How to run a session: startup, doing work, wrapping up.
- `claude/CODE_STANDARDS.md` — Coding conventions, conciseness rules, quality attributes, logging standards. Apply to ALL changes.
- `claude/ARCHITECTURE.md` — Execution flows, file locations, config values, known issues.
- `claude/ARCHI_TODO.md` — The work queue (completed archive + open items + audit progress tracker).
- `claude/AUDIT_PROMPT.md` — The codebase audit prompt. Copy-paste to start audit sessions.
- `claude/TEST_PROMPTS.md` — Manual Discord test prompts for verifying all systems.

## Key Constraints

- `src/core/plan_executor.py` and `src/core/safety_controller.py` are **protected files** — Archi can't modify them autonomously, but we (Jesse + Claude) can
- Changes should be tested where possible (`pytest tests/`)
- The agent runs on Windows (PowerShell for shell commands)
- Local LLM infrastructure has been **removed entirely** (session 24). SDXL image gen works independently via diffusers.
- Daily OpenRouter budget: $5.00, monthly: $100.00
- **Cowork session has Desktop Commander access** — full filesystem access to Jesse's Windows machine via MCP, in addition to the Cowork VM's mounted folder

**Last updated:** 2026-02-16
