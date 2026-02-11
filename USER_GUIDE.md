# Archi User Guide

Complete guide to using Archi, your autonomous AI agent.

## Table of Contents

- [Quick Start](#quick-start)
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
   # Edit .env with your API keys (optional)
   ```

### First Run

**Start Archi:**
```bash
python scripts/start_archi.py
```

**Access dashboard:**
- Open browser to: http://127.0.0.1:5000
- Monitor status, costs, and activity

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
Layer 3: Grok API (~$0.0001, when needed)
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

### Via Web Dashboard

Create goals via `POST /api/goals/create` (see API_REFERENCE.md).

---

## Monitoring

### Dashboard

**Access:** http://127.0.0.1:5000

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
python scripts/test_health_check.py
```

**Shows detailed status of all components**

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
4. Ports available? (5000 for dashboard)

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
python scripts/start_archi.py
```

### Budget Exceeded

**Symptom:** Grok API calls blocked

**Check current spend:**
```bash
python scripts/test_cost_tracking.py
```

**Solutions:**
1. Increase budget in `config/rules.yaml`
2. Use local model more (configure router)
3. Clear old tracking: `rm data/cost_usage.json`

### High Memory Usage

**Monitor:**
```bash
python scripts/test_health_check.py
```

**Fixes:**
1. Reduce cache size in `src/models/cache.py`
2. Clear old cache: `rm -r data/cache/`
3. Restart service

### Dream Cycles Not Running

**Check:**
1. Idle threshold met? (default 5 minutes)
2. Service running? (`python scripts/start_archi.py`)
3. Goals queued? (check dashboard)

**Force dream cycle (test):**
```python
from src.core.dream_cycle import DreamCycle

dream = DreamCycle(idle_threshold_seconds=5)
dream.start_monitoring()
# Wait 5 seconds of inactivity
```

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

# Install service
.\scripts\install_windows_service.ps1

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
