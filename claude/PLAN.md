# Plan: Split Identity Config into Static + Dynamic Project Context

## Summary

Split `config/archi_identity.yaml` into two files: a protected static identity config (name, role, timezone, safety rules, communication style) and a new `data/project_context.json` that Archi can update at runtime. The idea generator will scan actual project files before brainstorming so Grok stops hallucinating filenames. `reset.py` will interactively ask whether to clear the project context or keep it.

## Why

1. `archi_identity.yaml` is protected — nothing can write to it, so project data is frozen forever
2. The idea generator reads `autonomous_tasks` like "Identify gaps in current protocol" and Grok invents `gaps.md` that doesn't exist
3. `focus_areas` lists 6 broad categories but only Health has a project folder
4. `user_preferences.py` learns from conversations but doesn't feed back into work generation
5. Several keys in the yaml (`requires_approval`, `absolute_rules`, `communication`, `constraints.prefer_local`) are never read by code — they're decorative

## New File: `data/project_context.json`

```json
{
  "version": 1,
  "last_updated": "2026-02-17T...",
  "focus_areas": ["Health: Physical well-being, fitness, nutrition, sleep"],
  "interests": [
    "Health optimization and longevity",
    "AI and autonomous systems",
    "Financial optimization",
    "System automation",
    "Personal productivity"
  ],
  "current_projects": [
    "Building Archi (autonomous AI agent)",
    "Health optimization protocols"
  ],
  "active_projects": {
    "health_optimization": {
      "path": "workspace/projects/Health_Optimization",
      "description": "Comprehensive health and longevity optimization protocol",
      "priority": "high",
      "focus_areas": [
        "Supplement research and optimization",
        "Fitness and exercise protocols",
        "Sleep optimization",
        "Nutrition strategies",
        "Longevity interventions"
      ],
      "autonomous_tasks": [
        "Review existing files in the project and suggest improvements based on recent research",
        "Find contradictory evidence or risks for topics covered in project files",
        "Suggest evidence-based additions to existing documents"
      ]
    }
  }
}
```

Key changes from what was in identity.yaml:
- Only Health focus area (the only one with infrastructure)
- Removed "Job search and career transition" from current_projects (confirm with Jesse)
- Rewrote autonomous_tasks to be grounded ("Review existing files") instead of hallucination-prone ("Identify gaps in current protocol")
- No `file_inventory` field — we'll scan live at brainstorm time instead of caching

## New File: `src/utils/project_context.py` (~60 lines)

Centralizes all project context loading so 4 different files don't each re-implement it.

```python
def load() -> dict:
    """Load from data/project_context.json, fallback to identity yaml."""

def save(context: dict) -> bool:
    """Atomic write to data/project_context.json."""

def scan_project_files(project_path: str) -> list[str]:
    """List actual files in a project directory (*.md, *.json, subdirs)."""
```

All 4 consumers switch to `from src.utils.project_context import load, scan_project_files`.

## File Changes

### 1. `config/archi_identity.yaml` — Remove dynamic data, keep static

**Remove:** `focus_areas`, `user_context.interests`, `user_context.current_projects`, `user_context.active_projects`, `requires_approval`, `absolute_rules`, `constraints.prefer_local`, `communication` (none are read by code)

**Keep:**
```yaml
identity:
  name: "Archi"
  role: "Local autonomous intelligence for Jesse"

user_context:
  location: "Madison, Wisconsin"
  timezone: "America/Chicago"
  working_hours: "9 AM - 11 PM"
```

That's it. Everything else either moved to project_context.json or was dead config.

### 2. `src/core/idea_generator.py` — Use project context + file scanning

- Change `suggest_work()` param from `identity: dict` to `project_context: dict`
- Change `_get_active_project_names(identity)` to `_get_active_project_names(project_context)`
- Change `is_goal_relevant(desc, identity)` to `is_goal_relevant(desc, project_context)`
- In prompt construction: call `scan_project_files()` and inject "Files in this project: ..." so Grok knows what actually exists
- Read `focus_areas`, `interests`, `active_projects`, `current_projects` from project_context instead of identity

### 3. `src/core/dream_cycle.py` — Load project context alongside identity

- Add `self.project_context = project_context.load()` in `__init__`
- Pass `project_context=self.project_context` to `suggest_work()` (both call sites: `_try_proactive_initiative` and `_ask_user_for_work`)
- Keep `self.identity` load for the one place it reads `identity.role` (or just hardcode "Archi" and drop it)

### 4. `src/core/autonomous_executor.py` — Update `_resolve_project_path()`

- Replace yaml load of `archi_identity.yaml` with `project_context.load()`
- Same keyword matching logic, just reads from `active_projects` in the new dict

### 5. `src/interfaces/message_handler.py` — Update `_load_active_project_context()`

- Replace yaml load with `project_context.load()`
- Same output format (project list for system prompt)

### 6. `src/utils/time_awareness.py` — No changes

Still reads `timezone` and `working_hours` from identity yaml. These stay in the static config.

### 7. `scripts/reset.py` — Interactive project context prompt

Add after the main confirmation, before resetting:

```python
# Ask about project context separately
clear_project_context = False
ctx_path = DATA_DIR / "project_context.json"
if ctx_path.exists() and not args.yes:
    print("  Project context (data/project_context.json) stores your active")
    print("  projects, interests, and focus areas.")
    print()
    ctx_answer = input("  Also clear project context? [y/N] ").strip().lower()
    clear_project_context = ctx_answer in ("y", "yes")
elif args.yes:
    clear_project_context = False  # --yes preserves project context by default
```

Then in `clear_data_runtime()`, pass a flag and conditionally reset:

```python
if clear_project_context:
    # Reset to empty — Archi will rebuild from identity yaml fallback
    json_resets["project_context.json"] = {}
else:
    _banner("project_context.json preserved")
```

Add `--clear-context` flag for automation: `reset.py --yes --clear-context`.

### 8. `config/rules.yaml` — No changes needed

`archi_identity.yaml` stays in `protected_files`. `project_context.json` is in `data/` which isn't protected, so Archi can write to it. No rule changes needed.

## Implementation Order

1. Create `src/utils/project_context.py` (new helper module)
2. Create `data/project_context.json` (seed with current values, cleaned up)
3. Update `idea_generator.py` (biggest change — new param, file scanning in prompt)
4. Update `dream_cycle.py` (load + pass project context)
5. Update `autonomous_executor.py` (swap yaml load for project_context.load)
6. Update `message_handler.py` (swap yaml load for project_context.load)
7. Slim down `archi_identity.yaml` (remove moved/dead keys)
8. Update `reset.py` (interactive project context prompt)
9. Update claude docs (ARCHITECTURE.md, SESSION_CONTEXT.md, TODO.md)

## What This Does NOT Change (Future Work)

- **user_preferences.py doesn't feed into project_context yet** — a future session could have Archi update `current_projects` or `interests` when it learns something from conversation
- **Archi can't add new projects yet** — that would need a Discord command or conversation trigger. For now Jesse edits the JSON manually or asks in a Cowork session
- **No auto-scanning on startup** — file inventory is scanned live at brainstorm time, not cached. If performance becomes an issue, add caching later
