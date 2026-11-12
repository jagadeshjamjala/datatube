# DataEngTube

A curated video library for data engineers, inspired by Karpathy's HackerTube
(its pipeline and site; DataEngTube has no Hacker News source).
It collects YouTube videos from data engineering creators, vendors and conferences,
uses an LLM to keep only genuine data engineering content and tag it, ranks the
results, and publishes a static, searchable site.

The build plan lives in `docs/PLAN.md`. Work one phase at a time, in order.
Tick the phase's checkboxes in `docs/PLAN.md` when its "Done when" criteria pass.

## Stack (do not change without asking)
- Python 3.12, plain modules in `datatube/`, CLI via `python -m datatube <command>`
- DuckDB, single file at `data/datatube.duckdb`
- `requests` for HTTP, `pyyaml` for config, `anthropic` SDK for classification
- Static site: one `index.html` (vanilla JS, no framework, no build step) + generated `data.js`
- `pytest` for tests; GitHub Actions + GitHub Pages for nightly refresh and hosting

## Architecture
```
config/channels.yaml ─► resolve ─► ingest (YouTube Data API v3) ─► DuckDB ─► classify (LLM) ─► rank ─► build ─► site/
```
Tables: `channels`, `videos` (raw facts) and `classifications` (LLM output),
kept separate so videos can be re-classified without re-fetching.

## Rules
- **Never call real APIs in tests.** Mock HTTP and the Anthropic client; use `tests/fixtures/`.
- **YouTube quota is 10,000 units/day.** Never use `search.list` (100 units). Use
  `channels.list`, `playlistItems.list` and `videos.list` (1 unit each, 50 items per call).
- Every step is idempotent and resumable: re-running skips work already done.
- Secrets come from env vars `YT_API_KEY` and `ANTHROPIC_API_KEY`; never write keys to files.
- Classifier model comes from env `DATATUBE_MODEL`, default `claude-haiku-5-5`.
  Use a forced tool call so output is always valid JSON.
- Keep functions small and readable; this is also a portfolio project.
- After each change run `pytest -q`. Don't mark a phase done with failing tests.
- Escape all video text in the site (titles come from the internet).

## Commands
- `pip install -r requirements.txt`
- `pytest -q`
- `python -m datatube demo` — offline run on fixtures, no keys needed
- `python -m datatube all` — real run
