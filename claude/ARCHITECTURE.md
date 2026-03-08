# Archi Architecture Map

Reference for understanding and modifying Archi's codebase. Updated 2026-03-06 (session 224).
For the original evolution spec, see `claude/archive/ARCHITECTURE_PROPOSAL.md`.
For a human-developer-facing guide, see `docs/ARCHITECTURE.md`.

---

## System Overview

Archi is an autonomous AI agent running on Windows, communicating via Discord. **API-only architecture:** Grok 4.1 Fast (Reasoning) via xAI direct for all reasoning, Claude Haiku 4.5 for computer use tasks, local SDXL for image generation. Discord is the sole interface. Two modes: **chat mode** (single-shot responses) and **dream mode** (autonomous background work when idle 15+ min).

## Directory Layout

```
Archi/
├── config/
│   ├── archi_identity.yaml    # Static identity (name, role, timezone, working hours)
│   ├── archi_brand.yaml       # Brand voice, topic pillars, content rules (session 234)
│   ├── personality.yaml       # Personality framework (voice, values, humor, philosophical DNA)
│   ├── heartbeat.yaml         # Heartbeat interval config
│   ├── prime_directive.txt    # Core operational guidelines (references personality.yaml)
│   └── rules.yaml             # Safety: budgets, protected files, blocked commands, risk levels
├── src/
│   ├── core/
│   │   ├── agent_loop.py      # Backward-compat shim
│   │   ├── autonomous_executor.py  # Parallel wave task execution + follow-up extraction
│   │   ├── idea_generator.py  # Work suggestions, goal hygiene, scanner integration
│   │   ├── opportunity_scanner.py  # Structured work discovery from project files
│   │   ├── research_agent.py  # Deep multi-round web research with synthesis (session 235)
│   │   ├── capability_assessor.py  # Self-assessment: identify capability gaps (session 236)
│   │   ├── strategic_planner.py   # Multi-phase implementation plans (session 237)
│   │   ├── reporting.py       # Morning report + hourly summaries
│   │   ├── notification_formatter.py  # Model-based conversational message generation
│   │   ├── discovery.py       # Project context scanning before goal decomposition
│   │   ├── goal_manager.py    # Goal/task CRUD, Architect decomposition, state persistence
│   │   ├── plan_executor/     # Multi-step task execution package
│   │   │   ├── executor.py    # Core loop, prompt building, verification
│   │   │   ├── actions.py     # Action handlers (web_search, create_file, etc.)
│   │   │   ├── safety.py      # Path resolution, protected files, approval, error classification
│   │   │   ├── recovery.py    # Crash recovery + task cancellation
│   │   │   └── web.py         # SSL context, URL fetching, SSRF guard
│   │   ├── output_schemas.py  # Schema validation for PlanExecutor actions
│   │   ├── qa_evaluator.py    # Post-task + post-goal quality gate
│   │   ├── integrator.py      # Cross-task synthesis + glue detection
│   │   ├── critic.py          # Adversarial evaluation + User Model preferences
│   │   ├── heartbeat.py       # Background loop (emergency stop, throttle, dream cycles)
│   │   ├── safety_controller.py  # Action authorization by risk level
│   │   ├── learning_system.py # Experience recording, pattern extraction, skill tracking
│   │   ├── skill_system.py    # SkillRegistry singleton — load, validate, execute skills
│   │   ├── skill_validator.py # AST-based safety checks for skill code
│   │   ├── skill_creator.py   # Skill creation from user request or pattern detection
│   │   ├── skill_suggestions.py # Dream-cycle pattern detection for auto-suggesting skills
│   │   ├── scheduler.py         # Scheduled task system (cron-based, session 196)
│   │   ├── journal.py           # Daily journal + self-reflection (sessions 197-199)
│   │   ├── worldview.py         # Evolving opinions, preferences, interests (session 199)
│   │   ├── behavioral_rules.py  # Avoidance/preference rules from experience (session 200)
│   │   ├── conversational_router.py  # Single model call per message (intent + easy answer)
│   │   ├── user_model.py      # Structured store (facts, preferences, corrections, patterns, style, suggestion_style, output_format)
│   │   ├── user_preferences.py   # Legacy preference extraction (pre-Phase 4)
│   │   ├── interesting_findings.py  # Queue notable research for user delivery
│   │   ├── file_tracker.py    # Workspace file tracking (goal→file mapping)
│   │   ├── logger.py          # Logging configuration
│   │   └── resilience.py      # Circuit breakers and retry logic
│   ├── interfaces/
│   │   ├── message_handler.py  # Entry point: pre-process → classify → dispatch → respond
│   │   ├── intent_classifier.py # Fast-paths (datetime/commands/greeting) + model intent
│   │   ├── action_dispatcher.py # 22 action handlers (incl. 4 schedule, 3 email, morning_digest, check_calendar, 3 content)
│   │   ├── response_builder.py  # Trace logging, response assembly
│   │   ├── discord_bot.py       # Discord DM interface, notifications, heartbeat commands
│   │   ├── chat_history.py      # Multi-turn conversation history (thread-safe, atomic writes)
│   │   └── voice_interface.py   # Text-to-speech via Piper
│   ├── models/
│   │   ├── router.py          # Multi-provider routing, model switching
│   │   ├── fallback.py        # Provider fallback chain with circuit breakers
│   │   ├── openrouter_client.py  # Universal LLM client (any OpenAI-compatible provider)
│   │   ├── providers.py       # Provider registry, model aliases, pricing
│   │   └── cache.py           # Query cache (dedup identical prompts)
│   ├── tools/
│   │   ├── tool_registry.py   # MCP-aware tool dispatch, lazy-init singleton
│   │   ├── mcp_client.py      # MCP client lifecycle
│   │   ├── local_mcp_server.py # Built-in tools as local MCP server
│   │   ├── image_gen.py       # SDXL local image generation (direct-only)
│   │   ├── desktop_control.py # pyautogui (lazy-init)
│   │   ├── browser_control.py # Playwright (lazy-init)
│   │   ├── computer_use.py    # UI task orchestrator
│   │   ├── image_analyzer.py  # Vision API service
│   │   ├── web_search_tool.py # DuckDuckGo web search
│   │   ├── tavily_search.py   # Tavily API wrapper — search + extract (session 235)
│   │   ├── ui_memory.py       # UI element position cache
│   │   ├── email_tool.py      # Email send/check/search tool (session 224)
│   │   └── content_creator.py # Content generation + multi-platform publishing (session 228)
│   ├── memory/
│   │   ├── memory_manager.py  # 3-tier: short-term (deque), working (SQLite), long-term (LanceDB)
│   │   └── vector_store.py    # LanceDB vector storage (IVF-PQ at 10K+ rows)
│   ├── monitoring/
│   │   ├── system_monitor.py, cost_tracker.py, health_check.py, performance_monitor.py
│   │   ├── morning_digest.py   # Morning briefing: email + news + weather (session 226)
│   ├── utils/
│   │   ├── paths.py, config.py (get_user_name, get_identity, get_monitoring, get_email_config, etc.), fast_paths.py (shared fast-path patterns), git_safety.py, net_safety.py, text_cleaning.py, parsing.py, project_context.py, project_sync.py, email_client.py (SMTP/IMAP, session 224), news_client.py (HN + RSS, session 226), weather_client.py (wttr.in, session 226), calendar_client.py (ICS feeds, session 227)
│   ├── maintenance/
│   │   └── timestamps.py
│   └── service/
│       └── archi_service.py   # Production service wrapper
├── config/
│   └── skills.yaml            # Skill system config (enabled, blocked imports, timeouts)
├── data/                       # Runtime state (goals_state.json, dream_log.jsonl, user_preferences.json, cost_usage.json, etc.)
│   ├── journal/               # Daily journal files (YYYY-MM-DD.json, session 197)
│   └── skills/                # Self-extending skill modules (data/skills/<name>/skill.py + SKILL.json)
├── workspace/                  # User-facing output
├── logs/                       # conversations.jsonl, chat_trace.log, actions/, llm_debug/
├── scripts/                    # install.py, start.py, fix.py, stop.py, reset.py, profile_setup.py, _common.py, .bat launchers
├── claude/                     # Claude session docs (this directory)
└── tests/                      # unit/ and integration/
```

