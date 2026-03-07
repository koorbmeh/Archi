# Session 229 — Starter Prompt

Read all docs in `claude/` first: SESSION_CONTEXT.md, WORKFLOW.md, CODE_STANDARDS.md, ARCHITECTURE.md, TODO.md, SELF_IMPROVEMENT.md.

---

## What was done (session 228)

Built the content creation pipeline (Phase 1) — Archi can now generate and publish content across platforms. New module `src/tools/content_creator.py` (~460 lines): model-based content generation in 4 formats (blog post, tweet, tweet thread, Reddit post), plus 3 platform publishers (GitHub Pages via API, Twitter/X via Tweepy, Reddit via PRAW). All publishers gracefully degrade if credentials or libraries are missing. Integrated via router intent `"content"` → 3 action handlers (create_content, publish_content, list_content). Content activity logged to `logs/content_log.jsonl`. Also wrote design doc `claude/DESIGN_CONTENT_PIPELINE.md`. Added PyGithub, tweepy, praw to requirements.txt. +26 new tests, 210 passed (content + dispatcher + router).

Also fixed a latent bug in the router's tier validation — intents with action handlers (schedule, email, digest, calendar, content) were being incorrectly bumped from "easy" to "complex" when the model didn't include an `answer` field. Added these intents to the exemption list.

**Test count:** 210 passed on content + dispatcher + router tests. Full suite not re-run.

---

## What to work on this session

### Priority 1: Next roadmap capability

Content pipeline Phase 1 is built (GitHub blog + Twitter + Reddit). Jesse wants to expand to video/social platforms. Revised platform priorities (see `claude/DESIGN_CONTENT_PIPELINE.md`):

- **YouTube publisher** — Highest priority. Free API, OAuth 2.0, `google-api-python-client`. Even before video generation, Archi can create descriptions, tags, metadata, and scripts. Jesse has a YouTube presence.
- **Meta Graph API (Facebook + Instagram)** — Same developer account covers both. Free. Supports text, images, video, Reels, Stories. Requires Business/Creator Instagram account.
- **Dream cycle auto-content** — When Archi discovers something interesting during exploration, draft a blog post and tweet about it. Key step from tool → autonomous content creator.
- **Rumble publisher** — Has API, Selenium fallback exists. Growing platform Jesse is interested in.
- **Kick** — Just launched public API. Mainly streaming, video upload unclear. Monitor for now.
- Reddit is deprioritized. TikTok skipped (audit friction too high).

### Priority 2: Install dependencies

Jesse needs to:
```
pip install feedparser icalendar PyGithub tweepy praw
```

### Priority 3: Git

Git is still blocked by `.git/index.lock`. Jesse needs to delete it and commit.

---

## Jesse action needed

1. **Install dependencies:** `pip install feedparser icalendar PyGithub tweepy praw`
2. **Configure GitHub blog (easiest platform to start):**
   - Create a GitHub Personal Access Token at https://github.com/settings/tokens (classic, with "repo" scope)
   - Add to `.env`: `GITHUB_PAT=ghp_xxx`
   - Add to `.env`: `GITHUB_BLOG_REPO=YourUsername/archi-blog` (Archi will create the repo if it doesn't exist)
3. **Configure Twitter/X (optional — free tier):**
   - Apply for developer account at https://developer.x.com
   - Create project/app, enable OAuth 1.0a with Read+Write
   - Add 4 keys to `.env`: `TWITTER_API_KEY`, `TWITTER_API_SECRET`, `TWITTER_ACCESS_TOKEN`, `TWITTER_ACCESS_SECRET`
4. **Configure Reddit (optional — free):**
   - Create app at https://www.reddit.com/prefs/apps (script type)
   - Add to `.env`: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`
5. **Test content creation:** Send Archi "write a blog post about AI agents" via Discord. Should get a draft back. Then "publish that to the blog" to post it.
6. **Delete `.git/index.lock`** and commit all sessions.
7. **Calendar + digest** (from session 227) still needs ICS URL config.

---

## Key constraints

- Follow `claude/CODE_STANDARDS.md` for all code changes.
- Test count: 210 passed on content + dispatcher + router. ~3922+ passed overall (pre-existing).
- Protected files: `src/core/plan_executor/` (all 6 files), `src/core/safety_controller.py`.
- **Stay under 50% context window.** Wrap up proportional to what you did.
- **Never use AskUserQuestion tool.** Never delete files. Never attempt interactive confirmation.
- **Don't re-verify unchanged items.** Verification items are parked until next deploy.
