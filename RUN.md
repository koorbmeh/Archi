# How to Run Archi (Gate A)

## First-time setup

You have Python via `py` (no `python` in PATH). Use the virtual environment so dependencies are installed.

### 1. Create venv and install deps (one-time)

```powershell
cd C:\Repos\Archi
py -m venv venv
.\venv\Scripts\pip.exe install -r requirements.txt
```

(No need to activate if you use the venv Python directly—see below.)

### 2. Workspace and test file (one-time)

Already created: `workspace\test.txt`.  
If you use a different base (e.g. `C:\Archi`), set `$env:ARCHI_ROOT = "C:\Archi"` and create `C:\Archi\workspace\test.txt`.

### 3. Run the agent (no activation required)

**Option A – Use venv Python directly (works even when script execution is disabled):**

```powershell
cd C:\Repos\Archi
.\venv\Scripts\python.exe -m src.core.agent_loop
```

**Option B – If you can run PowerShell scripts (activate then run):**

```powershell
cd C:\Repos\Archi
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser   # one-time, if you want to allow scripts
.\venv\Scripts\Activate.ps1
py -m src.core.agent_loop
```

- **Heartbeat** every ~60 s (logged).
- **Every 5 minutes**: three test actions. You’ll get approval prompts; type `yes` or `no` and Enter.
- **Stop**: Ctrl+C (graceful shutdown).

### 4. If `python` is in PATH later

You can use `python` instead of `py`, and either run with the venv executable or activate first.

## Gate B/C – Local model (Forge + Qwen3-VL)

Archi uses **Forge** (model-agnostic inference) for local LLM. Forge provides `backends/` (llamacpp, hf_transformers), `utils/model_detector.py`, and hardware config. The primary model is **Qwen3VL-8B** (vision + reasoning). Place the model files in `models/`:

- `Qwen3VL-8B-Instruct-Q4_K_M.gguf` (main model)
- `mmproj-Qwen3VL-8B-Instruct-F16.gguf` (vision encoder, auto-detected)

**Qwen3-VL requires the JamePeng fork** of llama-cpp-python (standard releases lack Qwen3VL chat handler):

```powershell
pip uninstall llama-cpp-python -y
pip install llama-cpp-python @ git+https://github.com/jamepeng/llama-cpp-python
```

For **GPU (CUDA)**, build from source with CUDA (see below). For **CPU-only**, the standard wheel works for text, but vision needs the JamePeng fork.

### Legacy: Prebuilt wheel (original section)

Use a **prebuilt wheel** (building from source needs Visual Studio on Windows).

**CPU (recommended for compatibility):** Official 0.3.x wheel — required for Qwen2.5 GGUF to load correctly. Older 0.2.x wheels can fail with `AssertionError`.

```powershell
cd C:\Repos\Archi
.\venv\Scripts\python.exe -m pip install llama-cpp-python --prefer-binary --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --force-reinstall
```

**GPU (CUDA):** Your system has **RTX 5070** and you can install the toolkit with:
`winget install -e --id Nvidia.CUDA --accept-package-agreements`
That installs **CUDA 13.1**. The community **cu122** wheel, however, was built for **CUDA 12.2** and needs the 12.2 runtime DLLs (`cudart64_122.dll`, etc.); 13.1 does not provide them. So for the cu122 wheel to work you must install **CUDA Toolkit 12.2** as well (it can sit alongside 13.1):

