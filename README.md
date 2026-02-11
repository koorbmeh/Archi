# Archi

Autonomous AI agent with safety controls, workspace isolation, and optional local LLM (llama.cpp/CUDA).

- **Gate A:** Core loop, adaptive heartbeat, path safety, tool execution (read/write in workspace).
- **Gate B:** Local model (Forge + Qwen3-VL/llama-cpp-python), memory (LanceDB + short-term/working), optional API routing (Phase 3).
- **Gate C:** Computer use — desktop control (pyautogui), browser automation (Playwright), UI memory cache, local + Grok vision orchestration.
- **Gate D:** Proactive autonomy — dream cycle engine, goal decomposition, autonomous execution, self-improvement (learning system).

## Quick start

1. Clone and enter the repo.
2. Create a venv and install deps:  
   `py -m venv venv` then  
   `.\venv\Scripts\pip.exe install -r requirements.txt`
3. Run the agent:  
   `.\venv\Scripts\python.exe -m src.core.agent_loop`

Full setup (workspace, Gate B local model, CUDA build) → **[RUN.md](RUN.md)**  
Current status and gates → **[MISSION_CONTROL.md](MISSION_CONTROL.md)**

## Files not tracked (by design)

- **`.env`** – Copy from `.env.example` and fill in (secrets, `LOCAL_MODEL_PATH`, optional `CUDA_PATH`).
- **`venv/`** – Recreate with the steps above.
- **`models/`** – GGUF files; use `scripts/download_model.py` after setup.
- **`data/`** – LanceDB vectors and SQLite working memory (created at runtime).
- **`Archi Plan v2.0 - Final Production.txt`** – Local planning doc; not in the repo.
- Script debug artifacts (`scripts/nmake_where.txt`, `path_debug.txt`) – Ignored.
