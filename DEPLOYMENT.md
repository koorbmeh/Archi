# Archi Deployment Guide

Production deployment instructions.

## Prerequisites

- Python 3.10+
- 16GB+ RAM (for local AI model)
- NVIDIA GPU (optional, recommended)
- Linux/Windows Server

## Production Setup

### 1. System Preparation

**Linux:**
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.10+
sudo apt install python3.10 python3.10-venv python3-pip

# Install NVIDIA drivers (if GPU)
sudo apt install nvidia-driver-535
```

**Windows Server:**
- Install Python 3.10+ from python.org
- Install Visual C++ Redistributable
- Install NVIDIA drivers (if GPU)

### 2. Application Setup

```bash
# Clone repository
git clone https://github.com/koorbmeh/Archi.git
cd Archi

# Create virtual environment
python3.10 -m venv venv
source venv/bin/activate  # Linux
# .\venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Download AI model
# See MISSION_CONTROL.md for model setup
```

### 3. Configuration

**Create `.env`:**
```bash
# Optional: Grok API key
GROK_API_KEY=your_key_here

# Optional: Custom settings
ARCHI_LOG_LEVEL=INFO
ARCHI_DASHBOARD_PORT=5000
```

**Configure `config/rules.yaml`:**
```yaml
- name: "budget_hard_stop"
  value: 10.00  # Production budget
  enabled: true
```

### 4. Service Installation

**Linux (systemd):**
```bash
# Edit archi.service
sudo nano archi.service

# Update paths:
# - User=YOUR_USERNAME
# - WorkingDirectory=/path/to/Archi
# - ExecStart=/path/to/Archi/venv/bin/python scripts/start_archi.py

# Install
sudo cp archi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable archi
sudo systemctl start archi

# Check status
sudo systemctl status archi

# View logs
sudo journalctl -u archi -f
```

**Windows (NSSM):**
```powershell
# Install NSSM
choco install nssm

# Edit paths in install_windows_service.ps1
# Then run as Administrator:
.\scripts\install_windows_service.ps1

# Start service
nssm start ArchiAgent

# Check status
nssm status ArchiAgent
```

## Monitoring

### Dashboard Access

**Expose dashboard:**
```bash
# Option 1: SSH tunnel (recommended)
ssh -L 5000:localhost:5000 user@server

# Option 2: Nginx reverse proxy
# Configure nginx to proxy to localhost:5000
```

### Log Monitoring

```bash
# Real-time logs
tail -f logs/archi_service.log

# Search logs
grep "ERROR" logs/archi_service.log

# Rotate logs (add to cron)
0 0 * * * find /path/to/Archi/logs -name "*.log" -mtime +30 -delete
```

### Health Checks

```bash
# Add to monitoring system
curl http://localhost:5000/api/health

# Expected: {"overall_status": "healthy", ...}
```

## Security

### Network

- Dashboard on localhost only (default)
- Use SSH tunneling or VPN for remote access
- Never expose port 5000 directly to internet

### API Keys

- Store in `.env` (not committed to git)
- Rotate keys periodically
- Use read-only keys where possible

### File Permissions

```bash
# Restrict access
chmod 600 .env
chmod 700 data/
chmod 700 logs/
```

## Backup

### Critical Files

```bash
# Backup directory
data/
  ├── goals_state.json       # Goal progress
  ├── cost_usage.json        # Cost tracking
  ├── experiences.json       # Learning data
  └── ui_memory.db          # UI cache

# Backup command
tar -czf archi-backup-$(date +%Y%m%d).tar.gz data/ config/ .env
```

### Automated Backups

```bash
# Add to cron (daily at 2 AM)
0 2 * * * cd /path/to/Archi && tar -czf backup-$(date +\%Y\%m\%d).tar.gz data/ config/ .env
```

## Updates

```bash
# Stop service
sudo systemctl stop archi

# Pull updates
git pull origin main

# Update dependencies
source venv/bin/activate
pip install -r requirements.txt --upgrade

# Restart service
sudo systemctl start archi
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
sudo journalctl -u archi -n 50

# Common issues:
# - Missing .env file
# - Model not downloaded
# - Port 5000 in use
# - Permission errors
```

### High Resource Usage

```bash
# Monitor resources
htop

# Adjust cache size in src/models/cache.py
# Restart service after changes
```

## Scaling

For high-load scenarios:

1. **Separate components:**
   - Run dashboard on different server
   - Use Redis for caching
   - External database for goals

2. **Load balancing:**
   - Multiple Archi instances
   - Shared database
   - Task queue (Celery)

3. **Cost optimization:**
   - Dedicated GPU server
   - Batch processing
   - Aggressive caching

---

For support: https://github.com/koorbmeh/Archi/issues