1. Download [CUDA 12.2](https://developer.nvidia.com/cuda-12-2-0-download-archive) (Windows → exe local).
2. Install it, then add to PATH: `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.2\bin` (or put it first in PATH when running the test).
3. In a **new** PowerShell (so PATH is updated), switch to the CUDA wheel and test:
   ```powershell
   .\venv\Scripts\python.exe -m pip uninstall llama-cpp-python -y
   .\venv\Scripts\python.exe -m pip install llama-cpp-python --prefer-binary --extra-index-url https://jllllll.github.io/llama-cpp-python-cuBLAS-wheels/AVX2/cu122 --force-reinstall
   .\venv\Scripts\python.exe test_local_model.py
   ```
4. If you get **AssertionError** when loading the model (not DLL), the jllllll wheel is 0.2.26 and can fail on this GGUF; switch back to CPU 0.3.x (command above).

**Build from source (GPU, 0.3.x):** To get both GPU and working Qwen2.5 load, run `scripts\build_llama_cuda.bat` (double‑click or from cmd). Requires **Visual Studio with "Desktop development with C++"** and **CUDA Toolkit 12.4+** (e.g. 13.1; VS 2026’s STL requires 12.4+). The script prefers CUDA 13.1 if present, else 12.2 with a compiler override. Build takes 20–40 min. After a successful build, run the test with CUDA on PATH: **`scripts\run_test_cuda.bat`** (or set `CUDA_PATH` to the toolkit root and ensure `bin\x64` is on PATH so the CUDA runtime DLLs load).

Then install the rest for Gate B and download the model:

```powershell
.\venv\Scripts\python.exe -m pip install python-dotenv huggingface_hub sentence-transformers
.\venv\Scripts\python.exe scripts\download_model.py
.\venv\Scripts\python.exe test_local_model.py
```

## Gate B Phase 2 – Memory (LanceDB)

Memory is initialized automatically when the agent runs (`MemoryManager` + `VectorStore`). Data is stored under `data/` (ignored by git).

- **Vectors (long-term semantic):** `data/vectors/` (LanceDB)
- **Working memory:** `data/memory.db` (SQLite)
- **Short-term:** Last 50 actions in memory (cleared on restart)

**Optional – run memory tests:**
```powershell
.\venv\Scripts\python.exe test_lancedb.py
.\venv\Scripts\python.exe test_vector_store.py
```

## Gate B Phase 3 – Model router (local vs Grok)

Set `GROK_API_KEY` in `.env` (see `.env.example`). Optional: run Grok-only test: `.\venv\Scripts\python.exe test_grok_api.py`.

**CUDA for local model:** The agent and router tests automatically prepend the CUDA runtime to `PATH` when you have the NVIDIA toolkit installed in a standard location (e.g. `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1`). You can run from any shell (PowerShell, IDE, etc.); no need for a special batch. To use a different install, set `CUDA_PATH` in `.env` or the environment.

**Router test:** `.\venv\Scripts\python.exe test_router.py`

**Cache test:** `.\venv\Scripts\python.exe test_cache.py`

**Full system test:** `.\venv\Scripts\python.exe test_full_system.py`

**Agent loop:** `.\venv\Scripts\python.exe -m src.core.agent_loop` (router + one test query at startup; router stats every 100 actions). For local-first routing, ensure CUDA is installed or `CUDA_PATH` is set so the bootstrap can find it.

## Gate C – Computer Use

Computer use provides desktop control (mouse, keyboard, screenshots) and browser automation, with an intelligent orchestration layer:

1. **Cache** — Reuses previously found coordinates (instant, $0).
2. **Known positions** — Common UI (e.g. Windows Start button) uses measured coordinates.
3. **Local vision** (Qwen3-VL) — Tries first, free.
4. **Grok vision** (API) — Fallback when local fails (~$0.0001).
5. **Cache stores result** — Future clicks free.

**Test computer use:**
```powershell
.\venv\Scripts\python.exe scripts\test_computer_use.py
```

Optional flags:
- `--clear-cache` — Clear UI cache for fresh vision test.
- `START_BUTTON_X=843` — Override Start button X (or fraction, e.g. `0.33`).
- `SKIP_GROK=1` — Disable Grok fallback.
- `DEBUG_CLICK=1` — Save annotated screenshot to `data/debug_vision_detection.png`.

**Data:** UI cache at `data/ui_memory.db`.

---

## Gate B enhancements – goals and recovery

- **Goal queue:** `src/goals/goal_manager.py` – persistent goals in `data/memory.db` (table `goals`). During idle time (no triggers), the agent picks the highest-priority active goal and logs/touches it; full autonomous work is planned for Gate D.
- **Startup recovery:** Before the main loop, the agent runs a recovery check: optional “last Dream Cycle” timestamp (stub until Gate D), and marking goals not touched in 30 days as stale.
- **Metadata:** `src/maintenance/timestamps.py` – load/save timestamps in `data/memory.db` table `metadata` (e.g. `last_dream_cycle`).
- **SQLite WAL:** All SQLite connections use `PRAGMA journal_mode=WAL` for better crash recovery (memory, goals, timestamps, system metrics).

## 30-minute Gate A validation test (you run this)

The test is **interactive**: you must answer approval prompts and stop with Ctrl+C. An automated run cannot do that, so **you** run it locally.

**1. Optional: clear old logs**
```powershell
cd C:\Repos\Archi
Remove-Item logs\actions\* -Force -ErrorAction SilentlyContinue
Remove-Item logs\errors\* -Force -ErrorAction SilentlyContinue
Remove-Item logs\system\* -Force -ErrorAction SilentlyContinue
```

**2. Start the agent (production heartbeat: 10s/60s/600s)**
```powershell
.\venv\Scripts\python.exe -m src.core.agent_loop
```

**3. During ~30 minutes**
- When you see **Approve? (yes/no):**, type `yes` or `no` and Enter. Saying `yes` triggers **command mode** (10s checks for 2 min).
- Watch logs for: "Entered command mode", "Exited command mode → monitoring", "Entered deep sleep mode".
- After ~30 min, press **Ctrl+C** for graceful shutdown.

**4. Verify**
```powershell
.\verify_gate_a.ps1
```

**Fast test variant** (more cycles in 30 min): set `$env:ARCHI_GATE_A_FAST_TEST = "1"` before step 2; then heartbeat 10s and test cycle every 2 min.

## Quick check after a run

```powershell
# Action log (today)
Get-Content logs\actions\2026-02-08.jsonl

# Denied actions (illegal path should appear here)
Select-String -Path logs\actions\*.jsonl -Pattern "denied"
```
