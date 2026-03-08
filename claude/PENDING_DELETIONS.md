# Pending Deletions

Cowork sessions cannot delete files (requires manual approval, stalls sessions). Log deletions here instead. Jesse will review and action them.

**Format:** `- [ ] path/to/file — reason (session N)`

---

## Pending

- [x] `.git/index.lock.bak`, `.git/index.lock.gone`, `.git/index.lock.stale`, `.git/index.lock.stale2` — No longer present as of session 221. Already cleaned up.
- [x] `.git/HEAD.lock` — Was present sessions 219-222. Jesse cleared it before session 223.
- [x] `.git/index.lock` — Was blocking commits. Jesse cleared before session 240. Sessions 234-240 committed.
- [x] `.git/index.lock` — 0-byte stale lock, blocking all git operations. (session 241, still present session 243)
- [x] `.git/HEAD.lock` — 0-byte stale lock. (session 243)

**Jesse:** Delete both lock files, then run:
```
del .git\index.lock
del .git\HEAD.lock
git add -A && git commit -m "Sessions 241-245: content calendar, visual pipeline, image hosting, supplement tracker, finance tracker"
```

## Completed

- [x] `.git/index.lock` — Was blocking commits in sessions 216-218. Resolved by session 219 — commits went through. (session 219)
- [x] Sessions 216-218 uncommitted changes — All committed as of session 219.