---

## Execution Flows

### Chat Mode (Discord Message)

```
User message → discord_bot.on_message()
  ├─ Discord-level fast-paths (approve, switch model, set dream cycle, etc.)
  ├─ Build ContextState
  ├─ conversational_router.route() — SINGLE MODEL CALL:
  │   ├─ Local fast-paths ($0.00): /commands, datetime, screenshot, image gen, deferred
  │   └─ Router model → JSON {intent, tier, answer, complexity}
  │       Classifies intent, determines tier (easy/complex), extracts user signals
  ├─ Dispatch: easy tier → send directly; complex tier → message_handler → PlanExecutor
  │   └─ Post-PE: _record_chat_task_reflection() → worldview + taste + behavioral rules (session 209)
  └─ Post: send Discord reply, persist chat history
```

Key files: `conversational_router.py` (~770 lines, temp 0.35, max_tokens 650, includes `/skill` command), `message_handler.py` (~450 lines, includes in-flight dedup), `intent_classifier.py` (~360 lines), `action_dispatcher.py` (~600 lines, 13 handlers including `create_skill`, send_file extracts paths from reply context). Shared fast-path patterns (datetime, screenshot, image gen, cost queries) live in `src/utils/fast_paths.py` (~200 lines).

### Dream Mode (Autonomous Background Work)

```
_monitor_loop() [polls every 5s]
  → is_idle() [default 900s / 15 min]
    → _run_cycle()
       ├─ Morning report (6-9 AM, once/day)
       ├─ Has pending work? → execute tasks via parallel wave execution
       │   (caps: 120 min, $0.50/cycle, 50 tasks, 3 concurrent per wave)
       ├─ No work? → suggest_work() via opportunity scanner, or conversation starter
       ├─ Learning review (if ≥5 experiences)
       ├─ Synthesis (every 10th cycle, informational only)
       └─ File cleanup (every 10th cycle, offset by 5)
```

### Quality Pipeline (post-task)

Per-task: deterministic checks → semantic model eval → accept/reject/fail. On reject: retry once, auto-escalate to Gemini 3.1 Pro.
Per-goal: Integrator (cross-task fit) → Goal QA (conformance) → Critic (adversarial + User Model prefs).
Files: `qa_evaluator.py`, `integrator.py`, `critic.py`.

---

## Model Routing

Default: Grok 4.1 Fast via xAI direct. Escalation: Gemini 3.1 Pro Preview via OpenRouter (QA rejection retries + schema retry exhaustion). Computer use: Claude Haiku 4.5.

**Fallback chain:** xai → openrouter → deepseek → openai → anthropic → mistral (only providers with API keys active). Per-provider circuit breakers (3 failures → OPEN, exponential recovery).

**Runtime switching:** Users switch models via Discord (`"switch to grok"`, `"use claude direct for this task"`, `"switch to auto"`). `escalate_for_task()` context manager snapshots/restores model state.

Files: `router.py`, `fallback.py`, `providers.py`, `openrouter_client.py`.

---

## Scheduled Task System (session 196)

Gives Archi time-awareness. Cron-based recurring tasks persisted in `data/scheduled_tasks.json`, checked every heartbeat tick (~5s). Supports `notify` (Discord DM) and `create_goal` action types. Respects quiet hours (11 PM–6 AM) and fire rate limits (10/hour, 50 tasks max).

Conversational scheduling: Router classifies intent as `"schedule"` → dispatcher handles CRUD (`create_schedule`, `modify_schedule`, `remove_schedule`, `list_schedule`). Slash commands: `/schedule`, `/reminders`. Natural language: "Remind me to stretch every day at 4:15". User-facing times formatted via `format_friendly_time()` (session 207) — "4:20 PM today" instead of ISO.

