# Session 247 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done (session 246)

Built the **Telegram bot interface** — Archi's second communication channel. `src/interfaces/telegram_bot.py` (~280 lines) mirrors Discord's message processing through Telegram's API, reusing the existing `conversational_router` + `action_dispatcher` for all logic. Commands: /start, /help, /status. Proactive notifications via `send_telegram_notification()`. Launches in a background thread alongside Discord. +22 tests, all pass.

---

## What to work on this session

### Priority 1: Continue expanding real-world capabilities

Ideas (pick what's most impactful):
- **Dual-channel notifications** — Wire `send_telegram_notification()` into the heartbeat's existing notification points so Archi messages Jesse on both Discord AND Telegram simultaneously. Small change, big value.
- **Habit tracker** — generalize the supplement tracker pattern into a flexible habit system (exercise, reading, water, meditation, etc.)
- **Smart daily briefing** — combine morning digest + calendar + supplement status + finance summary + weather into a single comprehensive daily briefing
- **Bank statement import** — CSV parser for the finance tracker so Jesse can bulk-import transactions
- **Telegram inline keyboards** — add interactive buttons to Telegram messages (approve/deny, supplement logging shortcuts)

### Priority 2: Content Strategy Phase 3 — Music generation

Still needs Jesse to choose Suno access method. Skippable until credentials are available.

---

## Jesse action needed

1. **Set up Telegram bot** — Message @BotFather on Telegram, create a new bot, copy the token to `.env` as `TELEGRAM_BOT_TOKEN`. Then message the bot and it will auto-discover your user ID. Install: `pip install python-telegram-bot`.
2. **Test finance tracker** — Discord: "spent $50 on groceries", "add subscription Netflix $15.99/month", "budget report".
3. **Test supplement tracker** — Discord: "add supplement creatine 5g daily", "took my supplements".
4. **Choose Suno access method** — third-party API vs self-hosted. Unlocks Content Strategy Phase 3.

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all code changes.
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`.
- **Stay under 50% context window.** Wrap up proportional to what you did.
- **Never use AskUserQuestion tool.** Never delete files. Never attempt interactive confirmation.
- **Don't re-verify unchanged items.** Verification items are parked until next deploy.
