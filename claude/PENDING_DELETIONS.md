# Pending Deletions

Cowork sessions cannot delete files (requires manual approval, stalls sessions). Log deletions here instead. Jesse will review and action them.

**Format:** `- [ ] path/to/file — reason (session N)`

---

## Pending

- [x] `.git/index.lock.bak`, `.git/index.lock.gone`, `.git/index.lock.stale`, `.git/index.lock.stale2` — No longer present as of session 221. Already cleaned up.
- [x] `.git/HEAD.lock` — Was present sessions 219-222. Jesse cleared it before session 223.
- [ ] `.git/index.lock` — Recreated by session 223's `git add` attempt (Cowork sandbox can't unlink locks). Jesse: delete `.git/index.lock`, then run: `git add -A && git commit -m "Sessions 220-223: notification quality, exploration saturation, goal completion, suggestion quality, email design doc"`. (session 223)

## Completed

- [x] `.git/index.lock` — Was blocking commits in sessions 216-218. Resolved by session 219 — commits went through. (session 219)
- [x] Sessions 216-218 uncommitted changes — All committed as of session 219.