Engagement tracking (session 198): `_fire_scheduled_task()` records notify task_id + timestamp in `_pending_ack_tasks`. `acknowledge_recent_tasks()` (called from `discord_bot.on_message()`) marks within-window tasks as acknowledged. `_check_engagement_timeouts()` (every tick) marks expired tasks as ignored. 30-minute acknowledgment window. Stats: `times_fired`, `times_acknowledged`, `times_ignored`. Retirement logic: `get_ignored_tasks()` finds tasks with >70% ignore rate over 14+ days.

Files: `scheduler.py` (core), `heartbeat.py` (`_check_scheduled_tasks()`, `_check_engagement_timeouts()`, `acknowledge_recent_tasks()`), `discord_bot.py` (ack call in `on_message()`), `action_dispatcher.py` (4 handlers), `conversational_router.py` (intent + slash commands). Design doc: `claude/DESIGN_SCHEDULED_TASKS.md`.

---

## Daily Journal System (session 197)

Gives Archi continuity of experience. Each day gets a `data/journal/YYYY-MM-DD.json` file with timestamped entries and aggregate counters. Not shown to Jesse unless asked — it's for Archi's internal context.

Entry types: `task_completed`, `conversation`, `observation`, `thing_learned`, `dream_cycle`, `mood_signal`, `reflection`. Each entry has a timestamp, type, content, and optional metadata.

Integration points: `autonomous_executor._record_task_result()` logs task completions, `message_handler.process_message()` logs conversations, `heartbeat._run_cycle()` logs dream cycles. Pruning (30-day retention) runs alongside heartbeat's periodic file cleanup.

Query API: `get_recent_entries(days, type)`, `get_day_summary(day)`, `get_orientation(days)` for morning context. Morning orientation integration (session 198): `reporting.send_morning_report()` calls `get_orientation(days=3)` and passes journal context to `notification_formatter.format_morning_report()`, which injects it into the prompt so Archi can reference yesterday's context in morning messages.

File: `journal.py` (~360 lines). Integration: `reporting.py`, `notification_formatter.py`. Design doc: `claude/DESIGN_BECOMING_SOMEONE.md` (Phase 1b).

Self-reflection (session 199): `generate_self_reflection(router, days=7)` analyzes recent journal entries via model call, stores reflection as journal entry, and calls `worldview._update_worldview_from_reflection()` to extract new opinions/interests. Triggered every 50 dream cycles (heartbeat Phase 5). Simple fallback without model call produces pattern-based summary.

---

## Worldview System (session 199)

Gives Archi evolving opinions, preferences, and interests derived from actual experience. Unlike `personality.yaml` (static), worldview changes over time. Data in `data/worldview.json`.

Three categories: **opinions** (topic + position + confidence + basis + history), **preferences** (domain + preference + strength + evidence_count), **interests** (topic + curiosity_level + notes + last_explored).

Pruning: opinions below 0.15 confidence removed on save. Opinions not updated in 30 days decay by 0.05/cycle. Interests not explored in 30 days decay by 0.15/cycle. Caps: 50 opinions, 50 preferences, 30 interests.

Integration points: `conversational_router.py` injects `get_worldview_context()` into system prompt. `autonomous_executor._record_task_result()` calls `reflect_on_task()` (lightweight, no model call) after each task and injects `model_used` from router for taste tracking. `heartbeat._run_cycle()` triggers decay prune every 10 cycles (alongside journal prune) and weekly self-reflection (every 50 cycles) which updates worldview via model.

**Bootstrap** (sessions 208, 218): `_lightweight_reflection()` seeds interests from task domains via `_extract_interest_topic()` when fewer than 3 interests exist, and seeds opinions via `_extract_seed_opinion()` when fewer than 3 opinions exist. Opinion seeds map task types (research, writing, coding, analysis, image) to success/failure position variants at low confidence (0.35). This bootstraps the worldview so exploration/self-reflection can build on initial seeds. `develop_taste()` also tracks unverified efficient tasks (strength 0.3) and model performance from router info.

File: `worldview.py` (~580 lines). Design doc: `claude/DESIGN_BECOMING_SOMEONE.md` (Phase 2).

---

## Behavioral Rules System (session 200)

Gives Archi habits of action derived from repeated task outcomes. Unlike worldview (opinions), behavioral rules change what Archi *does*. After 3+ similar failures → avoidance rule. After 3+ similar successes → preference rule. Data in `data/behavioral_rules.json`.

Two categories: **avoidance** (pattern + reason + keywords + strength + evidence_count) and **preference** (same schema). Keyword-based matching against task/goal descriptions.

Pruning: rules not reinforced in 30 days decay by 0.05/cycle. Rules below 0.15 strength pruned. Total cap: 80 rules.

Integration: `autonomous_executor._gather_execution_hints()` calls `get_relevant_rules()` to inject behavioral hints. `autonomous_executor._record_task_result()` calls `process_task_outcome()` for post-task reinforcement. `heartbeat._run_cycle()` Phase 2.1 calls `extract_rules_from_experiences()` on recent learning data. Decay prune runs every 10 cycles alongside worldview/journal prune.

File: `behavioral_rules.py` (~410 lines). Design doc: `claude/DESIGN_BECOMING_SOMEONE.md` (Phase 2, section 3).

---

## Social/Emotional Awareness (session 201)

Gives Archi the ability to "read the room" — detect the user's mood from message tone and adjust behavior accordingly. Also enables proactive communication when Archi changes its mind about something.

**Tone detection:** The Router extracts a `mood_signal` field per message (busy, frustrated, excited, engaged, tired, playful, neutral). Stored in `UserModel._mood_history` (in-memory, last 10 signals, 1-hour decay). `get_mood_context()` returns behavioral adjustment instructions injected into the router prompt and notification formatter.

