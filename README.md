# Archi

An autonomous AI agent that runs on your machine, communicates via Discord, and works independently in the background. Archi uses OpenRouter API by default (Grok 4.1 Fast) with optional direct provider routing (xAI, Anthropic, DeepSeek, OpenAI, Mistral), plus Claude Haiku for computer use tasks.

It operates in two modes: **chat mode** for responding to Discord messages, and **dream mode** for autonomous background work when idle — pursuing goals, researching topics, and learning from its actions.

## Features

- **Multi-provider inference** — Default: OpenRouter (x-ai/grok-4.1-fast at ~$0.52-1.04/day). Optional: route directly to xAI, Anthropic, DeepSeek, etc. by adding API keys. Switchable at runtime via Discord ("switch to deepseek", "switch to grok direct", etc.).
- **Auto-escalation for computer use** — Click, screenshot, and vision tasks automatically switch to Claude Haiku, then revert when done.
- **Dream cycles** — Autonomous background processing when idle 5+ minutes: goal decomposition, research, file creation, self-review, brainstorming, and cross-goal synthesis
- **Multi-step reasoning** — PlanExecutor engine handles research, analysis, and multi-part requests with crash recovery and self-verification
- **Goal system** — Create goals via chat or commands; Archi decomposes them into tasks and executes autonomously
- **Discord interface** — DM or @mention with live progress updates during multi-step tasks
- **Desktop & browser automation** — pyautogui mouse/keyboard/screenshot + Playwright web navigation
- **Three-tier memory** — Short-term (in-memory), working (SQLite), long-term (LanceDB vectors with semantic deduplication)
- **Safety controls** — Protected files, blocked commands, budget enforcement, workspace isolation, git-backed rollback
- **Image generation** — Local SDXL text-to-image (optional)
- **Free web search** — DuckDuckGo search, no API key needed
- **Learning system** — Records experiences, extracts patterns, generates improvement suggestions

## Quick Start

### Prerequisites

- Python 3.10–3.12 (3.13+ not yet supported by ML dependencies)
- 16GB+ RAM recommended
- NVIDIA GPU optional (for local SDXL image generation)
- Windows (primary target) or Linux

### 1. Clone and set up

```bash
git clone https://github.com/koorbmeh/Archi.git
cd Archi
```

Create a virtual environment and install dependencies:

```bash
# Windows (PowerShell)
py -m venv venv
.\venv\Scripts\pip.exe install -r requirements.txt

# Linux
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Or use the installer script: `python scripts/install.py deps`

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your settings
```

