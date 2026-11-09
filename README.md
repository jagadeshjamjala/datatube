# DataTube

A curated video library for data engineers. It collects YouTube videos from data engineering
creators, vendors and conferences, uses an LLM to keep only genuine data engineering content
and tag it, ranks the results, and publishes a static, searchable site.

```
config/channels.yaml ─► resolve ─► ingest (YouTube Data API) ─► DuckDB ─► classify (LLM) ─► rank ─► build ─► site/
```

## Local setup

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m datatube demo        # offline, no keys: builds site/ from test fixtures
```

For a real run, export `YT_API_KEY` (YouTube Data API v3) and `ANTHROPIC_API_KEY`.

## Commands

| Command | What it does |
|---|---|
| `resolve [--refresh]` | Channel handles in `config/channels.yaml` → channel IDs and uploads playlists |
| `ingest [--refresh]` | New videos from each channel; `--refresh` also re-pulls stats for stored ones |
| `classify [--limit N] [--offline]` | LLM tags unclassified videos; `--offline` uses a keyword heuristic |
| `eval [--offline]` | Classifier accuracy on the hand-labelled `tests/fixtures/eval.yaml` |
| `rank [--top N]` | Print the top videos with their scores |
| `build` | Write `site/index.html` and `site/data.js` |
| `all [--limit N] [--refresh]` | resolve → ingest → classify → build (what the nightly job runs) |
| `demo` | Offline end-to-end run on fixtures, in a throwaway database |

Every step skips work already done, so an interrupted run can simply be re-run.
To view the site locally, serve it rather than opening the file (YouTube's player
may refuse to play when the page is opened from `file://`): `python -m http.server -d site 8000`.

## Hosting (GitHub Actions + Pages)

`.github/workflows/refresh.yml` runs nightly and on demand: tests → download the database →
`all --limit 1000 --refresh` → upload the database → deploy `site/` to Pages.
The DuckDB file is kept as an asset on a release named `db` instead of in git, so the
repo doesn't grow by the database's size every night.

One-time setup:
1. Push the repo to GitHub.
2. Settings → Secrets and variables → Actions: add secrets `YT_API_KEY` and `ANTHROPIC_API_KEY`.
   Optionally add a variable `DATATUBE_MODEL` to override the classifier model.
   Without `ANTHROPIC_API_KEY`, `all` skips classify: stats and the site stay fresh, and new
   videos wait unclassified (not shown) until a key is added.
3. Settings → Pages → Source: **GitHub Actions**.
4. Upload your local database once: `gh release create db data/datatube.duckdb --title db --notes "Pipeline database"`.
5. Actions → Nightly refresh → **Run workflow**.

The job fails on purpose if the `db` release is missing, rather than starting from an empty
database and re-spending quota and LLM credit.

## Tuning

- **Ranking:** the weights at the top of `datatube/rank.py`. Check with `python -m datatube rank --top 20`.
- **Sources:** add channels to `config/channels.yaml` (`tier` 1 = core DE, 2 = mixed); `resolve` picks up new handles.
- **Topics:** `config/taxonomy.yaml`. After changing it, re-classify (delete rows from `classifications`).
- **Classifier:** the prompt in `datatube/classify.py`; measure changes with `eval`. Model via `DATATUBE_MODEL`.

## Costs

- **YouTube quota:** 10,000 units/day free. A nightly `--refresh` over ~40 channels and ~13k videos
  uses about 540 units (1 unit per 50 videos). `search.list` (100 units) is never used.
- **LLM:** classifying 1,000 videos uses about 385k input and 120k output tokens
  (measured on the default model). Once the backlog is done, a night only classifies newly
  uploaded videos, usually a few dozen. See [Anthropic pricing](https://www.anthropic.com/pricing)
  for current per-token rates.
- **Hosting:** GitHub Actions minutes and Pages are free for public repos.

See `docs/PLAN.md` for the build plan.