**Opinion revision ("I changed my mind"):** When `worldview.add_opinion()` detects a significant position change (different text + confidence delta >= 0.3 or new_confidence >= 0.6), it flags a `pending_revision` in `data/worldview.json`. Heartbeat Phase 5.5 delivers up to 2 revisions per cycle via `format_opinion_revision()` in `notification_formatter.py`, then clears them. Revisions include old/new position and confidence for context.

Files: `conversational_router.py` (mood_signal extraction), `user_model.py` (mood tracking + context), `notification_formatter.py` (opinion revision formatting + mood injection), `worldview.py` (revision detection + storage), `heartbeat.py` (Phase 5.5 delivery). Design doc: `claude/DESIGN_BECOMING_SOMEONE.md` (Phase 3, sections 6-7).

---

## Interest-Driven Exploration (session 202)

Gives Archi curiosity — ~20% of dream cycles are spent exploring topics Archi is interested in rather than doing productive work. Picks the highest-curiosity interest from the worldview system, researches via model call, and shares findings with personality.

**Exploration flow:** `idea_generator.explore_interest(router)` → picks top interest from `worldview.get_interests()` → model call to explore topic → updates `last_explored` → logs to journal as `exploration` entry → seeds related interests from `connects_to` → returns finding if interesting.

**Heartbeat integration:** Phase 6, every 5th cycle (offset 2). If exploration produces interesting findings, `format_exploration_sharing()` formats a personality-rich message and sends via Discord.

Files: `idea_generator.py` (`explore_interest()`), `heartbeat.py` (Phase 6), `notification_formatter.py` (`format_exploration_sharing()`). Design doc: `claude/DESIGN_BECOMING_SOMEONE.md` (Phase 4, section 4).

---

## Aesthetic/Taste Development (session 202)

Archi develops preferences about what works and what doesn't, informed by actual task performance data. Unlike behavioral rules (avoid/prefer actions), taste is about quality and efficiency patterns.

**Taste tracking:** `worldview.develop_taste()` analyzes each completed task's success, cost, step count, model used, and verification status. Classifies task type (research/writing/coding/analysis) and records preferences in three domains: `taste_efficiency` (what works well), `taste_caution` (expensive failure patterns), `taste_model` (which model handles which task type).

**Integration:** Called from `autonomous_executor._record_task_result()` after every task. `get_taste_context()` builds a compact summary injected into `_gather_execution_hints()` so future tasks benefit from learned preferences.

Files: `worldview.py` (`develop_taste()`, `get_taste_context()`), `autonomous_executor.py` (post-task recording + hint injection). Design doc: `claude/DESIGN_BECOMING_SOMEONE.md` (Phase 4, section 9).

---

## Long-Term Personal Projects (session 203)

Archi pursues self-directed work emerging from high-curiosity interests. Projects are persistent, multi-session efforts with progress tracking.

**Data:** Stored in `data/worldview.json` under `personal_projects` key. Each project has title, origin_interest, description, status (active/paused/completed), progress_notes (bounded to 15), work_sessions count, shared_with_user flag.

**Project lifecycle:** Interest explored 2+ times with curiosity >= 0.5 → `propose_personal_project()` (model call to decide if sustained work is warranted) → active project → `work_on_personal_project()` picks most-neglected project, makes progress → share-worthy results sent to user via `format_project_sharing()` → project completes when model determines it's stalled or finished.

**Heartbeat integration:** Phase 6.5 (every 10th cycle, offset 4). If no active projects, proposes new one; otherwise works on existing. Cap: 10 projects.

Files: `worldview.py` (project CRUD + context), `idea_generator.py` (`propose_personal_project()`, `work_on_personal_project()`), `notification_formatter.py` (`format_project_sharing()`), `heartbeat.py` (Phase 6.5). Design doc: `claude/DESIGN_BECOMING_SOMEONE.md` (Phase 4, section 10).

---

## Meta-Cognition (session 203)

Archi thinks about his own thinking — notices patterns in how he approaches tasks and adjusts behavior accordingly.

**Data:** Stored in `data/worldview.json` under `meta_observations` key. Each observation has pattern, category (estimation/approach/communication/efficiency/general), evidence, times_observed, adjustment. Duplicate patterns are reinforced rather than duplicated. Cap: 20 observations.

**Integration:** Generated during weekly self-reflection (every 50 cycles, alongside existing self-reflection). `generate_meta_cognition()` gathers evidence from behavioral rules, taste preferences, journal entries, and existing observations, then uses model to identify meta-patterns and propose adjustments. `get_meta_context()` injects into both router system prompt and PlanExecutor execution hints.

Files: `worldview.py` (observation CRUD + context), `idea_generator.py` (`generate_meta_cognition()`), `conversational_router.py` (prompt injection), `autonomous_executor.py` (hint injection), `heartbeat.py` (Phase 5). Design doc: `claude/DESIGN_BECOMING_SOMEONE.md` (Phase 4, section 11).

---

## Adaptive Retirement & Autonomous Scheduling (session 199)

**Adaptive retirement:** `idea_generator.check_retirement_candidates()` queries `scheduler.get_ignored_tasks()` (>70% ignore rate over 14+ days). Archi-created tasks disabled silently; user-created tasks proposed for retirement via Discord. Runs every 10 dream cycles (heartbeat Phase 0.95).

**Autonomous scheduling:** `idea_generator.suggest_scheduled_tasks(router)` gathers evidence from journal + conversation logs, uses model to detect recurring patterns, proposes schedules. Notify tasks proposed to user; create_goal tasks created silently. Once-per-day cooldown. Runs every 10 dream cycles offset by 7 (heartbeat Phase 2.7).

Files: `idea_generator.py` (retirement + scheduling functions), `heartbeat.py` (integration). Design doc: `claude/DESIGN_SCHEDULED_TASKS.md`.

---

## Email System (session 224)

Archi can send and receive email via `ArchiRex@outlook.com` using Outlook SMTP/IMAP with App Password auth. Python stdlib only (smtplib, imaplib).

**Architecture:** `src/utils/email_client.py` (low-level SMTP send + IMAP read/search/mark_read) → `src/tools/email_tool.py` (tool wrapper with lazy init, logging to `logs/email_log.jsonl`) → `action_dispatcher.py` (3 handlers: send_email, check_email, search_email) → `conversational_router.py` (intent "email").

