# DataTube build plan

Run each phase in a fresh Claude Code session: `/clear`, then `/next-phase`.
Approve the plan, let it build and test, run `/review`, then commit.

---

## Phase 1 — Project skeleton
- [x] Repo layout: `datatube/`, `config/`, `tests/fixtures/`, `data/` (gitignored WAL), `site/`
- [x] `requirements.txt`, `.gitignore`, `README.md` stub
- [x] CLI `python -m datatube <cmd>` with subcommands: resolve, ingest, classify, build, all, demo (stubs that print "not implemented")
- [x] `pytest` runs (one smoke test)

**Done when:** `python -m datatube --help` lists all commands and `pytest -q` passes.

## Phase 2 — Config: channels and topics
- [x] `config/channels.yaml`: seed channels grouped as creators / vendors_and_projects / conferences, each with `handle`, `tier` (1 core DE, 2 mixed), optional `max_videos`, `note`; plus `defaults` (max_videos 500, min_duration_seconds 240)
- [x] `config/taxonomy.yaml`: ~16 topics with stable `id`, `label`, `hint` (must include `migration`: legacy to cloud), levels (beginner/intermediate/advanced), formats (tutorial, talk, deep_dive, project, opinion, interview, product)
- [x] Loader with validation (unique ids, required fields) and a test

**Done when:** a test loads both files and fails clearly on a bad config.

*Your input:* review the channel list and add creators you rate, especially migration content.

## Phase 3 — Storage
- [x] `datatube/db.py`: connect, create tables `channels`, `videos`, `classifications` if missing
- [x] Generic `upsert(table, rows)` helper (INSERT OR REPLACE)
- [x] Tests on a temp DuckDB file

**Done when:** tests create the DB, upsert twice, and row counts don't double.

## Phase 4 — YouTube ingestion
- [x] `datatube/youtube.py`: client with retry/backoff, clear error on `quotaExceeded`, unit counter
- [x] `resolve`: handle → channel ID, uploads playlist, subscribers (`channels.list?forHandle=`); print unresolved handles
- [x] `ingest`: page the uploads playlist up to `max_videos`, then `videos.list` in batches of 50; skip video IDs already stored; `--refresh` re-pulls stats
- [x] ISO-8601 duration parser; drop videos shorter than `min_duration_seconds` later in the pipeline
- [x] Tests with mocked HTTP responses saved in `tests/fixtures/`

**Done when:** tests pass; then (with your key) `resolve` prints every channel and `ingest` reports units used (expect a few hundred).

## Phase 5 — LLM classification
- [x] `datatube/classify.py`: 20 videos per request (title, channel, minutes, first 700 chars of description)
- [x] Forced tool call `record_classifications` returning: is_de, confidence, primary_topic, topics (≤3), level, format, evergreen, one-line summary
- [x] Only classify videos with no classification; commit every 200; `--limit`
- [x] `--offline` keyword heuristic for tests and the demo
- [x] **Eval set:** `tests/fixtures/eval.yaml` with ~30 videos you label by hand; script `python -m datatube eval` prints accuracy for is_de and primary_topic

**Done when:** mocked tests pass; on the real eval set, is_de accuracy ≥ 90%. If not, tune the prompt and re-run.

*Your input:* label the 30 eval videos. This is where your judgment makes the product.

## Phase 6 — Ranking
- [x] `datatube/rank.py`: one readable SQL query. Score = popularity (log views) + like rate + channel tier + freshness (or evergreen bonus) − vendor-demo penalty − low-confidence penalty
- [x] Filter: is_de, confidence ≥ 0.6, duration ≥ min
- [x] Test on fixtures: a tier-1 deep dive outranks a vendor product demo

**Done when:** the top 20 on real data look right to you. Tune the weights until they do.

## Phase 7 — Static site
- [x] `build` writes `site/index.html` (from a template) and `site/data.js` (`window.DATATUBE = {...}`) so it works from `file://`
- [x] Topic sidebar with counts, level/format chips, "evergreen only", search, sort (best, newest, most viewed)
- [x] Cards: thumbnail, duration, channel, views, year, tags, summary; click plays in a modal (youtube-nocookie embed)
- [x] Light/dark mode, mobile layout, all text HTML-escaped
- [x] `demo` command: load fixtures → offline classify → build

**Done when:** `python -m datatube demo` then opening `site/index.html` works on desktop and phone width.

## Phase 8 — Automation and hosting
- [x] `.github/workflows/refresh.yml`: nightly ingest → classify (limit 1000) → build → save DB to the `db` release (not git, to keep history small) → deploy to GitHub Pages
- [x] Repo secrets `YT_API_KEY` (+ `ANTHROPIC_API_KEY` optional: without it classify is skipped); Pages source = GitHub Actions
- [x] README: setup, commands, tuning, cost notes

**Done when:** a manual "Run workflow" succeeds and the Pages URL serves the site.

## Phase 9 — Make it yours (pick any)
- [x] Learning paths: ordered beginner → advanced lists per topic
- [ ] Reddit r/dataengineering as a community source
- [ ] Transcripts for better tagging of long conference talks
- [ ] "Submit a video" form feeding a review queue
- [ ] Custom domain and a launch post for the newsletter
