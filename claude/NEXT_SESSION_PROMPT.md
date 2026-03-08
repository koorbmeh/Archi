# Session 248 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done (session 247)

1. **Dual-channel notifications** — All notifications (heartbeat, goal completions, morning reports, proactive messages) now mirror to Telegram alongside Discord. `_mirror_to_telegram()` in discord_bot.py, with Telegram-only fallback in reporting.py if Discord is down. +6 tests.

2. **Smart daily briefing** — Morning digest now includes supplement status (taken/not-taken, low stock) and finance snapshot (month-to-date spending, subscriptions, budget alerts) alongside weather, calendar, email, and news. On-demand via "daily briefing" / "how's my day looking?" Discord/Telegram commands. +7 tests.

---

## What to work on this session

### Priority 1: Continue expanding real-world capabilities

Ideas (pick what's most impactful):
- **Habit tracker** — Generalize the supplement tracker pattern into a flexible habit system (exercise, reading, water, meditation, etc.). New file: `src/tools/habit_tracker.py`. Reuse the Supplement pattern: define habits, log completions, track streaks, morning reminders.
- **Bank statement import** — CSV parser for the finance tracker so Jesse can bulk-import transactions from bank exports. Add to `finance_tracker.py`: `import_csv()` that auto-detects common bank CSV formats (Chase, BofA, etc.).
- **Telegram inline keyboards** — Add interactive buttons to Telegram messages (approve/deny for email drafts, supplement logging shortcuts, quick-reply options). Requires `InlineKeyboardMarkup` from python-telegram-bot.
- **Recurring reminders** — Let Jesse set reminders: "remind me to stretch every 2 hours", "remind me about the dentist on Tuesday". Heartbeat integration for time-based delivery.
- **Voice notes processing** — Telegram supports voice messages. Add a handler that transcribes voice notes (Whisper API or Google Speech-to-Text) and processes them through the router.

### Priority 2: Content Strategy Phase 3 — Music generation

Still needs Jesse to choose Suno access method. Skippable until credentials are available.

---

## Jesse action needed

1. **Set up Telegram bot** — Message @BotFather on Telegram, create a new bot, copy the token to `.env` as `TELEGRAM_BOT_TOKEN`. Then message the bot and it will auto-discover your user ID. Install: `pip install python-telegram-bot`.
2. **Test dual-channel notifications** — Once Telegram is set up, notifications should appear on both Discord and Telegram simultaneously.
3. **Test daily briefing** — Discord: "daily briefing" or "how's my day looking?" — should show weather, calendar, supplements, finances, inbox, and news.
4. **Choose Suno access method** — third-party API vs self-hosted. Unlocks Content Strategy Phase 3.

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all code changes.
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`.
- **Stay under 50% context window.** Wrap up proportional to what you did.
- **Never use AskUserQuestion tool.** Never delete files. Never attempt interactive confirmation.
- **Don't re-verify unchanged items.** Verification items are parked until next deploy.
