# Plan: Goal-Driven Idle Behavior (TODO Item 1)

## Summary

Replace Archi's autonomous work generation with user-driven behavior: when idle with nothing to do, brainstorm suggestions and ask the user what to work on. Never auto-approve work. Follow-up extraction adds tasks to the current goal instead of spawning new goals.

## Changes by File

### 1. `src/core/idea_generator.py`

**Remove:**
- `plan_future_work()` — redundant with brainstorming, runs every cycle creating config-driven goals
- `request_brainstorm_approval()` — no more auto-approval flow
- Module-level `_brainstorm_approval_event` / `_brainstorm_approval_result` state
- `get_follow_up_depth()` — no more follow-up goal chains to track depth of

**Modify `brainstorm_ideas()` → rename to `suggest_work()`:**
- Remove night-hour restriction (11PM–5AM) — now runs during waking hours when idle with no goals
- Remove 24h cooldown — replace with a shorter cooldown (e.g., 1 hour) so it doesn't spam every 5 minutes
- Remove goal creation — just return the scored, filtered list of ideas
- Keep all quality filters (duplicate, relevance, purpose-driven, memory dedup)
- Keep idea backlog saving
- New signature: `suggest_work(router, goal_manager, learning_system, identity, last_suggest, stop_flag, memory) -> (ideas_list, updated_timestamp)`

### 2. `src/core/dream_cycle.py`

**New idle flow (replaces old Phase 1 + Phase 4):**
```
Dream cycle starts
  → Phase 0: Morning report (unchanged)
  → Check: are there active goals with pending tasks?
    → YES: Execute them (Phase 2, unchanged)
    → NO:
      → Call suggest_work() to brainstorm ideas
      → Send Discord message with numbered suggestions:
        "I don't have anything to work on. Some ideas:
         1. [idea 1]
         2. [idea 2]
         3. [idea 3]
         Reply with a number to start one, or tell me what you'd like!"
      → Return immediately (don't block, don't wait)
  → Phase 3: Learning review (keep — lightweight, no cost when <5 experiences)
  → Phase 4: Synthesis (modified — informational only, no goal creation)
  → Phase 5: File cleanup (unchanged)
```

**Modify `_run_synthesis()`:**
- Remove the block that creates follow-up goals from synthesis results
- Keep: model call to identify themes, logging to `synthesis_log.jsonl`
- The themes/insights are already available for the morning report

**Remove:**
- `self._last_proactive_goal_time` — no more proactive goal throttling
- Phase 4 `plan_future_work()` call

**Add:**
- `self._last_suggest_time` — cooldown for suggest_work()
- `self._pending_suggestions` — store last brainstormed ideas so Discord can map "1" → idea
- New method `_ask_user_for_work()` — orchestrates suggest + Discord message

### 3. `src/core/autonomous_executor.py`

**Rename `extract_follow_up_goals()` → `extract_follow_up_tasks()`:**
- Instead of calling `goal_manager.create_goal()`, call `goal_manager.add_follow_up_tasks()`
- Add new tasks to the SAME goal the completed task belongs to
- New tasks depend on the completed task (so they execute in order)
- Remove depth-tracking, duplicate-goal checks, relevance checks (not needed — we're staying within the user's original goal scope)
- Keep: file reading, model prompt for "what follow-ups?", memory dedup
- Modify prompt: instead of "suggest follow-up goals", say "suggest 0-2 additional tasks within this goal's scope"

**Update call site in `execute_task()`:**
- Change `extract_follow_up_goals()` → `extract_follow_up_tasks()`
- Pass `task` object so we can set dependencies correctly

### 4. `src/core/goal_manager.py`

**Add method `add_follow_up_tasks()`:**
```python
def add_follow_up_tasks(self, goal_id: str, task_descriptions: list, after_task_id: str) -> List[Task]:
    """Add follow-up tasks to an existing goal. New tasks depend on after_task_id."""
```
- Creates Task objects with `dependencies=[after_task_id]`
- Adds them to the goal via `goal.add_task()`
- Resets `goal.update_progress()` (completion % recalculates with new tasks)
- Saves state

### 5. `src/interfaces/discord_bot.py`

**Remove:**
- Brainstorm approval handling in `on_message()` (the block checking `_brainstorm_approval_event`)

**Add:**
- Check for pending suggestions: if `dream_cycle._pending_suggestions` exists and user sends "1", "2", "#1", "do 1", etc. → create goal from that suggestion, clear pending suggestions
- Simple pattern match: strip message, check if it's a single digit 1-5

### 6. `config/archi_identity.yaml`

**Remove:**
- `proactive_tasks` section (research + monitoring lists) — no longer used

### 7. Doc updates

- `claude/TODO.md` — mark item 1 complete, update session info
- `claude/ARCHITECTURE.md` — update dream cycle flow, remove plan_future_work/brainstorm references, update follow-up description
- `claude/SESSION_CONTEXT.md` — update last session info
