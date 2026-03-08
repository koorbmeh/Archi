# Archi — Todo List

Last updated: 2026-03-07 (session 239)

---

## Open Items

### Self-extension system (new — session 233)

- [x] **Self-extension Phase 1: Deep Research Agent** — (Added session 233, built session 235.) `src/core/research_agent.py` (ResearchAgent class, multi-round loop with query generation → search → extract → evaluate → follow-up → synthesize). `src/tools/tavily_search.py` (Tavily API wrapper, search + extract). New PlanExecutor action: `research`. New router intent: `research` → `deep_research`. Discord: "Research the best music API". +23 tests. Tavily API key configured.
- [x] **Self-extension Phase 2: Capability Assessor** — (Added session 233, built session 236.) `src/core/capability_assessor.py` (~380 lines) — periodic self-assessment (every 20 dream cycles, Phase 0.97 in heartbeat). Gathers evidence from 6 sources (learning system failures, worldview interests/projects, tool inventory, behavioral avoidance rules, content platform config, stalled goals). Model call identifies concrete capability gaps ranked by impact. Proposes projects to Jesse via Discord when gap impact >= 0.5. 48h cooldown, $0.08 cost cap. Persists to `data/self_extension/assessments.json`. +36 tests.
- [x] **Self-extension Phase 3: Strategic Planner** — (Added session 233, built session 237.) `src/core/strategic_planner.py` (~520 lines) — `StrategicPlanner` with `create_plan()` (escalation model designs solution → writes design doc → persists plan), `activate_plan()` (starts Phase 1 after Jesse approval), `advance_plan()` (checks phase completion, creates goals for next phase). Data classes: `ImplementationPlan`, `PlanPhase`, `PhaseTask`, `PhaseResult`. Heartbeat Phase 0.98 (every 5 cycles). Persistence in `data/self_extension/projects.json`. +48 tests.
- [x] **Self-extension Phase 4: Multi-Cycle Project Execution** — (Added session 233, built session 238.) Extended `Goal` with `project_id` and `project_phase` fields. Goals created from strategic planner phases are tagged with project metadata. When all project-linked goals for a phase complete, `GoalWorkerPool` auto-marks phase tasks done via `mark_phase_task_done()`. `activate_plan()` now returns `PhaseResult` with Phase 1 goal descriptions. Wired capability assessor → strategic planner approval flow: Jesse says "go for it" → plan is created → sent for review → "go for it" again → plan activated with Phase 1 goals. Two-stage approval in `discord_bot.py` via `_pending_plan_activation`. +14 tests.
- [x] **Self-extension Phase 5: Integration & end-to-end testing** — (Added session 233, built session 239.) Failure propagation: `fail_phase()` and `resume_project()` in `StrategicPlanner`. `_check_project_phase_failure()` in `GoalWorkerPool` — when a project-linked goal is stuck (failed/blocked tasks, no pending), propagates failure → phase marked failed → project paused. Paused projects block `advance_plan()`. Jesse can resume via Discord. End-to-end test covers full cycle: plan → activate → goals → complete → mark phase tasks → advance → complete project. +17 tests in `tests/unit/test_self_extension_e2e.py`. Edge cases: duplicate project goals, cross-project dedup, serialization roundtrip.

### Content strategy (new — session 233)

- [x] **Content strategy Phase 1: Brand voice + enhanced generation** — (Added session 233, built session 234.) Created `config/archi_brand.yaml` (brand identity, voice, 5 topic pillars with keywords/angles, content rules, platform-specific styles). Added `get_brand_config()` to `src/utils/config.py`. Updated `generate_content()` to inject brand voice preamble + pillar angles into prompts. Auto-tags content with detected pillar. +18 tests in `tests/unit/test_brand_voice.py`.
- [ ] **Content strategy Phase 2: Visual content pipeline** — (Added session 233.) Integrate existing SDXL into content flow. Platform-specific image templates + text overlays via Pillow. New file: `src/tools/image_generator.py`.
- [ ] **Content strategy Phase 3: Music generation** — (Added session 233.) Integrate Suno/Udio via third-party API or self-hosted suno-api. New file: `src/tools/music_generator.py`. **Needs Jesse to choose API provider and set up credentials.**
- [ ] **Content strategy Phase 4: Content calendar + scheduling** — (Added session 233.) Weekly planning algorithm, content queue, heartbeat integration for auto-publish. New file: `src/tools/content_calendar.py`.
- [ ] **Content strategy Phase 5: Cross-platform adaptation** — (Added session 233.) One blog post → tweet thread + IG carousel + FB post + Reddit post. `adapt_content()` function.

### Code changes needed

