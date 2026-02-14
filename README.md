# Archi

A local-first autonomous AI agent that runs on your machine, communicates via Discord, and works independently in the background. Archi uses a local LLM (Qwen3VL-8B via llama.cpp) for most tasks and OpenRouter API as a paid fallback for complex work, keeping costs near zero for daily use.

Archi operates in two modes: **chat mode** for responding to your messages (via Discord, web, or CLI), and **dream mode** for autonomous background work when idle — researching topics, pursuing goals, and learning from its own actions.

## Features

- **Local-first AI** — Qwen3VL-8B handles most tasks at $0 cost, with vision support for screenshots and desktop automation
- **OpenRouter API fallback** — Automatic escalation to stronger models (Grok, DeepSeek, or auto-routed) when complexity warrants it
- **Dream cycles** — Autonomous background processing when idle for 5+ minutes: goal decomposition, research, file creation, self-review
- **Multi-step reasoning** — PlanExecutor engine handles research, analysis, and multi-part requests in both chat and dream mode
- **Goal system** — Create goals via chat or Discord; Archi decomposes them into tasks and executes them autonomously
- **Discord bot** — DM or @mention Archi in your server for real-time interaction
- **Web chat** — Browser-based chat interface with WebSocket, typing indicators, and live progress updates during multi-step tasks
- **Web dashboard** — System health, costs, goals, and dream cycle status at a glance
- **Desktop automation** — Mouse, keyboard, and screenshot control via pyautogui with vision-guided click targeting
- **Browser automation** — Playwright-based web navigation and interaction
- **Memory system** — Three-tier memory: short-term (in-memory), working (SQLite), long-term (LanceDB vectors)
- **Safety controls** — Protected files, blocked commands, budget enforcement, workspace isolation, git-backed rollback for source modifications
- **Cost tracking** — Per-request cost logging, daily/monthly budget limits, local vs API usage breakdown
- **Image generation** — Local SDXL text-to-image (optional, requires separate install)
- **Free web search** — DuckDuckGo search via local model, no API key needed
- **Learning system** — Records experiences, extracts patterns, generates improvement suggestions

## Quick Start

### Prerequisites

- Python 3.10+
- 16GB+ RAM recommended (for local model)
- NVIDIA GPU recommended (for fast local inference and CUDA); CPU-only works but is slower
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

