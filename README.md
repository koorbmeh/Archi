# Archi

A local-first autonomous AI agent that runs on your machine, communicates via Discord, and works independently in the background. Archi uses local LLMs via llama.cpp for most tasks (free) and OpenRouter API as a paid fallback for complex work.

It operates in two modes: **chat mode** for responding to messages (Discord, web, or CLI), and **dream mode** for autonomous background work when idle — pursuing goals, researching topics, and learning from its actions.

## Features

- **Local-first inference** — Dual local models via llama.cpp: Qwen3-8B for reasoning/chat (primary) and Qwen3VL-8B for vision tasks (loaded on demand). Single-GPU swap architecture keeps VRAM usage low.
- **OpenRouter API fallback** — Automatic escalation to stronger models when complexity warrants it. Default model configurable via `OPENROUTER_MODEL` env var.
- **Dream cycles** — Autonomous background processing when idle 5+ minutes: goal decomposition, research, file creation, self-review, brainstorming, and cross-goal synthesis
- **Multi-step reasoning** — PlanExecutor engine handles research, analysis, and multi-part requests with crash recovery and self-verification
- **Goal system** — Create goals via chat or commands; Archi decomposes them into tasks and executes autonomously
- **Discord bot, web chat, CLI** — Multiple interfaces with live progress updates during multi-step tasks
- **Web dashboard** — System health, costs, goals, and dream cycle status at a glance
- **Desktop & browser automation** — pyautogui mouse/keyboard/screenshot + Playwright web navigation
- **Three-tier memory** — Short-term (in-memory), working (SQLite), long-term (LanceDB vectors)
- **Safety controls** — Protected files, blocked commands, budget enforcement, workspace isolation, git-backed rollback
- **Image generation** — Local SDXL text-to-image (optional)
- **Free web search** — DuckDuckGo via local model, no API key needed
- **Learning system** — Records experiences, extracts patterns, generates improvement suggestions

## Quick Start

### Prerequisites

- Python 3.10+
- 16GB+ RAM recommended
- NVIDIA GPU recommended (CUDA); CPU-only works but is slower
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

