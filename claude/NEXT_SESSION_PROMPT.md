# Session 249 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done (session 248)

Six conversation quality fixes from today's log issues:
1. **Placeholder detection** — Easy-tier answers with `[list them]` etc. now escalate to complex tier
2. **Anti-confabulation** — Router knows its actual model names (Grok + Gemini), won't fabricate "Archi-2"
3. **Contextual create_goal replies** — Uses goal description instead of generic "On it — I'll work on that in the background"
4. **Banned fitness keywords** — `_BANNED_TOPIC_KEYWORDS` in idea_generator.py blocks workout/exercise/stretching iterations
5. **Worldview cleanup** — Removed 3 health sub-interests driving repetitive content, replaced with puppy training + finance
6. **PlanExecutor file awareness** — Added `claude/` directory to read_file docs so model doesn't write Python scripts to discover files

Also: protected files list trimmed (claude/, personality configs, monitoring files now writable by Archi). +15 tests.

---

## What to work on this session

### Priority 1: Continue expanding real-world capabilities

Ideas (pick what's most impactful):
- **Habit tracker** — Generalize the supplement tracker pattern into a flexible habit system (exercise, reading, water, meditation, etc.). New file: `src/tools/habit_tracker.py`. Reuse the Supplement pattern: define habits, log completions, track streaks, morning reminders.
- **Bank statement import** — CSV parser for the finance tracker so Jesse can bulk-import transactions from bank exports. Add to `finance_tracker.py`: `import_csv()` that auto-detects common bank CSV formats (Chase, BofA, etc.).
- **Telegram inline keyboards** — Add interactive buttons to Telegram messages (approve/deny for email drafts, supplement logging shortcuts, quick-reply options). Requires `InlineKeyboardMarkup` from python-telegram-bot.
- **Voice notes processing** — Telegram supports voice messages. Add a handler that transcribes voice notes (Whisper API or Google Speech-to-Text) and processes them through the router.

### Priority 2: Content Strategy Phase 3 — Music generation

Still needs Jesse to choose Suno access method. Skippable until credentials are available.

---

## Jesse action needed

1. **Set up Telegram bot** — Message @BotFather on Telegram, create a new bot, copy the token to `.env` as `TELEGRAM_BOT_TOKEN`. Then message the bot and it will auto-discover your user ID. Install: `pip install python-telegram-bot`.
2. **Test conversation quality fixes** — Send Archi messages that previously triggered bad responses: emotional statements, asking "what model are you using?", requesting file listings. Verify no more "Got it, thanks!" or placeholder brackets.
3. **Choose Suno access method** — third-party API vs self-hosted. Unlocks Content Strategy Phase 3.

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all code changes.
- Protected files: `src/core/plan_executor/` (6 files), `src/core/safety_controller.py`, `src/utils/config.py`, `src/utils/git_safety.py`, `config/rules.yaml`, `config/prime_directive.txt`, `config/mcp_servers.yaml`, `backup/`, `src/core/heartbeat.py`, `src/core/goal_manager.py`. **Note:** `claude/`, personality configs, and monitoring files are now writable.
- **Stay under 50% context window.** Wrap up proportional to what you did.
- **Never use AskUserQuestion tool.** Never delete files. Never attempt interactive confirmation.
- **Don't re-verify unchanged items.** Verification items are parked until next deploy.