- [x] **Reduce suggestion quantity — quality over quantity** — (Added 2026-03-06, fixed session 223.) `suggest_work()` now returns at most 1 idea and only if it clears MIN_SUGGEST_SCORE (0.5). Silence beats noise.

- [x] **Build email capability (Phase 1)** — (Added session 223, built session 224.) `src/utils/email_client.py` (SMTP send + IMAP read/search/mark_read), `src/tools/email_tool.py` (tool wrapper with logging), 3 action handlers in `action_dispatcher.py`, router intent detection for "email" intent. Credentials in `.env`. **Needs live testing** — see NEXT_SESSION_PROMPT.md.

- [x] **Email: dream-mode approval queue** — (Added session 224, built session 225.) `_handle_send_email` now checks `ctx.source` — dream mode emails go through `request_email_approval()` in discord_bot.py (rich embed + ✅/❌ reactions, 5-min timeout). Chat mode sends immediately. +4 tests.

- [ ] **Search query broadening** — (Added session 188. Untested — needs a query that truly returns 0 results.) `_simplify_query()` auto-retry on 0 results. **File:** `src/core/plan_executor/actions.py`.

- [x] **Dream cycles running 0-task** — (Added session 222, investigated session 225.) Root cause: heavy filtering (dedup + relevance + purpose + memory + staleness + saturation) kills all ideas, then MIN_SUGGEST_SCORE=0.5 rejects survivors. Not a bug — quality filter working as designed. **Fix:** Added `_interest_based_fallback()` in heartbeat — when `suggest_work()` returns empty during proactive initiative, falls back to worldview interests for lightweight research tasks. Prevents fully idle cycles.

- [x] **Morning digest pipeline** — (Added session 226, built session 226.) `news_client.py` (HN API + RSS feeds), `weather_client.py` (wttr.in, no API key), `morning_digest.py` (concurrent fetcher). Injected into morning report via `digest_context` param. On-demand via "give me a digest" Discord command (router intent `digest` → action `morning_digest`). +32 tests. **Needs live testing** after deploy — see NEXT_SESSION_PROMPT.md.

- [x] **Calendar integration (ICS feeds)** — (Added session 227, built session 227.) `calendar_client.py` (ICS feed parser, provider-agnostic — Outlook/Google/Apple). Integrated into morning digest pipeline. Standalone Discord command: "what's on my calendar?" / "any meetings today?" (router intent `calendar` → action `check_calendar`). Config via `ARCHI_CALENDAR_URLS` env var or `calendar_urls` in `archi_identity.yaml`. +30 new tests. **Needs live testing** — Jesse needs to configure an ICS URL.

- [x] **Content creation pipeline (Phase 1)** — (Added session 228, built session 228.) `src/tools/content_creator.py` (~460 lines) — generates blog posts, tweets, tweet threads, Reddit posts via model. Publishers: GitHub Pages (API commits), Twitter/X (Tweepy), Reddit (PRAW). All gracefully degrade if credentials/libraries missing. 3 action handlers (create_content, publish_content, list_content), router intent "content". Content logging to `logs/content_log.jsonl`. +26 tests. **Needs credentials** — see NEXT_SESSION_PROMPT.md.

- [x] **YouTube publisher (Phase 2)** — (Added session 229, built session 229.) YouTube Data API v3 via google-api-python-client. OAuth 2.0 with stored refresh token. `publish_to_youtube()` (resumable chunked upload, 10MB chunks, exponential backoff), `update_youtube_metadata()` (patch existing video), `video_script` content format (generates title/description/tags/script with section markers). Auth helpers for one-time setup. +28 tests, 54 passed (YouTube + existing content tests). **Needs credentials** — see NEXT_SESSION_PROMPT.md.

- [x] **Meta Graph API publisher (Phase 3)** — (Added session 230, built session 230.) Facebook Pages (text + photo posts) and Instagram (single image + carousel) via Meta Graph API v22.0. Stdlib-only (no extra library). `publish_to_facebook()`, `publish_to_facebook_photo()`, `publish_to_instagram()`, `publish_to_instagram_carousel()`. Updated action_dispatcher (facebook/instagram as publish targets), router prompt. +17 tests, 43 total content creator tests. **Needs credentials** — see NEXT_SESSION_PROMPT.md.

- [x] **Auto-attachment skips .json deliverables** — (Added 2026-03-07, Jesse-reported. Fixed session 230.) Split `_SKIP_EXT` into `_SKIP_EXT_ALWAYS` (binary: .db, .sqlite, .pyc, .exe, .dll) and `_SKIP_EXT_DREAM` (adds .json, .jsonl). User-requested goals use the smaller set, so .json deliverables attach. **File:** `src/core/goal_worker_pool.py`.