**Config:** `ARCHI_EMAIL_ADDRESS` and `ARCHI_EMAIL_APP_PASSWORD` in `.env`. `get_email_config()` in `config.py`.

**Safety:** Rate limit 20 sends/day. Content guard blocks emails containing API keys, passwords, or tokens. `send_email` is classified as L4_CRITICAL in `rules.yaml` (manual_execute_only). All activity logged to `logs/email_log.jsonl`. **Dream-mode approval queue** (session 225): `_handle_send_email` checks `ctx.source` — when `source == "dream_cycle_queue"`, calls `request_email_approval()` in `discord_bot.py` which sends a rich embed with email details (to, subject, body preview) and ✅/❌ reactions. 5-minute timeout, auto-cancels if no response. Chat-mode emails send immediately without approval.

**Router integration:** "check my email" / "any new emails" → intent `email`, action `check_email`. "Send an email to X about Y" → action `send_email`. "Search emails from X" → action `search_email`.

**Phase 2 (future):** Microsoft Graph API for folders, drafts, advanced search, calendar.

Files: `email_client.py` (~200 lines), `email_tool.py` (~120 lines). Design doc: `claude/DESIGN_EMAIL.md`.

---

## Morning Digest Pipeline (sessions 226–227)

Combines weather, calendar events, email inbox summary, and news headlines into a single morning briefing. Integrated into the existing morning report and available on-demand via Discord ("give me a digest", "morning briefing").

**Architecture:** `src/utils/news_client.py` (Hacker News API + RSS feeds) + `src/utils/weather_client.py` (wttr.in, no API key) + `src/utils/calendar_client.py` (ICS feed parser) → `src/core/morning_digest.py` (concurrent fetcher, combines all sources) → injected into `reporting.send_morning_report()` → `notification_formatter.format_morning_report()` with `digest_context` param.

**News sources:** Hacker News top stories (Firebase API, no auth), RSS feeds (BBC Tech, NYT Tech, r/technology) via feedparser. All fetched concurrently.

**Weather:** wttr.in JSON API, no API key required. Location from `archi_identity.yaml` (`user_context.location`). Returns current conditions + 2-day forecast.

**Calendar (session 227):** ICS feed parser — provider-agnostic, works with Outlook, Google Calendar, Apple Calendar, or any service that publishes ICS URLs. No API keys or OAuth required. Configure URLs via `ARCHI_CALENDAR_URLS` env var (comma-separated) or `user_context.calendar_urls` in `archi_identity.yaml`. Fetches events, filters to 2-day window, formats as compact summary. Standalone intent: `"calendar"` → action `check_calendar`. Phase 2 (future): Microsoft Graph API for read-write access.

**On-demand:** Router intent `"digest"` → action `morning_digest` → `_handle_morning_digest()` in action_dispatcher. Jesse can say "give me a digest" or "morning briefing" anytime. Router intent `"calendar"` → action `check_calendar` for standalone calendar queries ("what's on my calendar?", "any meetings today?").

**Resilience:** All sources are best-effort — if one fails, others still work. Concurrent fetching with 20s timeout. Empty results gracefully omitted.

Files: `news_client.py` (~130 lines), `weather_client.py` (~100 lines), `calendar_client.py` (~230 lines), `morning_digest.py` (~110 lines).

---

## Content Creation Pipeline (session 228)

Archi generates and publishes content across platforms — blog posts, tweets, Reddit posts, YouTube videos. Model generates content in the requested format (including `video_script` for YouTube), then platform-specific publishers handle posting.

**Architecture:** `src/tools/content_creator.py` — single module with content generation (model call for blog/tweet/tweet_thread/reddit/video_script formats) + brand voice injection from `config/archi_brand.yaml` + auto pillar tagging + platform publishers (GitHub Pages via API, Twitter via Tweepy, Reddit via PRAW, YouTube via Data API v3) + content logging (`logs/content_log.jsonl`).

**Router integration:** Intent `"content"` → actions `create_content` (generate draft), `publish_content` (post to platform), `list_content` (show log).

**Platforms:** GitHub Pages blog (commits Jekyll markdown posts via GitHub API, needs PAT), Twitter/X (free tier write-only via Tweepy, ~500 tweets/month), Reddit (free via PRAW), YouTube (OAuth 2.0 via google-api-python-client, resumable uploads with retry), Facebook Pages (Meta Graph API, stdlib-only), Instagram (Meta Graph API, business accounts). All publishers gracefully degrade if credentials or libraries are missing.

**YouTube specifics (session 229):** OAuth 2.0 with stored refresh token — no interactive browser flow needed at runtime. `publish_to_youtube()` handles video upload with resumable chunked upload (10 MB chunks, exponential backoff on 5xx errors). `update_youtube_metadata()` updates title/description/tags/privacy on existing videos. Auth helpers (`generate_youtube_auth_url()`, `exchange_youtube_auth_code()`) for one-time setup. Default privacy: private.

**Meta Graph API specifics (session 230):** Facebook Pages + Instagram via Graph API v22.0, stdlib-only (no extra library). `publish_to_facebook()` for text + link posts, `publish_to_facebook_photo()` for photo posts. Instagram uses container-based publishing: `publish_to_instagram()` creates container → polls status → publishes; `publish_to_instagram_carousel()` creates child containers → carousel container → publishes (2-10 images). Instagram requires JPEG images at public URLs, limited to 25 API-published posts per 24 hours.

**Safety:** Dream-mode publishing goes through Discord approval. Rate limits per platform tracked in content log.

**Config:** `GITHUB_PAT`, `GITHUB_BLOG_REPO`, `TWITTER_API_KEY/SECRET`, `TWITTER_ACCESS_TOKEN/SECRET`, `REDDIT_CLIENT_ID/SECRET/USERNAME/PASSWORD`, `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`, `META_PAGE_ACCESS_TOKEN`, `META_PAGE_ID`, `META_INSTAGRAM_ACCOUNT_ID` in `.env`.

