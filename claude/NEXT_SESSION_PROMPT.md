# Session 219 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done last session (session 218)

**Opinion bootstrapping + live verification.**

1. **Git index.lock STILL blocking commits.** Sessions 216-218 changes saved to disk but not committed. Jesse: delete `.git/index.lock` (and `*.bak`/`*.gone`/`*.stale*` variants), then commit. See `PENDING_DELETIONS.md` for full list.

2. **Live verification.** Worldview: 7 preferences, 6 interests, 0 opinions, 0 meta observations, 0 personal projects. Post-restart (PID 15544, 16:51+) confirmed healthy: "test" spam gone, scheduled tasks working, taste development active. Interests still 4/6 health-related (session 217 rotation fix not yet deployed due to commit block).

3. **Implemented opinion bootstrapping.** `_lightweight_reflection()` now seeds opinions from task outcomes when <3 opinions exist, via `_extract_seed_opinion()`. Five domain maps: research→"web research effectiveness", writing→"content creation approach", coding→"coding task strategy", analysis→"analysis workflow", image→"image generation". Each produces success/failure position variants. Confidence starts at 0.35 so model-based reflection can refine. +7 tests (103 total in test_worldview.py).

**Test count:** 4591 passed, 21 skipped, 23 failed (all scheduler/croniter pre-existing). On Windows with croniter: estimated ~4618 passed, 18 skipped.

---

## What to work on this session

### Priority 1: Commit ALL pending changes (if lock cleared)

If `.git/index.lock` is gone, commit everything from sessions 216-218:
```
git add src/core/idea_generator.py tests/unit/test_idea_generator.py src/core/worldview.py tests/unit/test_worldview.py claude/
git commit -m "Sessions 216-218: topic saturation, exploration rotation, opinion bootstrapping"
```

If lock still present, note in PENDING_DELETIONS.md and proceed.

### Priority 2: Verify opinion bootstrapping works (if deployed)

Check `data/worldview.json` after next task completions:
- Are opinions being seeded? (Should see 1-3 opinions with confidence 0.35)
- Are they from the expected domains (research/writing/coding/analysis)?
- Is the <3 cap working (no more than 3 seeded before stopping)?

### Priority 3: Remaining live verification items

These need observation over multiple dream cycles:
- **Exploration topic rotation** — session 217 fix. Check if explorations diversify away from health topics.
- **Interest growth cap** — session 217. Check if interest count stays at or below 8.
- **Worldview opinions** — session 218. Check if opinions appear after task completions.
- **Opinion revision delivery** — needs an opinion to change significantly.
- **Self-reflection** — every 50 dream cycles, needs >=5 journal entries in 7 days.
- **Personal projects** — proposals from high-curiosity interests, heartbeat Phase 6.5 every 10th cycle.
- **Meta-cognition** — observations after 50-cycle self-reflection.

### Priority 4: Proactive improvement work (if context budget allows)

Check `claude/SELF_IMPROVEMENT.md`. Good candidates:
- Review if `_filter_ideas()` should also use topic saturation at the filter level
- Dream cycle output quality monitoring (log formatter results before send)
- Explore what useful proactive tasks Archi could do for Jesse beyond suggestions

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all changes.
- Estimated test count: ~4618 passed, 18 skipped on Windows with croniter (session 218).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`, `config/personality.yaml`.
- Keep only last 10 sessions in TODO.md completed work.
- **Stay under 50% context window usage.** Plan for 2-3 solid tasks + thorough wrap-up.
- **Never use the AskUserQuestion tool** in Cowork sessions.
- **Never delete files** — log to `claude/PENDING_DELETIONS.md` instead.
- **Never attempt any action requiring interactive confirmation.**
- **Full autonomy mode:** Code, test, commit, write NEXT_SESSION_PROMPT for the next session.