- [x] **send_file handler uses fragile regex instead of FileTracker** — (Added 2026-03-07, Jesse-reported. Fixed session 230.) Added `_filetracker_fuzzy_lookup()` as fallback in `_handle_send_file()`. If regex/LLM path doesn't resolve, queries FileTracker manifest with user message + context. Two fallback points: when no path at all, and when resolved path doesn't exist. **File:** `src/interfaces/action_dispatcher.py`.

- [ ] **Graceful shutdown leaves aiohttp sessions unclosed** — (Added 2026-03-07.) On Ctrl+C, `aiohttp.ClientSession` objects aren't explicitly closed before the event loop shuts down. Python GC tries to log the "Unclosed client session" warning during interpreter teardown, hitting `ImportError: sys.meta_path is None`. Cosmetic but noisy. **Fix:** Explicitly close all aiohttp sessions in the shutdown handler before closing the event loop. **File:** Likely `src/interfaces/discord_bot.py` or wherever the main event loop teardown lives.

- [x] **Dream cycle notifications fire during active user conversations** — (Added 2026-03-07, Jesse-reported. Fixed session 230.) Added `_is_user_recently_active(window_seconds=300)` to Heartbeat. Suppresses phases 5.5 (opinion revisions), 6 (exploration sharing), 6.5 (project updates), and work suggestions when user messaged within 5 min. Task execution and morning reports still proceed. **File:** `src/core/heartbeat.py`.

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

**Session 239:** Self-extension Phase 5 — Integration & End-to-End Testing. Added failure propagation: `StrategicPlanner.fail_phase()` marks a phase as failed and pauses the project, `resume_project()` resets and retries. `GoalWorkerPool._check_project_phase_failure()` detects stuck project-linked goals (failed/blocked tasks, nothing pending) and propagates upward. Paused projects blocked from `advance_plan()`. Comprehensive end-to-end test (`tests/unit/test_self_extension_e2e.py`) covering: full plan→activate→goals→complete→advance→complete cycle, failure propagation from goals to phases, resume after failure, edge cases (duplicate project goals, cross-project dedup, serialization roundtrip, completed goals allow recreation). +17 tests, 4885 passed (162 in planner+goals+e2e suite; excl pre-existing scheduler + async failures).

**Session 238:** Self-extension Phase 4 — Multi-Cycle Project Execution + assessor→planner wiring. Extended `Goal` class with `project_id`/`project_phase` fields (persisted, round-trip serialization). `GoalManager.create_goal()` accepts project metadata. New `get_project_phase_goals()` method. `GoalWorkerPool._check_project_phase_completion()` auto-marks phase tasks done when all project-linked goals for a phase complete. `StrategicPlanner.activate_plan()` now returns `PhaseResult` with Phase 1 goal descriptions (breaking change from `bool` — test updated). Heartbeat Phase 0.98 tags goals with project metadata. Two-stage approval flow in `discord_bot.py`: gap proposal → create plan → plan review → activate plan → Phase 1 goals. Pending proposal tracking in `capability_assessor.py` (`set_pending_proposal`, `get_pending_proposal`, `clear_pending_proposal`). +14 tests, 4868 passed (excl pre-existing scheduler + async failures).

**Session 237:** Self-extension Phase 3 — Strategic Planner. Built `src/core/strategic_planner.py` (~520 lines) with `StrategicPlanner` class: `create_plan()` reads architecture context, uses escalation model (Gemini 3.1 Pro) to design multi-file solutions (new files, modified files, integration points, phased tasks), writes design docs to `claude/DESIGN_*.md`, persists plans to `data/self_extension/projects.json`. `activate_plan()` starts Phase 1 after Jesse approval. `advance_plan()` checks phase completion, advances to next phase, creates goals for new phase tasks. Heartbeat Phase 0.98 (every 5 cycles, offset 3) checks for active projects and auto-advances phases. Data classes: `ImplementationPlan`, `PlanPhase`, `PhaseTask`, `PhaseResult`. Safety: $0.20 cost cap, max 8 phases, one active project at a time, protected files untouched. +48 tests, 4855 passed (excl pre-existing scheduler + async failures).

**Session 236:** Self-extension Phase 2 — Capability Assessor. Built `src/core/capability_assessor.py` (~380 lines) with evidence gathering from 6 sources (learning system failures, worldview interests/stalled projects, tool inventory, behavioral avoidance rules, content platform config, stalled goals), model-based gap analysis, project proposal generation, Discord-friendly formatting. Integrated into heartbeat Phase 0.97 (every 20 cycles, 48h cooldown). Data classes: `CapabilityGap` (name, description, evidence, impact, category, requires_from_jesse) and `ProjectProposal` (title, description, research_needed, estimated_phases, jesse_actions, priority). Persistence in `data/self_extension/assessments.json`. +36 tests, 4091 passed (excl pre-existing scheduler failure).

