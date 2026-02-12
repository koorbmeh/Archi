# Archi Scripts

## Daily Use
- **`start_archi.py`** — Main entry point. Start Archi service.
- **`chat.py`** — CLI chat interface.
- **`restart_archi.ps1`** / **`restart_archi.bat`** — Restart Archi service.
- **`clear_cache.py`** — Clear query cache.

## Development
- **`run_web_chat.py`** — Run web chat standalone (for development).
- **`run_dashboard.py`** — Run dashboard standalone (for development).
- **`run_discord_bot.py`** — Run Discord bot standalone (for development).
- **`check_grok.py`** — Verify Grok API connection.

## Setup (One-Time)
- **`download_model.py`** — Download Qwen3-VL model.
- **`download_vision_model.py`** — Download vision projection model.
- **`build_llama_cuda.bat`** — Build llama-cpp with CUDA (Windows).
- **`install_windows_service.ps1`** — Install as Windows service (requires NSSM).

## Debugging (As Needed)
- **`diagnose_cuda.py`** — Diagnose CUDA/GPU issues.
- **`run_test_cuda.bat`** — Test CUDA setup.

## Archived (Rarely Run)
- **`verify_gate_a.ps1`** — Gate A safety verification (development phase).

## Tests
Test scripts have been moved to `tests/`:
- Unit tests: `tests/unit/`
- Integration tests: `tests/integration/`
- Component scripts: `tests/scripts/`

Run tests with: `pytest` or `python tests/scripts/test_*.py`