**Required:** `OPENROUTER_API_KEY` — get one at [openrouter.ai/keys](https://openrouter.ai/keys). This is the fallback for when the local model can't handle a request.

**Optional but recommended:**
- `LOCAL_MODEL_PATH` — path to your Qwen3VL GGUF model file (see [Local Model Setup](#local-model-setup))
- `DISCORD_BOT_TOKEN` — for Discord integration (see [Discord Bot](#discord-bot))
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

Archi uses **Qwen3VL-8B** (vision + text) via llama.cpp for free local inference. Two model files go in the `models/` directory:

- `Qwen3VL-8B-Instruct-Q4_K_M.gguf` (main model)
- `mmproj-Qwen3VL-8B-Instruct-F16.gguf` (vision encoder, auto-detected)

Download them with: `python scripts/install.py models`

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

This runs CUDA diagnostics and builds llama-cpp-python with GPU support. Build takes 20-40 minutes. After building, ensure the CUDA runtime DLLs are on PATH (the build script will guide you).

### CUDA environment

Set these in `.env` or your system environment if CUDA isn't auto-detected:

```bash
CUDA_PATH=C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.1
```

The agent auto-discovers CUDA via Windows registry and standard install paths. You can verify with: `python scripts/fix.py diagnose`

### Context window sizing

Set `ARCHI_CONTEXT_SIZE` in `.env` to control VRAM usage:

| VRAM | Recommended | Setting |
|------|------------|---------|
| 8GB+ | 32K tokens | `ARCHI_CONTEXT_SIZE=32768` (default) |
| 6GB | 16K tokens | `ARCHI_CONTEXT_SIZE=16384` |
| 4GB | 8K tokens | `ARCHI_CONTEXT_SIZE=8192` |

### Running without a local model

Archi works without a local model — it degrades gracefully and routes all requests to OpenRouter API. Set `LOCAL_MODEL_PATH=` (empty) in `.env`. This costs more but requires no GPU or model download.

## Configuration

### .env

Copy `.env.example` to `.env`. Key settings:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for paid model fallback |
| `OPENROUTER_MODEL` | No | Model to use (default: `openrouter/auto`) |
| `LOCAL_MODEL_PATH` | No | Path to local GGUF model file |
| `DISCORD_BOT_TOKEN` | No | Discord bot token for DM/channel interaction |
| `CUDA_PATH` | No | CUDA toolkit root (auto-detected on Windows) |
| `ARCHI_ROOT` | No | Base path for logs, data, workspace (default: repo root) |
| `ARCHI_CONTEXT_SIZE` | No | Context window in tokens (default: 32768) |
| `ARCHI_PREFER_LOCAL_STRICT` | No | Set to `1` to never escalate to API when prefer_local=True |
| `DAILY_BUDGET_USD` | No | Override daily budget (default: from rules.yaml) |

### config/rules.yaml

Safety and operational rules: budget limits, protected files, blocked commands, risk levels for different actions. The daily budget default is $5.00.

### config/archi_identity.yaml

Archi's personality, focus areas, and proactive task definitions. This drives what Archi works on during dream cycles.

### config/heartbeat.yaml

Adaptive sleep timing: command mode (10s for 120s after activity), monitoring mode (60s), deep sleep (600s, max 1800s). Night mode (11PM-6AM) uses 1800s intervals.

## Usage

### Discord Bot

The primary way to interact with Archi. DM the bot or @mention it in a channel.

**Setup:**
1. Create a bot at [Discord Developer Portal](https://discord.com/developers/applications)
2. Bot tab → Add Bot → Copy token
3. Add `DISCORD_BOT_TOKEN=your_token` to `.env`
4. Invite the bot to your server (OAuth2 → URL Generator → bot scope)
5. Start Archi — the Discord bot starts automatically with the service

**What you can do:**
- Chat naturally — Archi responds as a conversational assistant
- Ask questions — uses local model or API depending on complexity
- Give multi-step tasks — "Research the best thermal paste for my CPU and write a report" triggers multi-step PlanExecutor
- Create goals — `/goal <description>` or just describe what you want
- Check status — `/status`, `/cost`, `/goals`, `/help`
- Request files — "Create a Python script that..." triggers the coding fast-path
- Get progress updates — multi-step tasks show live progress ("Step 3/12: Searching...")
- Receive notifications — dream cycle completions, morning reports, goal updates

### Web Chat

Browser-based chat at **http://127.0.0.1:5001/chat** (runs automatically with the full service).

Same capabilities as Discord: real-time WebSocket messaging, typing indicator, cost display, action execution, goal creation.

**Standalone (for testing):** `python scripts/start.py web` — but use the full service for correct identity and model routing.

### CLI Chat

Terminal-based chat: `python scripts/start.py chat`

Commands: `/help`, `/goal <desc>`, `/goals`, `/status`, `/cost`, `/clear`, `/exit`

### Web Dashboard

System overview at **http://127.0.0.1:5000** (runs automatically with the full service).

Shows system health, resource usage, cost tracking, goals/tasks progress, dream cycle status, and model availability. Auto-refreshes every 10 seconds.

**Standalone:** `python scripts/start.py dashboard`

**API endpoints:** `GET /api/health`, `/api/costs`, `/api/performance`, `/api/goals`, `/api/dream`, `POST /api/goals/create`

## Scripts

Four consolidated scripts handle all operations. Each has an interactive menu and accepts subcommands.

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

`scripts/startup_archi.bat` is a portable wrapper that sets `ARCHI_ROOT` from its own location and runs `start.py watchdog`. Use `python scripts/install.py autostart` to configure it, or set it up manually in Task Scheduler.

## How Archi Works

### Chat Mode

When you send a message, it flows through `action_executor.py`:

1. **Fast paths** (no model call, $0): greetings, time questions, slash commands, file listings, work status queries
2. **Multi-step routing**: research, analysis, and multi-part requests go to PlanExecutor (up to 12 steps in chat, 30 for coding)
3. **Single-shot intent**: simple requests get a single model call to determine action (chat, search, create file, click, etc.)

The model router tries the local model first. In chat mode, it always uses local (user is waiting — speed matters). In dream/plan mode, it can escalate to OpenRouter API based on prompt complexity.

### Dream Mode

When Archi detects 5 minutes of inactivity, it enters a dream cycle:

1. **Morning report** (6-9 AM, once/day) — compiles overnight results
2. **Brainstorming** (night hours, once/24h) — generates new ideas
3. **Task execution** — processes goal queue via PlanExecutor (10 min cap, $0.50/cycle cap, 50 task cap)
4. **History review** (every 3rd cycle) — learning from past actions
5. **Future planning** — creates goals from identity config
6. **Synthesis** (every 10th cycle) — finds cross-goal themes

Dream cycles are interruptible — any user activity stops the cycle immediately.

### Goal System

Goals are created via chat, Discord commands, or autonomously during dream cycles. Each goal gets decomposed into 2-4 tasks by the AI, then tasks are executed one at a time through PlanExecutor.

PlanExecutor supports: web search, webpage fetching, file creation/reading/editing, Python execution, shell commands, and a "think" action for reasoning steps. It has crash recovery (state saved after each step) and self-verification (reads back created files and rates quality).

### Memory

Three tiers:
- **Short-term** — last 50 actions, in-memory (cleared on restart)
- **Working** — SQLite at `data/memory.db` (persistent)
- **Long-term** — LanceDB vector embeddings at `data/vectors/` (semantic search)

### Model Routing

```
Request arrives
  ├─ Cache hit? → return cached ($0)
  ├─ Chat mode → always use local model (speed priority)
  └─ Dream/plan mode → classify complexity
      ├─ Simple/medium → try local, escalate if low confidence
      └─ Complex → OpenRouter API
```

Available models:
- **Local:** Qwen3VL-8B (free, text + vision)
- **OpenRouter:** configurable — Grok, DeepSeek, auto-routed, or any OpenRouter-supported model

### Cost Optimization

Most interactions cost $0 through the local model. OpenRouter API is used only when needed. Budget enforcement stops API calls if daily ($5) or monthly ($100) limits are reached.

Typical daily cost: $0.01-0.10 depending on dream cycle activity and task complexity.

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
│   │   ├── local_model.py     # Qwen3VL-8B via llama.cpp
│   │   ├── openrouter_client.py  # OpenRouter API client
│   │   └── cache.py           # Query cache with LRU eviction
│   ├── tools/
│   │   ├── tool_registry.py   # Tool dispatch with circuit breakers
│   │   ├── image_gen.py       # SDXL local image generation
│   │   ├── desktop_control.py # pyautogui: click, type, screenshot
│   │   ├── browser_control.py # Playwright: navigate, click, fill
│   │   └── computer_use.py    # Vision-guided orchestrator (cache → local → API)
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

Archi has multiple safety layers:

- **Protected files** — core system files (plan_executor.py, safety_controller.py, rules.yaml, etc.) cannot be modified by autonomous actions
- **Blocked commands** — rm -rf, format, shutdown, reboot, fork bombs, registry edits, etc.
- **Budget enforcement** — hard stop at daily/monthly API cost limits
- **Workspace isolation** — file operations restricted to the workspace directory
- **Git safety** — automatic checkpoints before source modifications, syntax check after, rollback on failure
- **Risk levels** — actions classified by risk (L1 low → L4 critical), with different authorization requirements

## Deployment

### Linux (systemd)

An `archi.service` unit file is included. Edit it with your paths:

```bash
sudo cp archi.service /etc/systemd/system/
# Edit: User, WorkingDirectory, ExecStart paths
sudo systemctl daemon-reload
sudo systemctl enable archi
sudo systemctl start archi
```

### Windows (NSSM)

Use NSSM to run Archi as a Windows service:

```powershell
choco install nssm
python scripts/install.py autostart
nssm start ArchiAgent
```

Or use `scripts/startup_archi.bat` with Task Scheduler for a simpler approach.

### Security notes

- Dashboard and web chat bind to localhost only by default
- Use SSH tunneling or VPN for remote access — never expose ports directly
- Store API keys in `.env` (not committed to git via whitelist-based .gitignore)

## Troubleshooting

**Run diagnostics:** `python scripts/fix.py diagnose` — checks environment, models, CUDA, API keys, ports, and system health.

**Run tests:** `python scripts/fix.py test` or `python -m pytest tests/ -v`

### Common issues

**Archi won't start:** Check that dependencies are installed (`scripts/install.py deps`), local model is downloaded (`scripts/install.py models`), and ports 5000/5001 are free (`scripts/stop.py ports`).

**CUDA errors:** Run `scripts/install.py cuda` for diagnostics. If CUDA crashes persist, use `scripts/start.py watchdog` for auto-restart, or set `ARCHI_SKIP_LEARNING=1` to reduce GPU load.

**Budget exceeded / API calls blocked:** Check spend with `scripts/fix.py diagnose` or the `/cost` chat command. Increase the budget in `config/rules.yaml` or clear tracking with `scripts/reset.py`.

**Port conflict:** Only one process can use each port. If web chat works when Archi is stopped, a standalone instance is still running — use `scripts/stop.py` to kill it.

**Dashboard shows "Not Initialized":** The dashboard needs the full service running, not standalone mode. Use `scripts/start.py` (not `scripts/start.py dashboard`).

### Logs

| Log | Location | Contents |
|-----|----------|----------|
| Conversations | `logs/conversations.jsonl` | Every user↔Archi exchange with timestamp, source, action, cost |
| Chat trace | `logs/chat_trace.log` | Detailed chat flow: intent parsing, model selection, routing |
| Daily actions | `logs/actions/YYYY-MM-DD.jsonl` | Per-day action log (heartbeats, tasks, system events) |
| Dream log | `data/dream_log.jsonl` | Dream cycle summaries (tasks, duration, files, insights) |
| Goal state | `data/goals_state.json` | All goals and tasks with full lifecycle |

## Development

### Running tests

```bash
python -m pytest tests/ -v              # all tests
python -m pytest tests/unit/ -v         # unit tests only
python -m pytest tests/ -k router -v    # specific tests by keyword
```

31 test files across unit, integration, and functional test directories.

### Adding tools

Create a new tool class in `src/tools/` and register it in `tool_registry.py`. Tools are wrapped with circuit breakers for resilience.

### Adding models

Configure new OpenRouter models in `.env` via `OPENROUTER_MODEL`. For local models, update the model path and ensure llama-cpp-python supports the architecture.

---

**Issues:** [github.com/koorbmeh/Archi/issues](https://github.com/koorbmeh/Archi/issues)
