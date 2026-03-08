# Archi Project — Session Context

**All Claude docs live in `claude/`. Read them all at the start of any session.**
**Follow the workflow routine in `claude/WORKFLOW.md`.**
**Follow the coding standards in `claude/CODE_STANDARDS.md` for ALL changes.**

---

## What This Is

Jesse is building **Archi**, an autonomous AI agent that runs on his Windows PC, communicates via Discord, and does background work autonomously in "dream cycles" when idle. Archi uses an **API-only architecture**: **Grok 4.1 Fast (Reasoning)** via xAI direct as the default model, with **Gemini 3.1 Pro Preview** (`google/gemini-3.1-pro-preview`) via OpenRouter as the automatic escalation tier. **Claude Haiku 4.5** for computer use tasks, and **local SDXL** (diffusers) for uncensored image generation. Discord is the only interface. The project lives in the user's selected folder.

## Current Status

239+ sessions completed. See `claude/TODO.md` for open items and recent completed work. See `claude/NEXT_SESSION_PROMPT.md` for session-specific handoff context.

## Claude Docs Index

- `claude/SESSION_CONTEXT.md` — This file. Project overview, constraints, preferences.
- `claude/WORKFLOW.md` — How to run a session: startup, doing work, wrapping up.
- `claude/CODE_STANDARDS.md` — Coding conventions, conciseness rules, quality attributes, logging standards.
- `claude/ARCHITECTURE.md` — Execution flows, file locations, config values, known issues.
- `claude/TODO.md` — The work queue (open items + last 10 sessions of completed work).
- `claude/SELF_IMPROVEMENT.md` — **The autonomy roadmap. This drives the mission — not an afterthought.**
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
- **Ambitious over cautious** — Jesse wants sessions to push Archi's capabilities forward, not just maintain what exists. Roadmap work (SELF_IMPROVEMENT.md) is the primary mission, not an afterthought. Don't spend entire sessions re-verifying the same unchanged items.
- **Suggestions: quality over quantity** — When Archi suggests ideas to Jesse via Discord, send ONE genuinely good, relevant idea — not 5 mediocre ones. If there's no great idea, don't suggest anything. Silence beats noise.
- **Live testing must be quick and specific** — When something needs Jesse to verify, give exact steps: "Send this message, expect this response." Never say "wait 50 dream cycles." If it can't be tested quickly, redesign it.
- **Work autonomously** — pick work yourself and go. Jesse will override in the starter prompt if he wants a different priority.
- **Add TODO findings without asking** — spot bugs or improvements? Add them directly. Don't block on approval.
- **Quality over quantity** — Stay under ~50% context window. A clean handoff beats a rushed finish.
- **Keep wrap-ups proportional** — A session that built a new module needs thorough docs. A session that just researched something needs a good handoff note. Don't write walls of stats that repeat the same numbers every session.
- **Never use the AskUserQuestion tool.** Stalls Cowork sessions.
- **Never delete files in Cowork sessions.** Log to `claude/PENDING_DELETIONS.md` instead.
- **Never attempt any action requiring interactive confirmation.**

**Last updated:** 2026-03-07 (session 238)
