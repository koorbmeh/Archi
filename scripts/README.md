# Archi Scripts

Four consolidated scripts handle everything. Each has an interactive menu
and also accepts subcommands for quick one-liners.

## Quick Reference

| Script | Purpose | Example |
|--------|---------|---------|
| `install.py` | Dependencies, models, CUDA, voice, auto-start | `scripts\install.py models` |
| `start.py` | Launch service, chat, web, dashboard, discord | `scripts\start.py` |
| `fix.py` | Diagnostics, tests, cache cleanup, state repair | `scripts\fix.py diagnose` |
| `stop.py` | Stop processes, free ports, restart | `scripts\stop.py restart` |

## install.py — Setup & Installation

```
.\venv\Scripts\python.exe scripts\install.py              # interactive menu
.\venv\Scripts\python.exe scripts\install.py deps          # install requirements.txt
.\venv\Scripts\python.exe scripts\install.py models        # download AI models
.\venv\Scripts\python.exe scripts\install.py voice         # install voice (STT+TTS)
.\venv\Scripts\python.exe scripts\install.py cuda          # CUDA diagnostics & build
.\venv\Scripts\python.exe scripts\install.py autostart     # Windows auto-start setup
.\venv\Scripts\python.exe scripts\install.py all           # everything
```

## start.py — Launch Archi

```
.\venv\Scripts\python.exe scripts\start.py                 # interactive menu
.\venv\Scripts\python.exe scripts\start.py service         # full service (default)
.\venv\Scripts\python.exe scripts\start.py chat            # CLI terminal chat
.\venv\Scripts\python.exe scripts\start.py web             # web chat (port 5001)
.\venv\Scripts\python.exe scripts\start.py dashboard       # dashboard (port 5000)
.\venv\Scripts\python.exe scripts\start.py discord         # Discord bot
.\venv\Scripts\python.exe scripts\start.py watchdog        # service + auto-restart
```

## fix.py — Diagnostics & Repair

```
.\venv\Scripts\python.exe scripts\fix.py                   # interactive menu
.\venv\Scripts\python.exe scripts\fix.py diagnose          # env, models, CUDA, API, ports
.\venv\Scripts\python.exe scripts\fix.py test              # run pytest suite
.\venv\Scripts\python.exe scripts\fix.py clean             # clear caches & temp files
.\venv\Scripts\python.exe scripts\fix.py state             # repair goals, dirs, databases
```

## stop.py — Stop & Restart

```
.\venv\Scripts\python.exe scripts\stop.py                  # stop everything
.\venv\Scripts\python.exe scripts\stop.py service          # stop service only
.\venv\Scripts\python.exe scripts\stop.py ports            # free ports 5000/5001
.\venv\Scripts\python.exe scripts\stop.py restart          # stop + start in new window
```

## CUDA crash mitigation

If Archi crashes with `CUDA error`, try:
1. Run with watchdog: `scripts\start.py watchdog` — auto-restarts on crash
2. Reduce GPU load: Set `ARCHI_SKIP_LEARNING=1` in .env
3. Close other GPU apps to free VRAM

## Tests

Run tests via fix.py or directly:
```
.\venv\Scripts\python.exe -m pytest tests/ -v
```

## reset.py & clean_slate.py — State Reset

```
.\venv\Scripts\python.exe scripts\reset.py          # factory reset (interactive)
.\venv\Scripts\python.exe scripts\reset.py --yes   # skip confirmation
.\venv\Scripts\python.exe scripts\clean_slate.py   # backup + wipe goals, experiences, etc.
```

`reset.py` clears runtime state (logs, caches, data) while preserving config and workspace.  
`clean_slate.py` creates backups then resets goals, experiences, idea backlog, overnight results.

## Archived Scripts

Previous individual scripts are in `scripts/_archive/` for reference.
They are no longer needed — all functionality is in the four scripts above.
