# Archi Scripts

Five scripts handle everything. Each has an interactive menu and accepts subcommands.

| Script | Purpose | Example |
|--------|---------|---------|
| `install.py` | Dependencies, models, CUDA, image gen, voice, auto-start | `python scripts/install.py deps` |
| `start.py` | Launch service, discord bot, or watchdog | `python scripts/start.py` |
| `fix.py` | Diagnostics, tests, cache cleanup, state repair | `python scripts/fix.py diagnose` |
| `stop.py` | Stop processes, restart | `python scripts/stop.py restart` |
| `reset.py` | Factory reset (clear runtime state, keep config) | `python scripts/reset.py --yes` |

Shared utilities live in `_common.py` (cross-platform venv detection, header/run helpers).

## install.py — Setup & Installation

```
python scripts/install.py              # interactive menu
python scripts/install.py deps          # install requirements.txt
python scripts/install.py models        # download AI models
python scripts/install.py voice         # install voice (STT+TTS)
python scripts/install.py imagegen      # install image gen (diffusers + SDXL)
python scripts/install.py cuda          # CUDA diagnostics & build
python scripts/install.py autostart     # Windows auto-start setup
python scripts/install.py all           # everything
```

## start.py — Launch Archi

```
python scripts/start.py                 # interactive menu
python scripts/start.py service         # full agent (agent loop + discord)
python scripts/start.py discord         # Discord bot only
python scripts/start.py watchdog        # service with auto-restart on crash
```

Includes PID lock (`data/archi.pid`) to prevent multiple instances.

## fix.py — Diagnostics & Repair

```
python scripts/fix.py                   # interactive menu
python scripts/fix.py diagnose          # env, models, CUDA, API, imports
python scripts/fix.py test              # run pytest suite
python scripts/fix.py clean             # clear caches & temp files
python scripts/fix.py state             # repair goals, dirs, databases
```

## stop.py — Stop & Restart

```
python scripts/stop.py                  # stop everything (default)
python scripts/stop.py restart          # stop + start in new window
```

## reset.py — Factory Reset

```
python scripts/reset.py                 # interactive confirmation
python scripts/reset.py --yes           # skip confirmation
```

Clears logs, caches, goals, databases, and generated workspace content while preserving source code, config, and user project files.
