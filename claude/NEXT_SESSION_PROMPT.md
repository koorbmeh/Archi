# Session 250 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done (session 249)

**Habit tracker** — flexible daily habit system generalizing the supplement tracker pattern.

1. **New `src/tools/habit_tracker.py`** (~270 lines) — `HabitTracker` class supporting boolean ("meditate"), count ("8 glasses water"), and duration ("30 minutes reading") habits. CRUD, completion logging (single + bulk), analysis (streak, adherence_rate, incomplete_today, per-habit progress), formatting (list, daily status, report, reminder), JSON persistence with 90-day trimming.
2. **4 action handlers** in `action_dispatcher.py` — `add_habit`, `remove_habit`, `log_habit`, `habit_status`. Smart type inference: if user provides a unit like "minutes", auto-detects as duration type.
3. **Router intent** `"habit"` with full example set for all action variants.
4. **Heartbeat Phase 0.9955** — evening habit reminder (after 18:00, every 4 cycles, suppressed when user active).
5. **Morning digest** — habit status included in daily briefing via `_fetch_habit_status()`.
6. **+51 tests** in `test_habit_tracker.py`. Handler registration test updated. 155 pass in affected suites, 4007 pass full suite.

---

## What to work on this session

### Priority 1: Continue expanding real-world capabilities

Ideas (pick what's most impactful):
- **Bank statement import** — CSV parser for the finance tracker so Jesse can bulk-import transactions from bank exports. Add to `finance_tracker.py`: `import_csv()` that auto-detects common bank CSV formats (Chase, BofA, etc.).
- **Telegram inline keyboards** — Add interactive buttons to Telegram messages (approve/deny for email drafts, supplement/habit logging shortcuts, quick-reply options). Requires `InlineKeyboardMarkup` from python-telegram-bot.
- **Voice notes processing** — Telegram supports voice messages. Add a handler that transcribes voice notes (Whisper API or Google Speech-to-Text) and processes them through the router.
- **Habit + supplement quick-log** — Telegram inline keyboard with one-tap buttons for "took supplements" and "done with habits". Would make daily logging much faster than typing.

### Priority 2: Content Strategy Phase 3 — Music generation

Still needs Jesse to choose Suno access method. Skippable until credentials are available.

---

## Jesse action needed

1. **Set up Telegram bot** — Message @BotFather on Telegram, create a new bot, copy the token to `.env` as `TELEGRAM_BOT_TOKEN`. Then message the bot and it will auto-discover your user ID. Install: `pip install python-telegram-bot`.
2. **Test habit tracker** — Try: "add habit meditate 20 minutes daily", "track water intake 8 glasses daily", "did my meditation", "drank 3 glasses of water", "habit status", "habit report". Also: "done with all my habits" to bulk-log.
3. **Test conversation quality fixes** — Send Archi messages that previously triggered bad responses.
4. **Choose Suno access method** — third-party API vs self-hosted. Unlocks Content Strategy Phase 3.

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all code changes.
- Protected files: `src/core/plan_executor/` (6 files), `src/core/safety_controller.py`, `src/utils/config.py`, `src/utils/git_safety.py`, `config/rules.yaml`, `config/prime_directive.txt`, `config/mcp_servers.yaml`, `backup/`, `src/core/heartbeat.py`, `src/core/goal_manager.py`. **Note:** `claude/`, personality configs, and monitoring files are now writable.
- **Stay under 50% context window.** Wrap up proportional to what you did.
- **Never use AskUserQuestion tool.** Never delete files. Never attempt interactive confirmation.
- **Don't re-verify unchanged items.** Verification items are parked until next deploy.