**Required:** `OPENROUTER_API_KEY` — get one at [openrouter.ai/keys](https://openrouter.ai/keys). Used as fallback when the local model can't handle a request.

**Optional but recommended:**
- `LOCAL_MODEL_PATH` — path to your GGUF model file (see [Local Model Setup](#local-model-setup))
- `DISCORD_BOT_TOKEN` — for Discord integration
- `CUDA_PATH` — CUDA toolkit root if not auto-detected

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

This starts the full service: agent loop, dream cycle monitoring, Discord bot (if configured), web dashboard (port 5000), and web chat (port 5001).

## Local Model Setup

Archi uses a **dual local model architecture** via llama.cpp, with only one model loaded on the GPU at a time:

- **Reasoning model** (primary): Qwen3-8B — handles all text tasks (chat, intent parsing, planning, JSON generation). Loaded at startup.
- **Vision model** (on-demand): Qwen3VL-8B — loaded when image analysis is needed, then swapped back to reasoning.
- If no reasoning model is available, the vision model handles everything.

Reasoning model fallback order: Qwen3-8B → DeepSeek-R1-Distill-Llama-8B → Phi-4-mini-reasoning.

Model files go in the `models/` directory. Download them with: `python scripts/install.py models`

### llama-cpp-python (JamePeng fork required)

Qwen3VL vision support requires the [JamePeng fork](https://github.com/jamepeng/llama-cpp-python) of llama-cpp-python. The standard release doesn't include the Qwen3VL chat handler.

**CPU install:**
```bash
pip install llama-cpp-python @ git+https://github.com/jamepeng/llama-cpp-python
```

**GPU (CUDA) install — build from source:**

Requires Visual Studio with "Desktop development with C++" workload and CUDA Toolkit 12.4+.

```bash
python scripts/install.py cuda
```

This runs CUDA diagnostics and builds llama-cpp-python with GPU support. Build takes 20-40 minutes.

### CUDA environment

Set in `.env` if CUDA isn't auto-detected:

```bash
CUDA_PATH=C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.1
```

Verify with: `python scripts/fix.py diagnose`

### Context window sizing

Set `ARCHI_CONTEXT_SIZE` in `.env` to control VRAM usage:

| VRAM | Recommended | Setting |
|------|------------|---------|
| 8GB+ | 32K tokens | `ARCHI_CONTEXT_SIZE=32768` (default) |
| 6GB | 16K tokens | `ARCHI_CONTEXT_SIZE=16384` |
| 4GB | 8K tokens | `ARCHI_CONTEXT_SIZE=8192` |

### Running without a local model

Archi degrades gracefully without a local model, routing all requests to OpenRouter API. Set `LOCAL_MODEL_PATH=` (empty) in `.env`. This costs more but requires no GPU.

## Configuration

### .env

Copy `.env.example` to `.env`. Key settings:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for paid model fallback |
| `OPENROUTER_MODEL` | No | API model to use (default: `openrouter/auto`) |
| `LOCAL_MODEL_PATH` | No | Path to local GGUF model file |
| `DISCORD_BOT_TOKEN` | No | Discord bot token |
| `CUDA_PATH` | No | CUDA toolkit root (auto-detected on Windows) |
| `ARCHI_ROOT` | No | Base path for logs, data, workspace (default: repo root) |
| `ARCHI_CONTEXT_SIZE` | No | Context window in tokens (default: 32768) |
| `ARCHI_PREFER_LOCAL_STRICT` | No | Set to `1` to never escalate to API when prefer_local=True |
| `DAILY_BUDGET_USD` | No | Override daily budget (default: from rules.yaml) |

### config/rules.yaml

Safety and operational rules: budget limits ($5/day, $100/month default), protected files, blocked commands, risk levels for different actions.

### config/archi_identity.yaml

Archi's personality, focus areas, and proactive task definitions. Drives what Archi works on during dream cycles.

### config/heartbeat.yaml

Adaptive sleep timing: command mode (10s for 120s after activity), monitoring mode (60s), deep sleep (600s, max 1800s). Night mode (11PM-6AM) uses 1800s intervals.

## Usage

### Discord Bot

DM the bot or @mention it in a channel.

**Setup:**
1. Create a bot at [Discord Developer Portal](https://discord.com/developers/applications)
2. Copy the bot token
3. Add `DISCORD_BOT_TOKEN=your_token` to `.env`
4. Invite the bot to your server (OAuth2 → URL Generator → bot scope)
5. Start Archi — the Discord bot launches automatically

**Commands:** `/goal <description>`, `/goals`, `/status`, `/cost`, `/help`

You can also chat naturally, give multi-step tasks ("Research the best thermal paste and write a report"), request files ("Create a Python script that..."), and receive notifications from dream cycles.

### Web Chat

**http://127.0.0.1:5001/chat** — runs automatically with the full service. Same capabilities as Discord with WebSocket messaging and typing indicators.

Standalone (for testing): `python scripts/start.py web`

### CLI Chat

`python scripts/start.py chat` — terminal-based chat with the same command set.

### Web Dashboard

**http://127.0.0.1:5000** — system health, costs, goals, dream cycle status. Auto-refreshes every 10 seconds.

Standalone: `python scripts/start.py dashboard`

API endpoints: `GET /api/health`, `/api/costs`, `/api/performance`, `/api/goals`, `/api/dream`, `POST /api/goals/create`

## How It Works

### Chat Mode

Messages flow through `action_executor.py`:

1. **Fast paths** (no model call, $0): greetings, time questions, slash commands, file listings, work status queries
2. **Multi-step routing**: research, analysis, and multi-part requests go to PlanExecutor (up to 12 steps in chat, 30 for coding)
3. **Single-shot intent**: simple requests get a single model call to determine action

In chat mode, the local model always handles the request (speed priority). Multi-step tasks show live progress in Discord ("Step 3/12: Searching...").

### Dream Mode

After 5 minutes of inactivity, Archi enters a dream cycle with phases: morning report → brainstorming → task execution → history review → future planning → synthesis. Each cycle is capped at 10 minutes and $0.50 in API costs. Dream cycles are interruptible — any user activity stops the cycle immediately.

In dream/plan mode, the router can escalate to OpenRouter API based on prompt complexity, unlike chat mode.

### Goal System

Goals are created via chat, commands, or autonomously during dream cycles. Each goal is decomposed into 2-4 tasks, then executed through PlanExecutor. PlanExecutor supports web search, webpage fetching, file operations, Python execution, shell commands, and a "think" action for reasoning steps. It has crash recovery (state saved after each step) and self-verification (reads back created files, rates quality 1-10).

### Model Routing

```
Request arrives
  ├─ Cache hit? → return cached ($0)
  ├─ Chat mode (prefer_local=True) → always use local model
  └─ Dream/plan mode (prefer_local=False) → classify complexity
      ├─ Simple/medium → try local, escalate if low confidence
      └─ Complex → OpenRouter API
```

Local models: Qwen3-8B (reasoning), Qwen3VL-8B (vision). OpenRouter: configurable via `OPENROUTER_MODEL` (default: `openrouter/auto`). Most daily interactions cost $0 through local inference. Typical daily API cost: $0.01-0.10 depending on dream cycle activity.

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
│   │   ├── goal_manager.py    # Goal/task CRUD, decomposition, state
│   │   ├── plan_executor.py   # Multi-step task execution engine
│   │   ├── heartbeat.py       # Adaptive sleep (command/monitoring/deep)
│   │   ├── safety_controller.py  # Action authorization by risk level
│   │   └── learning_system.py # Experience recording, pattern extraction
│   ├── interfaces/
│   │   ├── action_executor.py # Message processing (intent → action → response)
│   │   ├── discord_bot.py     # Discord DM interface
│   │   └── web_chat.py        # Web chat (Flask-SocketIO)
│   ├── models/
│   │   ├── router.py          # Model routing: local vs API by complexity
│   │   ├── local_model.py     # Qwen3-8B + Qwen3VL-8B via llama.cpp
│   │   ├── openrouter_client.py  # OpenRouter API client
│   │   └── cache.py           # Query cache with LRU eviction
│   ├── tools/
│   │   ├── tool_registry.py   # Tool dispatch with circuit breakers
│   │   ├── image_gen.py       # SDXL local image generation
│   │   ├── desktop_control.py # pyautogui: click, type, screenshot
│   │   ├── browser_control.py # Playwright: navigate, click, fill
│   │   └── computer_use.py    # Vision-guided orchestrator
│   ├── memory/
│   │   └── memory_manager.py  # 3-tier: short-term, working (SQLite), long-term (LanceDB)
│   ├── monitoring/
│   │   ├── system_monitor.py  # CPU, memory, disk, temperature
│   │   ├── cost_tracker.py    # Budget enforcement
│   │   ├── health_check.py    # Component health checks
│   │   └── performance_monitor.py  # Response times, throughput
│   ├── service/
│   │   └── archi_service.py   # Production service wrapper
│   └── web/
│       └── dashboard.py       # Flask web dashboard
├── data/                       # Runtime data (created automatically)
│   ├── goals_state.json       # Goal/task state
│   ├── dream_log.jsonl        # Dream cycle history
│   ├── memory.db              # SQLite working memory
│   ├── ui_memory.db           # UI element position cache
│   └── vectors/               # LanceDB embeddings
├── workspace/                  # User-facing output (reports, projects, images)
├── logs/                       # Conversation logs, action logs, traces
├── models/                     # GGUF model files
├── scripts/
│   ├── install.py, start.py, fix.py, stop.py, reset.py
│   └── startup_archi.bat      # Windows auto-start wrapper
└── tests/
    ├── unit/                   # Unit tests (classifiers, history, cache, etc.)
    ├── integration/            # Full system and gate tests
    └── scripts/                # Functional tests (dream cycle, tools, etc.)
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
| `install.py` | Setup: deps, models, CUDA, voice, image gen, auto-start | `scripts/install.py models` |
| `start.py` | Launch: service, chat, web, dashboard, discord, watchdog | `scripts/start.py` |
| `fix.py` | Diagnose, test, clean caches, repair state | `scripts/fix.py diagnose` |
| `stop.py` | Stop processes, free ports, restart | `scripts/stop.py restart` |
| `reset.py` | Factory reset: clears runtime state, preserves config/workspace | `scripts/reset.py` |

### start.py modes

| Mode | What it runs |
|------|-------------|
| `service` | Agent loop + Discord bot (default) |
| `chat` | CLI terminal chat only |
| `web` | Web chat on port 5001 |
| `dashboard` | Dashboard on port 5000 |
| `discord` | Discord bot only |
| `watchdog` | Service with auto-restart on crash |

Add `--web` to `service` or `watchdog` to also enable web chat and dashboard.

### Windows auto-start

`scripts/startup_archi.bat` sets `ARCHI_ROOT` from its own location and runs `start.py watchdog`. Use `python scripts/install.py autostart` to configure it, or set it up manually in Task Scheduler.

## Deployment

### Linux (systemd)

```bash
sudo cp archi.service /etc/systemd/system/
# Edit: User, WorkingDirectory, ExecStart paths
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

- Dashboard and web chat bind to localhost only by default
- Use SSH tunneling or VPN for remote access — never expose ports directly
- Store API keys in `.env` (not committed to git via whitelist-based .gitignore)

## Troubleshooting

**Run diagnostics:** `python scripts/fix.py diagnose` — checks environment, models, CUDA, API keys, ports, and system health.

**Run tests:** `python scripts/fix.py test` or `python -m pytest tests/ -v`

### Common issues

**Archi won't start:** Check dependencies (`scripts/install.py deps`), local model download (`scripts/install.py models`), and ports 5000/5001 (`scripts/stop.py ports`).

**CUDA errors:** Run `scripts/install.py cuda` for diagnostics. Use `scripts/start.py watchdog` for auto-restart on CUDA crashes, or set `ARCHI_SKIP_LEARNING=1` to reduce GPU load.

**Budget exceeded:** Check spend with `scripts/fix.py diagnose` or `/cost` in chat. Increase in `config/rules.yaml` or clear with `scripts/reset.py`.

**Port conflict:** Use `scripts/stop.py` to kill stale processes holding ports.

**Dashboard shows "Not Initialized":** The dashboard needs the full service running. Use `scripts/start.py`, not `scripts/start.py dashboard`.

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

### Adding models

Configure new OpenRouter models via `OPENROUTER_MODEL` in `.env`. For local models, place the GGUF file in `models/` and update `LOCAL_MODEL_PATH`. The reasoning model fallback list is in `local_model.py`.

---

**Issues:** [github.com/koorbmeh/Archi/issues](https://github.com/koorbmeh/Archi/issues)