**Brand voice (session 234):** `config/archi_brand.yaml` defines Archi's persona (tagline, bio), voice (tone, perspective, style notes), 5 topic pillars (ai_tech, finance, health_fitness, self_improvement, music) with keywords and angles, content rules, and platform-specific style adjustments. `_build_brand_context()` constructs a preamble injected into every generation prompt. `_detect_pillar()` auto-tags content by matching topic keywords against pillar keywords. `_pillar_context()` injects relevant angles. Config loaded via `get_brand_config()` in `src/utils/config.py`.

**Phase 4 (future):** Dream cycle auto-content from exploration/findings, content calendar, cross-platform adaptation (one topic → multiple formats), Rumble, analytics.

Files: `content_creator.py` (~1150 lines), `config/archi_brand.yaml`. Design doc: `claude/DESIGN_CONTENT_STRATEGY.md`, `claude/DESIGN_CONTENT_PIPELINE.md`.

---

## Deep Research Agent (session 235)

Multi-round web research with synthesis — upgrades Archi from "search and get snippets" to "research a topic thoroughly." Uses Tavily API for search + content extraction, with DuckDuckGo fallback. This is Self-Extension Phase 1 — the foundation for Archi to evaluate options, compare APIs, and make informed technical decisions.

**Architecture:** `src/tools/tavily_search.py` (Tavily API wrapper, singleton, search + extract) → `src/core/research_agent.py` (ResearchAgent class, multi-round loop) → `action_dispatcher.py` (handler `deep_research`) → `conversational_router.py` (intent `research`).

**Research flow:** Generate queries (model) → Search (Tavily/DDG) → Extract page content (Tavily) → Evaluate findings (model: key facts, gaps, sufficient?) → If gaps remain, generate follow-up queries → loop → Synthesize (model: conclusion, confidence, recommendation).

**Interfaces:** Discord: "Research the best music generation API" (router intent `research` → `deep_research`). PlanExecutor: `research` action within tasks/goals (for programmatic use by future self-extension components). Also `compare_options()` for structured option comparison.

**Output:** `ResearchResult` dataclass with conclusion, key_findings, sources (with relevance scores), confidence (0-1), gaps, recommendation, cost. Serializable via `to_dict()`, user-friendly via `format_for_user()`.

**Safety:** Cost cap $0.15/research, max 5 rounds, max 10 sources. Tavily graceful degradation to DDG. All model calls via router (budget-tracked).

**Config:** `TAVILY_API_KEY` in `.env`. Free tier: 1,000 searches/month.

Files: `research_agent.py` (~320 lines), `tavily_search.py` (~210 lines). Design doc: `claude/DESIGN_SELF_EXTENSION.md`.

---

## Capability Assessor (session 236)

Self-Extension Phase 2 — periodic self-assessment that identifies what Archi can't do but wants to. Gathers evidence from 6 sources, uses model to identify concrete capability gaps ranked by impact, and proposes projects to Jesse via Discord.

**Architecture:** `src/core/capability_assessor.py` (evidence gathering + model-based gap analysis + project proposals) → `heartbeat.py` (Phase 0.97, every 20 cycles).

**Evidence sources:** Learning system (failed tasks), worldview (high-curiosity interests, stalled projects), tool registry (available tools), behavioral rules (avoidance patterns), content capabilities (configured/unconfigured platforms), goals (repeatedly failed).

**Flow:** `is_assessment_due()` (48h cooldown) → `gather_all_evidence()` → model call with `_ASSESS_PROMPT` → `_parse_gaps()` → ranked `CapabilityGap` list → if top gap impact >= 0.5, `propose_project()` → `format_gap_message()` → Discord notification to Jesse.

**Output:** `CapabilityGap` (name, description, evidence, impact 0-1, category, requires_from_jesse). `ProjectProposal` (title, description, research_needed, estimated_phases, jesse_actions, priority). Persisted in `data/self_extension/assessments.json`.

**Safety:** Cost cap $0.08/assessment. 48-hour cooldown between assessments. Max 5 gaps per run. Suppressed when user recently active. Jesse approval required before any project starts.

Files: `capability_assessor.py` (~380 lines). Design doc: `claude/DESIGN_SELF_EXTENSION.md` (Component 2).

---

## Strategic Planner (session 237)

Self-Extension Phase 3 — designs multi-phase implementation plans for new capabilities. Takes a ProjectProposal (from capability assessor) + optional ResearchResult (from research agent), uses the escalation model (Gemini 3.1 Pro) to design multi-file solutions, writes design docs, and breaks work into phases executable in 1-3 dream cycles each.

**Architecture:** `src/core/strategic_planner.py` (plan creation + phase management + design doc generation + persistence) → `heartbeat.py` (Phase 0.98, every 5 cycles).

**Flow:** `create_plan(title, description, gap_name, jesse_actions, research_result)` → reads architecture context → escalation model designs solution (new files, modified files, integration points, phased tasks) → `_generate_design_doc()` writes `claude/DESIGN_*.md` → persists plan to `data/self_extension/projects.json`. `activate_plan(project_id)` starts Phase 1 after Jesse approval. `advance_plan(project_id)` checks phase completion and advances (creates goals for next phase tasks).

**Data classes:** `ImplementationPlan` (project_id, title, description, gap_name, status, phases, new_files, modified_files, integration_points, jesse_actions, total_cost). `PlanPhase` (phase_number, title, description, tasks, status). `PhaseTask` (description, task_type, files_involved, done). `PhaseResult` (action, phase_number, message, goal_descriptions).

**Heartbeat integration:** Phase 0.98 (every 5 cycles, offset 3). Checks for active self-extension project → calls `advance_plan()` → if phase complete, advances and creates goals for next phase tasks → if project complete, notifies Jesse.

**Safety:** Cost cap $0.20/plan (escalation model). Max 8 phases, 6 tasks per phase. One active project at a time. Protected files remain protected. Jesse approval required to activate plans.

