# Session 246 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done (session 245)

Built the **personal finance tracker** — a new practical daily-life capability for expense logging, subscription management, budget tracking, and spending analysis. Also fixed the `test_all_handlers_registered` test that was failing due to handlers added in sessions 241-244 (content calendar, content adapt, content image, supplement tracker).

**New module: `src/tools/finance_tracker.py` (~330 lines):**
- `log_expense(amount, category, description)` — logs an expense with automatic category normalization (20+ categories with alias matching: "uber" → transport, "pharmacy" → health, etc.)
- `add_subscription(name, amount, frequency)` / `cancel_subscription()` — recurring payment tracking with auto-calculated next due dates
- `get_monthly_subscription_cost()` — aggregates all active subs to monthly equivalent
- `set_budget(category, limit)` / `check_budgets()` — per-category or total monthly budgets with configurable alert thresholds
- Spending analysis: `get_spending_by_category()`, `get_total_spending()`, `get_expenses_for_period()`, `get_expenses_for_month()`
- Rich formatting: `format_spending_summary()`, `format_subscription_list()`, `format_budget_report()`, `format_monthly_report()`, `format_budget_alert()`
- Persistence to `data/finance_tracker.json` with 365-day expense retention

**Integration:**
- 5 action handlers in `action_dispatcher.py`: `log_expense`, `add_subscription`, `cancel_subscription`, `set_budget`, `finance_status`
- Router intent `finance` with full prompt examples for natural language parsing
- Heartbeat Phase 0.996: budget alert notification (every 8 cycles, suppressed when user active)

**Tests:** +39 new tests in `tests/unit/test_finance_tracker.py`. All pass. Also fixed `test_all_handlers_registered` (was missing 9 handlers from sessions 241-245).

---

## What to work on this session

### Priority 1: Expand Archi's real-world capabilities

The practical daily-life tools are growing (supplement tracker, finance tracker). Ideas for next:
- **Telegram bot** — second communication channel for mobile access. Would be a significant infrastructure addition.
- **Financial data import** — CSV/bank statement parsing to bulk-import expenses rather than manual entry only
- **Habit tracker** — general-purpose habit tracking beyond just supplements (exercise, reading, water, etc.)
- **Smart notifications pipeline** — weather-aware suggestions, news alerts matching interests, subscription renewal reminders

### Priority 2: Content Strategy Phase 3 — Music generation

Still needs Jesse to choose Suno access method. Skippable until credentials are available.

### Priority 3: End-to-end visual content test

The full pipeline (generate → host → publish) hasn't been tested live. Needs SDXL + GitHub PAT configured.

---

## Jesse action needed

1. **Delete git lock files** — `.git/index.lock` and `.git/HEAD.lock` are still blocking commits. Delete both, then run: `git add -A && git commit -m "Sessions 241-245: content calendar, visual pipeline, image hosting, supplement tracker, finance tracker"`.
2. **Test finance tracker** — Discord: "spent $50 on groceries", "add subscription Netflix $15.99/month", "what did I spend this week?", "set budget groceries $500/month", "budget report".
3. **Test supplement tracker** — Discord: "add supplement creatine 5g daily", "took my supplements", "supplement status".
4. **Choose Suno access method** — third-party API vs self-hosted. Unlocks Content Strategy Phase 3.

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all code changes.
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`.
- **Stay under 50% context window.** Wrap up proportional to what you did.
- **Never use AskUserQuestion tool.** Never delete files. Never attempt interactive confirmation.
- **Don't re-verify unchanged items.** Verification items are parked until next deploy.
