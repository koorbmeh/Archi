# Archi Project — Session Context

**All Claude docs live in `claude/`. Read them all at the start of any session.**
**Follow the workflow routine in `claude/WORKFLOW.md`.**
**Follow the coding standards in `claude/CODE_STANDARDS.md` for ALL changes.**

---

## What This Is

Jesse is building **Archi**, an autonomous AI agent that runs on his Windows PC, communicates via Discord, and does background work autonomously in "dream cycles" when idle. Archi uses an **API-only architecture**: **Grok 4.1 Fast (Reasoning)** via xAI direct as the default model, with **Gemini 3.1 Pro Preview** (`google/gemini-3.1-pro-preview`) via OpenRouter as the automatic escalation tier. **Claude Haiku 4.5** for computer use tasks, and **local SDXL** (diffusers) for uncensored image generation. Discord is the only interface. The project lives in the user's selected folder.

## Current Status

212 sessions completed. See `claude/TODO.md` for open items and recent completed work. See `claude/NEXT_SESSION_PROMPT.md` for session-specific handoff context.

## Claude Docs Index

- `claude/SESSION_CONTEXT.md` — This file. Project overview, constraints, preferences.
- `claude/WORKFLOW.md` — How to run a session: startup, doing work, wrapping up.
- `claude/CODE_STANDARDS.md` — Coding conventions, conciseness rules, quality attributes, logging standards.
- `claude/ARCHITECTURE.md` — Execution flows, file locations, config values, known issues.
- `claude/TODO.md` — The work queue (open items + last 10 sessions of completed work).
- `claude/SELF_IMPROVEMENT.md` — Proactive improvement directives (what to work on when assigned tasks are done).
- `claude/archive/` — Completed work archive (sessions 1–96), original evolution spec, audit results, etc.

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
- **Work autonomously through TODO items** — pick the best sequence yourself and go. Jesse will override in the starter prompt if he wants a different order. Don't ask "what should I work on?" — just start.
- **Add TODO findings without asking** — if you spot bugs, improvements, or missing tests while working, add them directly to TODO.md. Mention what you added at wrap-up so Jesse can review.
- **Quality over quantity** — AI output degrades past ~50% context window usage (Jesse sometimes calls this "bandwidth"). Don't cram work into a session at the expense of quality. If there's more to do than fits cleanly, write a thorough handoff and let the next session continue. A clean handoff beats a rushed finish every time.
- **Never use the AskUserQuestion tool.** It causes frustrating delays in Cowork sessions. Asking questions inline (in normal text) is fine — just don't use the tool. This may be revisited in the future.
- **Never delete files in Cowork sessions.** Deletion requires manual approval and stalls the session. Log deletions to `claude/PENDING_DELETIONS.md` instead. See `claude/WORKFLOW.md` "Cowork Session Constraints" for details.
- **Never attempt any action requiring interactive confirmation.** If unsure, log it rather than risking a stall.

**Last updated:** 2026-03-06 (session 212 — "test" investigation deep-dive, croniter test skip fix)
