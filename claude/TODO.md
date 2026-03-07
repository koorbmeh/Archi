# Archi — Todo List

Last updated: 2026-03-06 (session 228)

---

## Open Items

### Code changes needed

- [x] **Reduce suggestion quantity — quality over quantity** — (Added 2026-03-06, fixed session 223.) `suggest_work()` now returns at most 1 idea and only if it clears MIN_SUGGEST_SCORE (0.5). Silence beats noise.

- [x] **Build email capability (Phase 1)** — (Added session 223, built session 224.) `src/utils/email_client.py` (SMTP send + IMAP read/search/mark_read), `src/tools/email_tool.py` (tool wrapper with logging), 3 action handlers in `action_dispatcher.py`, router intent detection for "email" intent. Credentials in `.env`. **Needs live testing** — see NEXT_SESSION_PROMPT.md.

- [x] **Email: dream-mode approval queue** — (Added session 224, built session 225.) `_handle_send_email` now checks `ctx.source` — dream mode emails go through `request_email_approval()` in discord_bot.py (rich embed + ✅/❌ reactions, 5-min timeout). Chat mode sends immediately. +4 tests.

- [ ] **Search query broadening** — (Added session 188. Untested — needs a query that truly returns 0 results.) `_simplify_query()` auto-retry on 0 results. **File:** `src/core/plan_executor/actions.py`.

- [x] **Dream cycles running 0-task** — (Added session 222, investigated session 225.) Root cause: heavy filtering (dedup + relevance + purpose + memory + staleness + saturation) kills all ideas, then MIN_SUGGEST_SCORE=0.5 rejects survivors. Not a bug — quality filter working as designed. **Fix:** Added `_interest_based_fallback()` in heartbeat — when `suggest_work()` returns empty during proactive initiative, falls back to worldview interests for lightweight research tasks. Prevents fully idle cycles.

- [x] **Morning digest pipeline** — (Added session 226, built session 226.) `news_client.py` (HN API + RSS feeds), `weather_client.py` (wttr.in, no API key), `morning_digest.py` (concurrent fetcher). Injected into morning report via `digest_context` param. On-demand via "give me a digest" Discord command (router intent `digest` → action `morning_digest`). +32 tests. **Needs live testing** after deploy — see NEXT_SESSION_PROMPT.md.

- [x] **Calendar integration (ICS feeds)** — (Added session 227, built session 227.) `calendar_client.py` (ICS feed parser, provider-agnostic — Outlook/Google/Apple). Integrated into morning digest pipeline. Standalone Discord command: "what's on my calendar?" / "any meetings today?" (router intent `calendar` → action `check_calendar`). Config via `ARCHI_CALENDAR_URLS` env var or `calendar_urls` in `archi_identity.yaml`. +30 new tests. **Needs live testing** — Jesse needs to configure an ICS URL.

- [x] **Content creation pipeline (Phase 1)** — (Added session 228, built session 228.) `src/tools/content_creator.py` (~460 lines) — generates blog posts, tweets, tweet threads, Reddit posts via model. Publishers: GitHub Pages (API commits), Twitter/X (Tweepy), Reddit (PRAW). All gracefully degrade if credentials/libraries missing. 3 action handlers (create_content, publish_content, list_content), router intent "content". Content logging to `logs/content_log.jsonl`. +26 tests. **Needs credentials** — see NEXT_SESSION_PROMPT.md.

### Git — Jesse action required

- [ ] **Delete git lock file** — `.git/index.lock` is a 0-byte stale lock blocking all git operations. These are created by Cowork sessions that get interrupted mid-git-operation. **Jesse:** delete both files, then run: `git add -A && git commit -m "Sessions 220-222: notification quality monitoring, exploration saturation fix, goal completion fix, doc updates"`. See `claude/PENDING_DELETIONS.md`.

### Needs live verification (after next deploy — DO NOT re-check every session)

These items can only be verified after Archi restarts with new code deployed. Don't waste session time re-checking them — just verify once after a confirmed deploy.

- [ ] **Email send/receive** — Verify email works end-to-end: send test via Discord "send an email to koorbmeh@gmail.com about testing", check inbox via "check my email". (Session 224.)
- [ ] **Morning digest** — Verify digest in morning report (auto at 6-9 AM) includes weather + calendar + news + email. Test on-demand via "give me a digest". (Sessions 226-227.)
- [ ] **Calendar integration** — Configure `ARCHI_CALENDAR_URLS` in .env, then test via "what's on my calendar?" Discord command. Also verify calendar appears in morning digest. (Session 227.)
- [ ] **Content pipeline** — Configure platform credentials (GitHub PAT + blog repo at minimum), then test: "write a blog post about AI agents" → "publish that to the blog". Also test "what have I published?" for content log. (Session 228.)
- [ ] **Notification quality log fields** — Verify new entries in `logs/notifications.jsonl` have char_count and timestamps. (Sessions 220+ code not yet deployed.)
- [ ] **Exploration saturation** — Verify explorations rotate away from health topics after session 217/221 fixes deploy.
- [ ] **Opinion bootstrapping** — At 1 opinion. Will grow as more tasks complete. No action needed.
- [ ] **Topic saturation detection** — 153 saturated keywords extracted. Verify suggestions diversify after deploy.
- [ ] **Worldview MagicMock contamination** — Guard added in `develop_taste()`. Verify no new garbage entries after deploy.
- [ ] **Various Phase 3-4 systems** — Self-reflection (50 cycles), adaptive retirement (70% ignore rate over 14 days), autonomous scheduling, interest exploration, taste development, personal projects, meta-cognition. All need sustained runtime to verify. Check after Archi has been running for a week+ with current code.

