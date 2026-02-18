# Archi Project — Session Context

**All Claude docs live in `claude/`. Read them all at the start of any session.**
**Follow the workflow routine in `claude/WORKFLOW.md`.**
**Follow the coding standards in `claude/CODE_STANDARDS.md` for ALL changes.**

---

## What This Is

Jesse is building **Archi**, an autonomous AI agent that runs on his Windows PC, communicates via Discord, and does background work autonomously in "dream cycles" when idle. Archi uses an **API-only architecture**: **Grok 4.1 Fast (Reasoning)** via xAI direct as the default model for all reasoning, **Claude Haiku 4.5** for computer use tasks, and **local SDXL** (diffusers) for uncensored image generation. Users can switch API models on-the-fly via Discord commands. Discord is the only interface. The project lives in the user's selected folder.

**Local models are dead** (decided session 24). All local LLM infrastructure (LocalModel, backends/, model_detector, cuda_bootstrap, llama-cpp-python) is being removed. SDXL image generation stays — it uses diffusers/torch directly with zero dependency on the local model stack. The "switch to local" command is gone. Future direction: direct API provider support (e.g. xAI Grok API) as an alternative to OpenRouter.

## Current Status

50+ items completed through session 43. API-first migration, interface cleanup, v2 architecture refactor, dream cycle quality improvements, multi-step chat features, concurrent architecture, identity config split, shutdown hardening, memory persistence, loop detection, opportunity scanner, and task reliability fixes are all done. See `claude/TODO.md` for the full completed/open item list.

**Last session:** Session 43 (Cowork) — **Fix task failures from live testing** — Ran Archi live and diagnosed why every task was failing. Root cause: `PLAN_MAX_TOKENS = 1000` was too low for Grok's reasoning model — `<think>` blocks consumed most of the budget, truncating JSON responses. 27/80 API responses hit exactly 1000 tokens. Fixed by raising to 4096. Also added `write_source`/`edit_file` to path-based loop detection keys (were falling through to bare action type), extended write-then-read exemption, and added SSL cert diagnostic logging for persistent arxiv.org failures.

**Open work:** Re-evaluate loops/heartbeat/dream cycle, startup on boot, architecture review, test concurrent goals, test wave-based parallelism, test ask-user, test proactive initiative, test opportunity scanner live. See `claude/TODO.md`.

## Claude Docs Index

- `claude/SESSION_CONTEXT.md` — This file. Project overview, current status, constraints.
- `claude/WORKFLOW.md` — How to run a session: startup, doing work, wrapping up.
- `claude/CODE_STANDARDS.md` — Coding conventions, conciseness rules, quality attributes, logging standards. Apply to ALL changes.
- `claude/ARCHITECTURE.md` — Execution flows, file locations, config values, known issues.
- `claude/TODO.md` — The work queue (completed archive + open items + audit progress tracker).
- `claude/AUDIT_PROMPT.md` — The codebase audit prompt. Copy-paste to start audit sessions.
- `claude/PLAN.md` — Last implementation plan (session 38 identity config split, completed). Kept for reference.

## Key Constraints

- `src/core/plan_executor.py` and `src/core/safety_controller.py` are **protected files** — Archi can't modify them autonomously, but we (Jesse + Claude) can
- Changes should be tested where possible (`pytest tests/`)
- The agent runs on Windows (PowerShell for shell commands)
- Local LLM infrastructure has been **removed entirely** (session 24). SDXL image gen works independently via diffusers.
- Daily OpenRouter budget: $5.00, monthly: $100.00
- **Cowork sessions** mount the Archi project folder, giving full read/write access to project files

**Last updated:** 2026-02-18 (session 43)