Files: `strategic_planner.py` (~520 lines). Design doc: `claude/DESIGN_SELF_EXTENSION.md` (Component 3).

---

## Multi-Cycle Project Execution (session 238)

Self-Extension Phase 4 — connects the strategic planner's phases to the goal system so project work auto-advances through phases.

**Goal system extensions:** `Goal` class has `project_id` (str) and `project_phase` (int) fields. Goals created from strategic planner phases are tagged with project metadata. `GoalManager.get_project_phase_goals(project_id, phase)` returns all goals for a project+phase.

**Phase completion detection:** `GoalWorkerPool._check_project_phase_completion()` runs after each project-linked goal completes. When all goals for a project+phase are complete, marks all `PhaseTask`s in that phase as done via `mark_phase_task_done()`, enabling `advance_plan()` to progress on the next heartbeat check.

**Approval flow (assessor → planner → activation):** Two-stage process in `discord_bot.py`. Stage 1: Jesse affirms a capability gap proposal → `_create_plan_from_proposal()` invokes `StrategicPlanner.create_plan()` → formatted plan sent to Jesse → `_pending_plan_activation` stores project_id. Stage 2: Jesse affirms the plan → `_do_activate_plan()` → `activate_plan()` returns Phase 1 goal descriptions → goals created with project metadata → execution begins in dream cycles.

**Pending proposal tracking:** `capability_assessor.py` stores the last gap proposal via `set_pending_proposal(gap, proposal)`. Heartbeat Phase 0.97 sets this after sending a gap message. Discord bot checks it when processing affirmation intents.

**`activate_plan()` change:** Now returns `PhaseResult` (was `bool`) with `action="started"`, Phase 1 goal descriptions, so callers can create project-tagged goals immediately.

---

## Goal System

Goals are created from user requests, suggestion picks, or auto-escalated chat. Decomposed into 2-4 tasks by the Architect. Tasks execute via PlanExecutor (50 step limit, 25 for coding, 12 for chat).

Key mechanics: deferred request classification (Router model, no regex), task deferral (`deferred_until` field), file tracker for artifact awareness, long-term memory injection (LanceDB), follow-up task extraction (within-goal only).

Quality gates: `is_goal_relevant()`, `is_duplicate_goal()` (Jaccard > 0.6), `is_purpose_driven()`, memory dedup (distance < 0.5), 25 active goal cap. Stale goal pruning: `prune_stale_goals()` removes old undecomposed, empty zombie, all-terminal goals, and completed goals older than 7 days (session 222, uses last task completion time). `_repair_blocked_tasks()` (session 204) fixes pending tasks with failed dependencies → BLOCKED so all-terminal pruning catches dead goals.

File: `goal_manager.py`. See also `autonomous_executor.py`, `file_tracker.py`.

---

## PlanExecutor

Package: `src/core/plan_executor/` (executor, actions, safety, recovery, web).

**Actions:** web_search, fetch_webpage, create_file, append_file, read_file, list_files, write_source, edit_file, run_python, run_command, think, done, generate_image, skill_* (dynamic — any registered skill). **Search resilience** (session 187): `_do_web_search()` auto-broadens queries via `_simplify_query()` on 0 results (strips quotes, filler words, caps at 5 keywords, retries once). Caches search snippets by URL. `_do_fetch_webpage()` falls back to cached snippets on fetch failure (403, timeout, etc.).

Key behaviors: step budget awareness (warns at halfway, urgent at 3 remaining), per-task cost cap (`TASK_COST_CAP = $0.50` default, per-instance override via `cost_cap` param, session 178), context compression after step 8, structured output validation (2 retries + Claude escalation), mechanical error recovery (transient/mechanical/permanent classification), crash recovery state per task, repeated-error abort after 3 identical errors, rewrite-loop detection (strong hints at 2-3, force-stop at 4 writes to same file, session 178 strengthened), edit failure recovery (after 2 edit/append failures on same file → prompt hint to rewrite with create_file, session 175), `run_python` JS-boolean preamble (`true=True`, session 178), model-aware cache keys, **JSON truncation guard** (session 181): `create_file` validates JSON after write and returns error with `run_python` guidance if malformed; EFFICIENCY RULES hint steers model to `run_python` for large structured data. **Requirements pre-check** (session 179): after verify, `_check_task_requirements()` evaluates output against QA-level criteria using cheap Grok model. If gaps found and ≥3 steps remain, runs correction pass (up to 5 steps) with feedback injected. Prevents expensive Gemini retries by catching requirement gaps early. **Instruction anchoring** (session 166): hints split into "TASK REQUIREMENTS (mandatory)" (Architect spec hints) placed right after task description, vs "Context from past work" (everything else). Action-precedence directive before action menu. **Debug logging** (session 162): every LLM response logged to `logs/llm_debug/YYYY-MM-DD.jsonl` when `LLM_DEBUG_LOG=1` (default on). Disable with `LLM_DEBUG_LOG=0`.

**Source code approval:** `write_source`/`edit_file` on `src/` require Discord approval in dream mode, auto-approve in chat mode.

---

## Self-Extending Skill System

Skills are reusable Python modules in `data/skills/<name>/` with `skill.py` (implements `execute(params: dict) -> dict`), `SKILL.json` (manifest), and optional `README.md`. Configured in `config/skills.yaml`.

```
User: "/skill create X"  OR  Dream cycle detects repeated pattern
  → conversational_router fast-path → action="create_skill"
  → action_dispatcher._handle_create_skill() calls:
    → skill_creator.py generates code + manifest
    → skill_validator.py AST-checks for blocked imports/builtins/attributes
    → skill_system.py registers as LoadedSkill, wraps as _SkillTool in tool_registry
  → PlanExecutor invokes via "skill_<name>" action → actions._do_invoke_skill()
```

**Safety:** AST validation blocks subprocess, socket, eval, exec, os.system, etc. 30s execution timeout. 50KB code limit. All outcomes tracked in LearningSystem. `/skill` commands: `list`, `info <name>`, `create <desc>`.

