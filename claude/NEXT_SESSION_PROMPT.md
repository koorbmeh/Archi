# Session 87 — Open Items

**Read all docs in `claude/` first:** SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md.

Session 86 completed: Implemented LanceDB IVF-PQ index for vector store scalability. `_ensure_index()` in `vector_store.py` auto-creates index when memory exceeds 10K entries (cosine metric, `rows // 4096` partitions, 48 sub-vectors for 384-dim embeddings). Rechecks every 1000 adds. Graceful degradation if index creation fails. 12 new unit tests in `tests/unit/test_vector_store.py`. ~1230 tests passing (pending confirmation — run `PYTHONPATH=. pytest tests/unit/ -x`).

---

## Open items (lower priority)

- **git_safety.py multi-file checkpoint** — After switching from `git add -A` to specific-file staging, multi-file changes within a single task aren't fully captured. Noted as acceptable tradeoff but worth revisiting if it causes issues. Touches: `src/utils/git_safety.py`.

---

## After completing fixes

1. Run full test suite: `PYTHONPATH=. pytest tests/unit/ -x`
2. Follow the **Wrapping Up a Session** checklist in `claude/WORKFLOW.md` — all 5 steps are mandatory:
   - Present new TODO items found while reading code
   - Update `claude/TODO.md` (verify fixes in source before marking done)
   - Update `claude/SESSION_CONTEXT.md`
   - Update **ALL** other claude/ docs as needed — **especially ARCHITECTURE.md** (test counts, module changes, config values)
   - Overwrite this file (`claude/NEXT_SESSION_PROMPT.md`) with the handoff for the next session

Key constraints: Follow CODE_STANDARDS.md strictly — read before writing, search before deleting, trace the ripple, net-zero lines where possible. ~1230 tests currently passing.
