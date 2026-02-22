# Archi Project — Session Context

**All Claude docs live in `claude/`. Read them all at the start of any session.**
**Follow the workflow routine in `claude/WORKFLOW.md`.**
**Follow the coding standards in `claude/CODE_STANDARDS.md` for ALL changes.**

---

## What This Is

Jesse is building **Archi**, an autonomous AI agent that runs on his Windows PC, communicates via Discord, and does background work autonomously in "dream cycles" when idle. Archi uses an **API-only architecture**: **Grok 4.1 Fast (Reasoning)** via xAI direct as the default model for all reasoning, with **Claude Sonnet 4.6** via OpenRouter as an automatic escalation tier when Grok fails (QA rejections and schema failures — session 62). **Claude Haiku 4.5** for computer use tasks, and **local SDXL** (diffusers) for uncensored image generation. Users can switch API models on-the-fly via Discord commands. Discord is the only interface. The project lives in the user's selected folder.

**Local models are dead** (decided session 24). All local LLM infrastructure (LocalModel, backends/, model_detector, cuda_bootstrap, llama-cpp-python) is being removed. SDXL image generation stays — it uses diffusers/torch directly with zero dependency on the local model stack. The "switch to local" command is gone. Future direction: direct API provider support (e.g. xAI Grok API) as an alternative to OpenRouter.

## Current Status

87 sessions completed. All major architecture, security, and quality items resolved. See `claude/TODO.md` for the consolidated completed work archive and open items.

**Last session:** Cowork session 87 — TODO.md cleanup (826→659 lines), log review found two bugs: (1) suggestion_pick fall-through in discord_bot.py caused "Sounds good, thanks" to be misclassified as a new goal; (2) test source entries polluting conversations.jsonl. Both fixed. Onboarding script added to future work. 1230 tests passing.

**Open work:** git_safety multi-file checkpoint (acceptable tradeoff), onboarding script (future work). See `claude/TODO.md`.

## Claude Docs Index

- `claude/SESSION_CONTEXT.md` — This file. Project overview, current status, open work, constraints.
- `claude/WORKFLOW.md` — How to run a session: startup, doing work, wrapping up.
- `claude/CODE_STANDARDS.md` — Coding conventions, conciseness rules, quality attributes, logging standards. Apply to ALL changes.
- `claude/ARCHITECTURE.md` — Execution flows, file locations, config values, known issues.
- `claude/TODO.md` — The work queue (completed archive + open items).
- `claude/archive/` — Completed reference docs: ARCHITECTURE_PROPOSAL.md (original evolution spec), VERIFICATION_REPORT.md (audit results), AUDIT_PROMPT.md, PLAN.md, VERIFICATION_PROMPT.md, PATCH_PROMPT.md, IMPLEMENTATION_PROMPT.md.

## Open Work Items

### 1. Lower Priority

git_safety multi-file checkpoint (acceptable tradeoff).

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

**Last updated:** 2026-02-22 (session 87 — TODO cleanup, log review, bug fixes)