**Dream integration:** `skill_suggestions.py` scans every 5th dream cycle for repeated action patterns (3+ occurrences) and proposes new skills.

**Input schema extraction** (session 192): `_extract_input_schema()` in `skill_creator.py` populates `input_schema.properties` automatically from generated code — AST-based `params.get()` extraction for names/types/defaults, docstring parsing for descriptions and required/optional classification. **Description extraction** (session 193): `_extract_description()` extracts clean one-line descriptions from skill code docstrings for the manifest, replacing raw user request text.

Files: `skill_system.py` (~280 lines), `skill_validator.py` (~250 lines), `skill_creator.py` (~590 lines), `skill_suggestions.py` (~220 lines).

---

## Safety Boundaries

**Protected files:** plan_executor/ (all 6), safety_controller.py, config.py, git_safety.py, prime_directive.txt, rules.yaml, archi_identity.yaml, personality.yaml, mcp_servers.yaml, claude/, heartbeat.py, goal_manager.py, system_monitor.py, health_check.py, performance_monitor.py.

**Command safety:** Allowlist-first (`rules.yaml`), blocklist as defense-in-depth. No `echo` (env var exfiltration vector).

**Path validation:** `os.path.realpath()` resolves symlinks before boundary checks. SSRF protection via `is_private_url()` in `net_safety.py`.

**Budget enforcement:** Daily $5, monthly $100, per-cycle $0.50, per-task $0.50, per-goal $1.00. Atomic writes for cost_usage.json. **Budget trajectory** (session 125): `CostTracker.get_budget_projection()` extrapolates hourly burn rate to EOD/EOM; `Heartbeat._check_budget_trajectory()` skips work on "stop", halves workers on "throttle", notifies user via Discord (2hr rate limit).

**Quiet hours:** 11 PM–6 AM, overridden by recent activity (30 min). Suppressed messages queued and delivered as digest.

---

## Key Config Values

| Setting | Value | Location |
|---------|-------|----------|
| Daily/monthly budget | $5 / $100 | rules.yaml |
| Per-cycle budget | $0.50 | rules.yaml |
| Dream cycle interval | 900s base (adaptive: 300s–7200s) | heartbeat.yaml |
| Max steps per task | 50 (25 coding, 12 chat) | plan_executor |
| Per-task cost cap | $0.50 | plan_executor |
| Max active goals | 25 | idea_generator.py |
| Quiet hours | 11 PM–6 AM | archi_identity.yaml |
| Max parallel tasks | 3 per wave | heartbeat.yaml |
| Suggest cooldown | 120s base, doubles, 4h max | heartbeat.py |

---

## Entry Points

- **Start:** `python scripts/start.py` → service, discord-only, or watchdog mode. Startup runs "2+2" connectivity test. Network readiness check (DNS probe loop, session 191) blocks before Discord bot start; heartbeat deferred until Discord `on_ready` fires (health gate).
- **Discord bot:** `_wait_for_network()` → `run_bot()` (with retry on transient DNS/connection errors) → `on_ready` → `_load_startup_context()` (backfills chat history from DM if empty) → `_ready_at = time.time()` → heartbeat starts. `on_message` skips messages older than 30s via timestamp guard. Commands: `/purge`, `/clear`, `/cleanup`.
- **Shutdown:** Ctrl+C → signal handler (installed before bot thread) → suppresses console logging → prints boxed message → `stop_event` + `signal_task_cancellation("shutdown")` → `router.close()` kills in-flight API requests → `request_bot_stop()` signals Discord bot's asyncio loop → cancel all pending asyncio tasks → bot thread join (8s timeout) → clean exit. Main loop uses 0.5s wait timeout for sub-second signal response. Watchdog uses `Popen` + poll loop with `KeyboardInterrupt` catch. `scripts/stop.py` is nuclear kill.
- **Monitor resilience:** `_monitor_loop()` wrapped in try/except per tick, CRITICAL log on thread death, watchdog heartbeat.

---

## Testing

~1399 unit tests on Windows (session 127 count, likely stale). Linux/Cowork shows ~4568 passed (excl scheduler), ~18 skipped (session 222 count); env-specific skips (mcp_client asyncio, project_context, project_sync). `test_direct_providers.py` cleanly skipped via `pytest.importorskip("openai")`. `tests/conftest.py` ensures project root is on `sys.path` — no `PYTHONPATH=.` needed. 36 live API integration tests (~$0.008/run). Standalone harness via `/test` Discord command or `python tests/integration/test_harness.py --quick`.

```
pytest tests/unit/ -m "not live"          # Unit tests (free)
pytest tests/integration/test_v2_pipeline.py -v  # Live API (~$0.008)
```

---

## Notification System

All outbound notifications route through `notification_formatter.py` (one Grok call per notification, ~$0.0002). Types: goal completion, morning report, hourly summary, work suggestions, idle prompt, findings, initiative announcements. Per-task notifications disabled (session 166) — only goal-level completion DMs. 60s cooldown between DMs (bypass for goal completions). Reaction tracking (👍/👎) feeds into `learning_system.record_feedback()`. `strip_tool_names()` (public API, session 189) strips internal tool name references from user-facing text — applied both in `_call_formatter()` output and in task result summaries before storage. Conversation starters use forced category rotation (session 189) — 10 interest categories cycle sequentially via `_STARTER_CATEGORIES` in Heartbeat. **File delivery** (session 207): chat-mode replies auto-attach `files_created` from PlanExecutor results as Discord files (8 MB limit, skips binary/DB); dream-mode goal completions attach the first sendable file via `send_notification(file_path=...)`. **Quality monitoring** (session 220): all `_call_formatter()` outputs logged to `logs/notifications.jsonl` with type, source (model/fallback/rejected/error), message text, char count, and cost.

---

## Known Issues

**Greeting handler edge case:** `_is_greeting_or_social()` can misclassify short messages starting with greetings where the remainder is under 16 chars and contains no action keyword. Low priority.