**Session 235:** Self-extension Phase 1 — Deep Research Agent. Built `src/core/research_agent.py` (~320 lines) with multi-round research loop: generate queries (model) → search (Tavily/DDG) → extract page content (Tavily) → evaluate findings (model: key facts, gaps, sufficient?) → follow-up queries → synthesize (model: conclusion, confidence, recommendation). Built `src/tools/tavily_search.py` (~210 lines) — Tavily API wrapper with search + extract + singleton + graceful DDG fallback. Added `deep_research` action handler to `action_dispatcher.py`, `research` intent to `conversational_router.py`, `research` action to PlanExecutor `_execute_action()`. Updated `.env.example`, `requirements.txt` (tavily-python). +23 tests, 4777 passed (excl pre-existing scheduler failure).

**Session 234:** Content strategy Phase 1 — brand voice + enhanced generation. Created `config/archi_brand.yaml` with Archi's brand identity (tagline, bio), voice profile (tone, perspective, style notes), 5 topic pillars (ai_tech, finance, health_fitness, self_improvement, music) each with keywords and angles, content rules, and platform-specific style adjustments. Added `get_brand_config()` cached accessor to `src/utils/config.py`. Updated `generate_content()` in `content_creator.py` with `_build_brand_context()` (prompt preamble from config), `_detect_pillar()` (keyword-matching auto-tagger), `_pillar_context()` (angle injection). All content now sounds like Archi and gets tagged by pillar. +18 tests, 4034 passed (excl pre-existing scheduler failure).

**Instagram setup session (between 233-234):** Completed full Instagram Business Login API setup with Jesse. Accepted tester invite, generated token via Meta dashboard, discovered IG Business Login returns different user ID (`34733103192939808`). Updated `.env` with working token + correct account ID. Successfully published test post via Archi's code (https://www.instagram.com/p/DVmuAhnjbR2/). Added comprehensive Facebook Pages + Instagram setup guides to `README.md`. Instagram is now fully operational.

**Session 233:** Two major design docs. (1) Content strategy — Jesse decided Archi posts as its own personality ("An AI learning out loud"). `claude/DESIGN_CONTENT_STRATEGY.md`: brand voice, Suno music gen, Flux images, content calendar, cross-platform adaptation, monetization. Decided: Suno for music, Flux for images (text rendering), daily content, approval-first. (2) Self-extension system — `claude/DESIGN_SELF_EXTENSION.md`: the roadmap for Archi to extend his own capabilities (deep research agent, capability assessor, strategic planner, multi-cycle projects). Tavily API for research. No code changes — pure design session.

**Session 232:** Instagram Business Login API support — old `instagram_basic`/`instagram_content_publish` permissions deprecated Jan 2025. Added dual-token architecture: `META_INSTAGRAM_ACCESS_TOKEN` (preferred, uses `graph.instagram.com`) with fallback to Page token (legacy `graph.facebook.com`). Updated `_get_meta_config()`, `publish_to_instagram()`, `publish_to_instagram_carousel()`, `_meta_graph_post()`, `_meta_graph_get()` with `base_url` kwarg. Added @archistroic as Instagram Tester (pending acceptance). Updated `.env.example` with full IG Business Login setup instructions. 43 content creator tests pass.

**Session 230:** Meta Graph API publisher + 3 Jesse-reported bug fixes. Facebook Pages (text + photo posts) and Instagram (single image + carousel) via Graph API v22.0, stdlib-only. Fixed: .json auto-attachment for user-requested goals, send_file FileTracker fallback for fuzzy matching, dream cycle notification suppression when user active within 5 min. Updated action_dispatcher, router prompt, .env.example. +18 tests, 4736 passed.

**Session 229:** YouTube publisher + video_script format — extended `content_creator.py` with YouTube Data API v3 integration (OAuth 2.0 with stored refresh token). `publish_to_youtube()` with resumable chunked upload (10 MB, exponential backoff on 5xx), `update_youtube_metadata()` for patching existing videos, `_parse_video_script()` parser, `video_script` content format (generates title/description/tags/script with section markers). Auth helpers for one-time setup (`generate_youtube_auth_url()`, `exchange_youtube_auth_code()`). Updated action_dispatcher to support YouTube as publish target, router prompt updated for video_script format + YouTube platform. Added google-api-python-client, google-auth-oauthlib, google-auth-httplib2 to requirements.txt + .env.example. +28 tests, 54 passed (YouTube + existing content tests).

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