**Required:** `OPENROUTER_API_KEY` — get one at [openrouter.ai/keys](https://openrouter.ai/keys). Powers all inference (default model: Grok 4.1 Fast).

**Optional but recommended:**
- `DISCORD_BOT_TOKEN` — for Discord interface (the only active interface)
- `CUDA_PATH` — CUDA toolkit root if not auto-detected (only needed for SDXL image generation)

### 3. Configure identity

```bash
cp config/archi_identity.example.yaml config/archi_identity.yaml
cp config/prime_directive.example.txt config/prime_directive.txt
```

Edit `archi_identity.yaml` to set Archi's name, role, focus areas, and proactive tasks. Edit `prime_directive.txt` with your operational guidelines. These shape how Archi behaves and what it works on autonomously.

### 4. Run

```bash
# Windows
.\venv\Scripts\python.exe scripts\start.py

# Linux
python scripts/start.py
```

This starts the full service: agent loop, dream cycle monitoring, and Discord bot (if configured).

## Configuration

### .env

Copy `.env.example` to `.env`. Key settings:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key (default provider for all inference) |
| `OPENROUTER_MODEL` | No | API model override (default: `x-ai/grok-4.1-fast` in code) |
| `XAI_API_KEY` | No | xAI direct API key ("switch to grok direct") |
| `ANTHROPIC_API_KEY` | No | Anthropic direct API key ("switch to claude direct") |
| `DEEPSEEK_API_KEY` | No | DeepSeek direct API key ("switch to deepseek direct") |
| `DISCORD_BOT_TOKEN` | No | Discord bot token |
| `CUDA_PATH` | No | CUDA toolkit root (auto-detected on Windows, only for SDXL) |
| `ARCHI_ROOT` | No | Base path for logs, data, workspace (default: repo root) |
| `DAILY_BUDGET_USD` | No | Override daily budget (default: from rules.yaml) |

### config/rules.yaml

Safety and operational rules: budget limits ($5/day, $100/month — typical usage ~$0.52-1.04/day with Grok), protected files, blocked commands, risk levels for different actions.

### config/archi_identity.yaml

Archi's personality, focus areas, and proactive task definitions. Drives what Archi works on during dream cycles.

### config/heartbeat.yaml

Adaptive sleep timing: command mode (10s for 120s after activity), monitoring mode (60s), deep sleep (600s, max 1800s). Night mode (11PM-6AM) uses 1800s intervals.

## Usage

### Discord Bot

DM the bot or @mention it in a channel.

**Setup:**
1. Create a bot at [Discord Developer Portal](https://discord.com/developers/applications)
2. Under **Bot → Privileged Gateway Intents**, enable: **Message Content Intent**
3. Copy the bot token → add `DISCORD_BOT_TOKEN=your_token` to `.env`
4. Under **OAuth2 → URL Generator**: select **bot** scope, then these permissions: **Send Messages**, **Embed Links**, **Attach Files**, **Read Message History**
5. Open the generated URL to invite the bot to your server
6. Start Archi — the Discord bot launches automatically

**Commands:** `/goal <description>`, `/goals`, `/status`, `/cost`, `/test`, `/help`

You can also chat naturally, give multi-step tasks ("Research the best thermal paste and write a report"), request files ("Create a Python script that..."), switch models on the fly ("switch to deepseek", "use claude for this task"), and receive notifications from dream cycles.

## How It Works

### Chat Mode

Messages flow through a v2 pipeline (`message_handler.py` → `intent_classifier.py` → `action_dispatcher.py` → `response_builder.py`):

1. **Fast paths** (no model call, $0): greetings, time questions, slash commands
2. **Multi-step routing**: research, analysis, and multi-part requests go to PlanExecutor (up to 12 steps in chat, 25 for coding)
3. **Model intent classification**: everything else gets a single API call to determine action

Multi-step tasks show live progress in Discord ("Step 3/12: Searching...").

### Dream Mode

After 5 minutes of inactivity, Archi enters a dream cycle with phases: morning report → brainstorming → task execution → history review → future planning → synthesis. Each cycle is capped at 10 minutes and $0.50 in API costs. Dream cycles are interruptible — any user activity stops the cycle immediately.

### Goal System

Goals are created via chat, commands, or autonomously during dream cycles. Each goal is decomposed into 2-4 tasks, then executed through PlanExecutor. PlanExecutor supports web search, webpage fetching, file operations, Python execution, shell commands, and a "think" action for reasoning steps. It has crash recovery (state saved after each step) and self-verification (reads back created files, rates quality 1-10).

### Model Routing

```
Request arrives
  ├─ Cache hit? → return cached ($0)
  └─ Active provider's API (default: OpenRouter → x-ai/grok-4.1-fast)
      └─ User can switch models and providers at runtime via Discord
```

Default model: x-ai/grok-4.1-fast ($0.20/$1.00 per 1M tokens input/output) via OpenRouter. Computer use tasks auto-escalate to Claude Haiku. Typical daily cost: ~$0.52-1.04 with active dream cycles. Add API keys for xAI, Anthropic, DeepSeek, etc. to route directly to those providers ("switch to grok direct").

## Project Structure

```
Archi/
├── config/
│   ├── archi_identity.yaml    # Identity, focus areas, proactive tasks
│   ├── heartbeat.yaml         # Sleep timing configuration
│   ├── prime_directive.txt    # Core operational guidelines
│   └── rules.yaml             # Safety: budgets, protected files, blocked commands
├── src/
│   ├── core/
│   │   ├── agent_loop.py      # Main tick loop
│   │   ├── dream_cycle.py     # Autonomous background work engine
│   │   ├── autonomous_executor.py  # Task execution loop + follow-up extraction
│   │   ├── idea_generator.py  # Brainstorming, goal hygiene, proactive planning
│   │   ├── reporting.py       # Morning report + hourly summary notifications
│   │   ├── goal_manager.py    # Goal/task CRUD, decomposition, state
│   │   ├── plan_executor.py   # Multi-step task execution engine
│   │   ├── heartbeat.py       # Adaptive sleep (command/monitoring/deep)
│   │   ├── safety_controller.py  # Action authorization by risk level
│   │   ├── learning_system.py # Experience recording, pattern extraction
│   │   ├── user_preferences.py   # Preference extraction from conversations
│   │   ├── interesting_findings.py  # Queue notable research for user delivery
│   │   ├── file_tracker.py    # Workspace file tracking (goal→file mapping)
│   │   ├── logger.py          # Logging configuration
│   │   └── resilience.py      # Circuit breakers and retry logic
│   ├── interfaces/
│   │   ├── message_handler.py   # v2 entry point: pre-process → classify → dispatch → respond
│   │   ├── intent_classifier.py # Fast-path routing + model intent classification
│   │   ├── action_dispatcher.py # Action execution (file ops, search, browse, etc.)
│   │   ├── response_builder.py  # Response formatting, logging, preference extraction
│   │   ├── discord_bot.py       # Discord DM interface
│   │   ├── chat_history.py      # Multi-turn conversation history
│   │   └── voice_interface.py   # Text-to-speech via Piper
│   ├── models/
│   │   ├── router.py          # Multi-provider routing + auto-escalation for computer use
│   │   ├── openrouter_client.py  # Universal LLM client (any OpenAI-compatible provider)
│   │   ├── providers.py       # Provider registry, model aliases, pricing
│   │   └── cache.py           # Query cache with LRU eviction
│   ├── tools/
│   │   ├── tool_registry.py   # Tool dispatch with circuit breakers
│   │   ├── image_gen.py       # SDXL local image generation
│   │   ├── desktop_control.py # pyautogui: click, type, screenshot
│   │   ├── browser_control.py # Playwright: navigate, click, fill
│   │   ├── computer_use.py    # Vision-guided orchestrator
│   │   ├── web_search_tool.py # DuckDuckGo web search
│   │   └── ui_memory.py       # UI element position cache
│   ├── memory/
│   │   ├── memory_manager.py  # 3-tier: short-term, working (SQLite), long-term (LanceDB)
│   │   └── vector_store.py    # LanceDB vector storage backend
│   ├── monitoring/
│   │   ├── system_monitor.py  # CPU, memory, disk, temperature
│   │   ├── cost_tracker.py    # Budget enforcement
│   │   ├── health_check.py    # Component health checks
│   │   └── performance_monitor.py  # Response times, throughput
│   ├── utils/
│   │   ├── paths.py           # base_path resolution
│   │   ├── config.py          # rules.yaml + heartbeat.yaml loading
│   │   ├── git_safety.py      # Git checkpoint/rollback for source modifications
│   │   ├── text_cleaning.py   # strip_thinking, sanitize_identity, extract_json
│   │   └── parsing.py         # JSON extraction helpers
│   ├── maintenance/
│   │   └── timestamps.py      # Timestamp utilities
│   └── service/
│       └── archi_service.py   # Production service wrapper
├── data/                       # Runtime data (created automatically)
│   ├── goals_state.json       # Goal/task state
│   ├── dream_log.jsonl        # Dream cycle history
│   ├── memory.db              # SQLite working memory
│   ├── ui_memory.db           # UI element position cache
│   └── vectors/               # LanceDB embeddings
├── workspace/                  # User-facing output (reports, projects, images)
├── logs/                       # Conversation logs, action logs, traces
├── scripts/
│   ├── install.py, start.py, fix.py, stop.py, reset.py
│   └── startup_archi.bat      # Windows auto-start wrapper
└── tests/
    ├── unit/                   # Unit tests (classifiers, history, cache, etc.)
    └── integration/            # Full system, gate tests, and test harness
```

## Safety

- **Protected files** — core system files (plan_executor.py, safety_controller.py, rules.yaml, etc.) cannot be modified by autonomous actions
- **Blocked commands** — rm -rf, format, shutdown, reboot, fork bombs, registry edits, etc.
- **Budget enforcement** — hard stop at daily/monthly API cost limits
- **Workspace isolation** — file operations restricted to the workspace directory
- **Git safety** — automatic checkpoints before source modifications, syntax check after, rollback on failure
- **Risk levels** — actions classified L1 (low) through L4 (critical) with different authorization requirements

## Scripts

| Script | Purpose | Examples |
|--------|---------|---------|
| `install.py` | Setup: deps, models, CUDA, image gen, auto-start | `scripts/install.py deps` |
| `start.py` | Launch: service, discord, watchdog | `scripts/start.py` |
| `fix.py` | Diagnose, test, clean caches, repair state | `scripts/fix.py diagnose` |
| `stop.py` | Stop processes, restart | `scripts/stop.py restart` |
| `reset.py` | Factory reset: clears runtime state, preserves config/workspace | `scripts/reset.py` |

### start.py modes

| Mode | What it runs |
|------|-------------|
| `service` | Agent loop + Discord bot (default) |
| `discord` | Discord bot only |
| `watchdog` | Service with auto-restart on crash |

### Windows auto-start

`scripts/startup_archi.bat` sets `ARCHI_ROOT` from its own location and runs `start.py watchdog`. Use `python scripts/install.py autostart` to configure it, or set it up manually in Task Scheduler.

## Deployment

### Linux (systemd)

Create a service file at `/etc/systemd/system/archi.service`:

```ini
[Unit]
Description=Archi AI Agent
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/Archi
ExecStart=/path/to/Archi/venv/bin/python scripts/start.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
# Edit paths in archi.service, then:
sudo systemctl daemon-reload
sudo systemctl enable archi
sudo systemctl start archi
```

### Windows (NSSM)

```powershell
choco install nssm
python scripts/install.py autostart
nssm start ArchiAgent
```

Or use `scripts/startup_archi.bat` with Task Scheduler.

### Security notes

- Store API keys in `.env` (not committed to git via whitelist-based .gitignore)
- Discord bot token should be kept secret — never commit it to version control

## Troubleshooting

**Run diagnostics:** `python scripts/fix.py diagnose` — checks environment, models, CUDA, API keys, ports, and system health.

**Run tests:** `python scripts/fix.py test` or `python -m pytest tests/ -v`

### Common issues

**Archi won't start:** Check dependencies (`scripts/install.py deps`) and verify your `.env` has `OPENROUTER_API_KEY` and `DISCORD_BOT_TOKEN` set.

**CUDA errors:** Only relevant if using SDXL image generation. Run `scripts/fix.py diagnose` for diagnostics.

**Budget exceeded:** Check spend with `scripts/fix.py diagnose` or `/cost` in Discord. Increase limits in `config/rules.yaml` or clear with `scripts/reset.py`.

### Logs

| Log | Location | Contents |
|-----|----------|----------|
| Conversations | `logs/conversations.jsonl` | User↔Archi exchanges with timestamp, source, action, cost |
| Chat trace | `logs/chat_trace.log` | Chat flow: intent parsing, model selection, routing |
| Daily actions | `logs/actions/YYYY-MM-DD.jsonl` | Per-day action log |
| Dream log | `data/dream_log.jsonl` | Dream cycle summaries |
| Goal state | `data/goals_state.json` | Goals and tasks with full lifecycle |

## Development

### Running tests

```bash
python -m pytest tests/ -v              # all tests
python -m pytest tests/unit/ -v         # unit tests only
python -m pytest tests/ -k router -v    # specific tests by keyword
```

### Adding tools

Create a new tool class in `src/tools/` and register it in `tool_registry.py`. Tools are wrapped with circuit breakers for resilience.

### Adding models or providers

Model aliases, provider definitions, and pricing are all in `src/models/providers.py`. To add a new provider: add an entry to `PROVIDERS` (base_url, api_key_env, default_model), add aliases to `MODEL_ALIASES`, add pricing to `MODEL_PRICING`, and add the API key placeholder to `.env.example`. Switch at runtime via Discord ("switch to grok direct", "switch to deepseek", etc.).

---

**Issues:** [github.com/koorbmeh/Archi/issues](https://github.com/koorbmeh/Archi/issues)
