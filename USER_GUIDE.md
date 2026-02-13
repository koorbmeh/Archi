# Archi User Guide

Complete guide to using Archi, your autonomous AI agent.

## Table of Contents

- [Quick Start](#quick-start)
- [Interacting with Archi](#interacting-with-archi)
- [Core Concepts](#core-concepts)
- [Setting Goals](#setting-goals)
- [Monitoring](#monitoring)
- [Cost Management](#cost-management)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

### Installation

1. **Clone repository:**
   ```bash
   git clone https://github.com/koorbmeh/Archi.git
   cd Archi
   ```

2. **Create virtual environment:**
   ```bash
   python -m venv venv
   # Windows:
   .\venv\Scripts\activate
   # Linux/Mac:
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env: OPENROUTER_API_KEY required (get at https://openrouter.ai/keys)
   # Optional: LOCAL_MODEL_PATH for local AI, GROK_API_KEY deprecated (use OpenRouter)
   ```

### First Run

**Start Archi:**
```bash
python scripts/start.py
# or: python scripts/start.py service
```

**Access dashboard:**
- Open browser to: http://127.0.0.1:5000
- Monitor status, costs, and activity

**Access web chat:**
- Open browser to: http://127.0.0.1:5001/chat
- Chat with Archi in real time

---

## Interacting with Archi

Archi offers two ways to interact: **CLI chat** (terminal) and **Web chat** (browser).

### CLI Chat

**Start:**
```bash
python scripts/start.py chat
```

**Commands:**

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/goal <description>` | Create a goal for dream cycles |
| `/goals` | List goals and progress |
| `/status` | Show system health |
| `/cost` | Show cost summary |
| `/clear` | Clear screen |
| `/exit` or `/quit` | Exit chat |

**Example:**
```
You: Create a file called notes.txt in the workspace with content "My notes"
Archi: Done! I created the file at .../workspace/notes.txt
  [OK] Created file: .../workspace/notes.txt
```

### Web Chat

**CRITICAL: Use the full service for Archi (local model, correct identity):**

```bash
# STOP any standalone web chat first (Ctrl+C in that terminal)
python scripts/start.py
# or: python scripts/start.py service
```
Then open **http://127.0.0.1:5001/chat**

**Why?** If the web chat runs standalone for hours, it uses code from when it started. It may route to OpenRouter and say a model name instead of "I'm Archi." The full service loads the latest code and uses the local model + Archi identity.

**Standalone (for testing only):**
```bash
python scripts/start.py web
```
Then open http://127.0.0.1:5001/chat — **restart this script after any code changes.**

**Port conflict:** Only one process can use port 5001. If the chat works when Archi is stopped, a standalone web chat is still running — stop it with `python scripts/stop.py`, then use `scripts/start.py`.

**Features:**
- Real-time messaging (WebSocket)
- Typing indicator
- Cost display (today + session)
- Same action execution as CLI (create files in workspace)
- Goal creation (when run with full service)

### What Archi Can Do Via Chat

- **Answer questions** — Archi identifies as Archi (not Grok)
- **Create files** — e.g. "Create workspace/hello.txt with content Hello from Archi!"
- **Set goals** — Use `/goal` (CLI) or create_goal (Web when service running)
- **Check status** — `/status` (CLI) or dashboard

**Note:** File creation is limited to the workspace (enforced by safety rules).

### Discord Bot

**Chat with Archi from Discord** (DMs or @mention in channels). Starts automatically with Archi when configured.

1. Create a bot at [Discord Developer Portal](https://discord.com/developers/applications)
2. Bot tab → Add Bot → Copy token
3. Add to `.env`: `DISCORD_BOT_TOKEN=your_token`
4. Invite the bot to your server (OAuth2 → URL Generator → bot scope)
5. Start Archi: `python scripts/start.py` — the Discord bot starts automatically

**Usage:**
- **DM the bot:** Any message gets a response from Archi
- **In channels:** `@Archi create a file called notes.txt` to get a response

Same capabilities as web chat: file creation, Q&A, cost tracking. Runs alongside web chat and dashboard.

**Standalone** (without full Archi): `python scripts/start.py discord` — useful for testing.

### Restarting Archi

**Full restart (kills everything and starts fresh):**
```powershell
python scripts/stop.py restart
```
This stops any running Archi processes (web chat, dashboard, service) and frees ports 5000/5001, then starts the full service in a new window.

**Manual restart:**
1. Press `Ctrl+C` in the terminal to stop
2. Run again: `python scripts/start.py`

**Windows service (NSSM):**
```powershell
nssm restart ArchiAgent
```

**Linux (systemd):**
```bash
sudo systemctl restart archi
```

---

## Core Concepts

### Dream Cycles

Archi works autonomously through **dream cycles** - background processing when idle.

**How it works:**
1. System detects 5 minutes of inactivity
2. Dream cycle starts automatically
3. Processes queued tasks
4. Reviews past actions (learning)
5. Plans future work
6. Continues until activity detected

**Dream cycles are:**
- Automatic (no user action needed)
- Interruptible (stops when you interact)
- Cost-optimized (uses local model when possible)

### Goal Decomposition

Archi breaks complex goals into actionable tasks:

```
Goal: "Build a budget tracker"
  ↓
Tasks:
  1. Research Python budget libraries
  2. Design data structure
  3. Implement CSV import
  4. Create expense categorization
  5. Build monthly report generator
```

### Cost Optimization

Multi-layer approach minimizes expenses:

```
Layer 1: Cache (FREE, instant)
Layer 2: Local AI (FREE, 2-3 seconds)
Layer 3: OpenRouter API (~$0.0001, when needed)
```

**Result: 99.9% cost reduction vs pure API**

---

## Setting Goals

### Via Python API

```python
from src.core.goal_manager import GoalManager

manager = GoalManager()

# Create goal
goal = manager.create_goal(
    description="Analyze sales data and create monthly report",
    user_intent="I need insights into Q4 sales trends",
    priority=8  # 1-10, higher = more urgent
)

# Decompose into tasks (uses AI)
from src.models.local_model import LocalModel
model = LocalModel()
tasks = manager.decompose_goal(goal.goal_id, model)

# Tasks will be executed during dream cycles
```

### Via CLI Chat

```bash
python scripts/start.py chat
/goal Analyze sales data and create monthly report
```

### Via Web Chat

When running with the full service, use the `create_goal` socket event (see API_REFERENCE.md).

### Via Dashboard API

Create goals via `POST /api/goals/create` (see API_REFERENCE.md).

---

## Monitoring

### Dashboard

**Access:** http://127.0.0.1:5000

**Start (with full service):**
```bash
python scripts/start.py
```

**Start (standalone):**
```bash
python scripts/start.py dashboard
```

**Shows:**
- System health (healthy/degraded/unhealthy)
- Resource usage (CPU, memory, disk)
- Cost tracking (daily spend vs budget)
- Goals and tasks progress
- Dream cycle status
- AI model availability

**Auto-refreshes every 10 seconds**

### Logs

**Location:** `logs/archi_service.log`

**Tail logs:**
```bash
# Windows (PowerShell):
Get-Content logs\archi_service.log -Wait -Tail 50

# Linux/Mac:
tail -f logs/archi_service.log
```

### Health Check

**Command line:**
```bash
python scripts/fix.py diagnose
```

**Shows detailed status of all components (env, models, CUDA, API, ports)**

---

## Cost Management

### Budget Limits

**Configure in `config/rules.yaml`:**
```yaml
- name: "budget_hard_stop"
  value: 5.00  # Daily limit in USD
  enabled: true
```

### Cost Tracking

**View costs:**
```python
from src.monitoring.cost_tracker import get_cost_tracker

tracker = get_cost_tracker()

# Today's summary
summary = tracker.get_summary('today')
print(f"Spent: ${summary['total_cost']:.4f}")
print(f"Budget: ${summary['budget']:.2f}")

# Get recommendations
recommendations = tracker.get_recommendations()
for rec in recommendations:
    print(f"- {rec}")
```

### Cost Optimization Tips

1. **Use local model when possible** (free)
2. **Enable caching** (responses cached 1 hour default)
3. **Set appropriate budgets** (prevents surprises)
4. **Monitor regularly** (dashboard or logs)

---

## Troubleshooting

### Archi Won't Start

**Check:**
1. Virtual environment activated?
2. Dependencies installed? (`pip install -r requirements.txt`)
3. Local model downloaded? (see MISSION_CONTROL.md)
4. Ports available? (5000 dashboard, 5001 web chat)

**Common fixes:**
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall

# Check Python version (3.10+ required)
python --version

# Verify CUDA (for GPU)
python -c "import torch; print(torch.cuda.is_available())"
```

### "Not Initialized" in Dashboard

**Cause:** Dashboard running standalone without service

**Fix:** Start full service:
```bash
python scripts/start.py
```

### Budget Exceeded

**Symptom:** OpenRouter API calls blocked

**Check current spend:**
```bash
python scripts/fix.py diagnose
# or run cost-related tests: python -m pytest tests/ -k cost -v
```

**Solutions:**
1. Increase budget in `config/rules.yaml`
2. Use local model more (configure router)
3. Clear old tracking: `rm data/cost_usage.json`

### High Memory Usage

**Monitor:**
```bash
python scripts/fix.py diagnose
# or: python -m pytest tests/ -k health -v
```

**Fixes:**
1. Reduce cache size in `src/models/cache.py`
2. Clear old cache: `rm -r data/cache/`
3. Restart service

### Dream Cycles Not Running

**Check:**
1. Idle threshold met? (default 5 minutes)
2. Service running? (`python scripts/start.py`)
3. Goals queued? (check dashboard)

**Force dream cycle (test):**
```python
from src.core.dream_cycle import DreamCycle

dream = DreamCycle(idle_threshold_seconds=5)
dream.start_monitoring()
# Wait 5 seconds of inactivity
```

### Conversation Logs for Troubleshooting

When debugging chat issues (memory, wrong answers, etc.), check these logs:

| Log | Location | Contents |
|-----|----------|----------|
| **Conversation log** | `logs/conversations.jsonl` | Each exchange (user message, Archi response, source, action type). One JSON object per line. |
| **Chat trace** | `logs/chat_trace.log` | Flow trace: user message preview, intent, model used, response preview. |
| **Persisted history** | `data/web_chat_history.json` | Full conversation history (used for context across restarts). |

**Example** – read last 10 exchanges:
```bash
tail -n 10 logs/conversations.jsonl
```

Each line in `conversations.jsonl` has: `ts`, `source` (web/discord/cli), `user`, `response`, `action`, `cost_usd`.

---

## Advanced

### Running as Service

**Linux (systemd):**
```bash
sudo cp archi.service /etc/systemd/system/
sudo systemctl enable archi
sudo systemctl start archi
```

**Windows (NSSM):**
```powershell
# Install NSSM
choco install nssm

# Install service (legacy script in scripts/_archive/)
# Or use: python scripts/install.py autostart
.\scripts\_archive\install_windows_service.ps1

# Start
nssm start ArchiAgent
```

### Custom Models

**Add to `src/models/router.py`:**
```python
# Your custom model
from your_package import YourModel

class ModelRouter:
    def __init__(self):
        self.custom = YourModel()
        # Add to routing logic
```

### Extending Capabilities

**Add new tools in `src/tools/`:**
```python
# src/tools/my_tool.py
class MyTool:
    def execute(self, params):
        # Your implementation
        pass
```

---

## Support

**Issues:** https://github.com/koorbmeh/Archi/issues

**Documentation:** See MISSION_CONTROL.md

**License:** MIT
