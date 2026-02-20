# Archi Project — Session Context

**All Claude docs live in `claude/`. Read them all at the start of any session.**
**Follow the workflow routine in `claude/WORKFLOW.md`.**
**Follow the coding standards in `claude/CODE_STANDARDS.md` for ALL changes.**

---

## What This Is

Jesse is building **Archi**, an autonomous AI agent that runs on his Windows PC, communicates via Discord, and does background work autonomously in "dream cycles" when idle. Archi uses an **API-only architecture**: **Grok 4.1 Fast (Reasoning)** via xAI direct as the default model for all reasoning, **Claude Haiku 4.5** for computer use tasks, and **local SDXL** (diffusers) for uncensored image generation. Users can switch API models on-the-fly via Discord commands. Discord is the only interface. The project lives in the user's selected folder.

**Local models are dead** (decided session 24). All local LLM infrastructure (LocalModel, backends/, model_detector, cuda_bootstrap, llama-cpp-python) is being removed. SDXL image generation stays — it uses diffusers/torch directly with zero dependency on the local model stack. The "switch to local" command is gone. Future direction: direct API provider support (e.g. xAI Grok API) as an alternative to OpenRouter.

## Current Status

50+ items completed through session 47. API-first migration, interface cleanup, v2 architecture refactor, dream cycle quality improvements, multi-step chat features, concurrent architecture, identity config split, shutdown hardening, memory persistence, loop detection, opportunity scanner, task reliability fixes, and Discord message tone overhaul are all done. See `claude/TODO.md` for the full completed/open item list.

**Last session:** Session 47 (Cowork) — **Fix _step_history crash, suggestion pick routing, message tone pass.** Fixed `AttributeError: 'PlanExecutor' object has no attribute '_step_history'` that blocked all task execution. Fixed suggestion-pick routing so affirmative replies ("go ahead", "sure") are recognized when there's one pending suggestion, instead of falling through to create_goal. Second pass on Discord message tone: removed all emoji prefixes, bold formatting, and verbose goal-description echoes from notifications (goal completion, morning report, hourly summary, ask_user, source approval, initiative announcements, interrupted task recovery, suggestion list). Files: `plan_executor.py`, `discord_bot.py`, `action_dispatcher.py`, `dream_cycle.py`, `goal_worker_pool.py`, `reporting.py`.

**Open work:** Re-evaluate loops/heartbeat/dream cycle, startup on boot, architecture review. See `claude/TODO.md`.

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

**Last updated:** 2026-02-20 (session 47)