### Low priority / back burner

- [ ] **Test count discrepancy Linux vs Windows** — Linux ~4568, Windows ~1399 (stale count from session 125). Environmental differences, not code issues.
- [ ] **Two-call approach for easy-tier** — Only if personality feels robotic after live testing. (Session 94.)
- [ ] **Protected-file user-directed override mechanism** — On back burner per Jesse. (Session 97.)
- [ ] **Long functions (code quality)** — `_record_task_result()` ~68 lines, `on_message()` 369 lines, `_handle_config_commands()` 161 lines, `execute_task()` ~127 lines, `run_diagnostics()` ~252 lines. All evaluated and deemed acceptable — branching logic that doesn't benefit from further decomposition.

---

## Completed Work (last 10 sessions)

Older completed work archived to `claude/archive/COMPLETED_WORK_SESSIONS_1_96.md`.

**Session 228:** Content creation pipeline (Phase 1) — `content_creator.py` (~460 lines) with model-based content generation (blog/tweet/tweet_thread/reddit formats) + 3 platform publishers (GitHub Pages via API, Twitter via Tweepy, Reddit via PRAW). All publishers gracefully degrade if credentials or libraries missing. 3 new action handlers + router intent "content". Content logging to `logs/content_log.jsonl`. Design doc: `claude/DESIGN_CONTENT_PIPELINE.md`. Added PyGithub, tweepy, praw to requirements.txt. +26 tests, 210 passed (content + dispatcher + router).

**Session 227:** Calendar integration via ICS feeds — `calendar_client.py` (provider-agnostic ICS parser, works with Outlook/Google/Apple). Integrated into morning digest pipeline as 4th concurrent source. Standalone Discord command: "what's on my calendar?" (router intent `calendar` → `check_calendar`). Config via `ARCHI_CALENDAR_URLS` env var or `calendar_urls` in `archi_identity.yaml`. Also added `feedparser` and `icalendar` to requirements.txt. +30 new tests, 42 passed (calendar + updated digest tests).

**Session 226:** Morning digest pipeline — `news_client.py` (Hacker News API + RSS feeds via feedparser), `weather_client.py` (wttr.in, no API key, auto-reads location from identity config), `morning_digest.py` (concurrent fetcher combining email + news + weather). Integrated into morning report via `digest_context` param in `format_morning_report()`. On-demand via "digest"/"briefing" Discord intent → `morning_digest` action handler. +32 tests, 3922 passed (excl pre-existing scheduler failure).

**Session 225:** Dream-mode email approval queue (`request_email_approval()` in discord_bot.py, source check in `_handle_send_email`). Investigated 0-task dream cycles — added `_interest_based_fallback()` to heartbeat for worldview-interest-driven research tasks when suggestion pipeline returns empty. +4 tests, 3900 passed (excl pre-existing scheduler failure).

**Session 224:** Built email capability (Phase 1). `email_client.py` (SMTP send + IMAP read/search), `email_tool.py` (tool wrapper + logging), 3 dispatcher handlers (send_email, check_email, search_email), router intent detection. Safety: rate limiting (20/day), secret content guard. +28 tests, 4597 passed (excl pre-existing scheduler failure).

**Session 223:** Reduced suggestion quantity to ONE (quality over quantity). Added score threshold (0.5) — if no idea is good enough, stay quiet. Wrote email capability design doc (`claude/DESIGN_EMAIL.md`). +2 tests, all existing tests pass.

**Session 222:** Completed goal pruning. `prune_stale_goals()` now removes completed goals older than 7 days. +3 tests.

**Session 221:** Fixed exploration interest saturation filter — now checks combined topic+notes for keyword overlap and propagates to child interests. +3 tests.

**Session 220:** Added notification quality monitoring — all `format_*` functions log to `logs/notifications.jsonl`. +4 tests.

**Session 219:** Added filter-level topic saturation — `_filter_ideas()` rejects ideas with 2+ saturated keywords. +6 tests. Git locks resolved, sessions 216-218 committed.

**Session 218:** Implemented opinion bootstrapping — seeds opinions from task outcomes when <3 exist. +7 tests.

**Session 217:** Post-restart verification confirmed "test" spam resolved. Fixed health-topic feedback loop in exploration. +7 tests.

**Session 216:** Added topic saturation detection to reduce repetitive suggestions. +10 tests.

**Session 215:** Added diagnostic PID logging for "test" spam investigation.

**Session 214:** Verification pass — all items blocked on restart.

**Session 213:** Post-restart verification. Added garbage guard defense-in-depth. Fixed scheduled task payload blocking.
