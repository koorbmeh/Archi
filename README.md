# Archi

Autonomous AI agent with safety controls, git-backed self-modification, and optional local LLM (llama.cpp/CUDA).

- **Gate A:** Core loop, adaptive heartbeat, path safety, tool execution.
- **Gate B:** Local model (Forge + Qwen3-VL/llama-cpp-python), memory (LanceDB + short-term/working), optional API routing (Phase 3).
- **Gate C:** Computer use — desktop control (pyautogui), browser automation (Playwright), UI memory cache, local + OpenRouter vision orchestration.
- **Gate D:** Proactive autonomy — dream cycle engine, goal decomposition, autonomous execution, self-improvement (learning system).

## Quick start

1. Clone and enter the repo.
2. Create a venv and install deps:  
   `py -m venv venv` then  
   `.\venv\Scripts\pip.exe install -r requirements.txt`
3. Run the agent:  
   `.\venv\Scripts\python.exe scripts\start.py`  
   (or `.\venv\Scripts\python.exe -m src.core.agent_loop` for the raw agent loop)

Full setup (workspace, Gate B local model, CUDA build) → **[RUN.md](RUN.md)**  
Current status and gates → **[MISSION_CONTROL.md](MISSION_CONTROL.md)**

## Setup (create from examples)

- **`.env`** – Copy from `.env.example` and fill in. Required: `OPENROUTER_API_KEY` (get at https://openrouter.ai/keys). Optional: `LOCAL_MODEL_PATH`, `CUDA_PATH`.
- **`venv/`** – Recreate with the steps above.
- **`models/`** – GGUF files; use `scripts/install.py models` after setup.
- **`config/archi_identity.yaml`** – Copy from `config/archi_identity.example.yaml` and customize.
- **`config/prime_directive.txt`** – Copy from `config/prime_directive.example.txt` and customize.
- **`data/`**, **`logs/`** – Created at runtime.
