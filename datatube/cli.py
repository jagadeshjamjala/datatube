"""Command-line entry point: python -m datatube <command>."""
import argparse
import json
import os
import sys
import tempfile
from functools import partial
from pathlib import Path

import anthropic

from datatube import classify as clf
from datatube import site
from datatube.config import load_channels, load_taxonomy
from datatube.db import connect, upsert
from datatube.rank import ranked
from datatube.youtube import QuotaExceeded, YouTube, _channel_row, _video_row, ingest, resolve

COMMANDS = {
    "resolve": "Resolve channel handles to IDs and uploads playlists",
    "ingest": "Fetch videos from channel uploads playlists",
    "classify": "Classify and tag videos with an LLM",
    "eval": "Measure classifier accuracy on tests/fixtures/eval.yaml",
    "rank": "Print the top-ranked videos (to tune the weights)",
    "build": "Build the static site",
    "all": "Run the full pipeline",
    "demo": "Offline run on fixtures, no keys needed",
}


def _youtube() -> YouTube:
    key = os.environ.get("YT_API_KEY")
    if not key:
        raise SystemExit("error: YT_API_KEY is not set. Export your YouTube Data API v3 key first.")
    return YouTube(key)


def run_resolve(args: argparse.Namespace) -> int:
    yt, con = _youtube(), connect()
    unresolved = resolve(con, yt, load_channels()["channels"], refresh=args.refresh)
    rows = con.execute(
        "SELECT group_name, handle, title, subscribers FROM channels ORDER BY group_name, handle"
    ).fetchall()
    for group, handle, title, subs in rows:
        print(f"  {group:<21} {handle:<24} {title}  ({subs if subs is not None else 'hidden'} subscribers)")
    for handle in unresolved:
        print(f"  UNRESOLVED {handle}")
    print(f"{len(rows)} channels resolved, {len(unresolved)} unresolved, units used: {yt.units}")
    return 0


def run_ingest(args: argparse.Namespace) -> int:
    yt, con = _youtube(), connect()
    stats = ingest(con, yt, load_channels()["channels"], refresh=args.refresh)
    for failure in stats["failed_channels"]:
        print(f"  FAILED {failure}")
    total = con.execute("SELECT count(*) FROM videos").fetchone()[0]
    print(f"added {stats['added']}, refreshed {stats['refreshed']}, {total} videos stored, units used: {yt.units}")
    return 0


def _classifier(offline: bool):
    taxonomy = load_taxonomy()
    if offline:
        return partial(clf.classify_offline, taxonomy=taxonomy)
    try:
        # A batch answers in well under a minute; the SDK's 10-minute default let one stuck request stall a run.
        client = anthropic.Anthropic(max_retries=5, timeout=120)
    except anthropic.AnthropicError as e:
        raise SystemExit(f"error: cannot create the Anthropic client ({e}). Set ANTHROPIC_API_KEY.")
    return clf.LLMClassifier(client, taxonomy)


def _report_usage(fn) -> None:
    if isinstance(fn, clf.LLMClassifier):
        print(f"model {fn.model}: {fn.input_tokens} input / {fn.output_tokens} output tokens, "
              f"{fn.failed_batches} failed batches (retried next run)")


def run_classify(args: argparse.Namespace) -> int:
    fn, con = _classifier(args.offline), connect()
    min_duration = load_channels()["defaults"]["min_duration_seconds"]
    # A real run replaces any keyword-heuristic rows left by an offline run.
    redo = not args.offline
    stored = clf.classify(con, fn, min_duration, limit=args.limit, redo_offline=redo)
    left = len(clf.pending_videos(con, min_duration, redo_offline=redo))
    print(f"classified {stored} videos, {left} still unclassified")
    _report_usage(fn)
    return 0


def run_eval(args: argparse.Namespace) -> int:
    fn = _classifier(args.offline)
    result = clf.evaluate(clf.load_eval(), fn)
    for miss in result["misses"]:
        item, pred = miss["item"], miss["predicted"]
        got = f"is_de={pred['is_de']} topic={pred['primary_topic']}" if pred else "no prediction"
        print(f"  MISS {item['video_id']} {item['title'][:60]!r}: expected is_de={item['is_de']} "
              f"topic={item['primary_topic']}, got {got}")
    print(f"is_de accuracy {result['is_de_accuracy']:.0%}, primary_topic accuracy {result['topic_accuracy']:.0%} "
          f"({result['n']} videos, {result['missing']} without a prediction)")
    _report_usage(fn)
    return 0


def run_rank(args: argparse.Namespace) -> int:
    rows = ranked(connect(), load_channels()["defaults"]["min_duration_seconds"], limit=args.top)
    for i, r in enumerate(rows, 1):
        print(f"{i:>3}. {r['score']:5.1f}  {r['title'][:60]:<60}  {(r['channel'] or '?')[:20]:<20} "
              f"{r['views'] or 0:>9,} views  {r['format']}/{r['primary_topic']}")
    return 0


def run_build(args: argparse.Namespace) -> int:
    count = site.build(connect(), load_channels()["defaults"]["min_duration_seconds"], load_taxonomy())
    print(f"built site/ with {count} videos")
    return 0


def run_all(args: argparse.Namespace) -> int:
    """The nightly pipeline. Each step skips work already done, so a failed run can simply be re-run."""
    run_resolve(argparse.Namespace(refresh=False))  # only handles added to channels.yaml since last time
    run_ingest(argparse.Namespace(refresh=args.refresh))
    if os.environ.get("ANTHROPIC_API_KEY"):
        run_classify(argparse.Namespace(offline=False, limit=args.limit))
    else:  # no LLM budget: keep stats and the site fresh; new videos wait unclassified (and unshown)
        waiting = len(clf.pending_videos(connect(), load_channels()["defaults"]["min_duration_seconds"]))
        print(f"ANTHROPIC_API_KEY not set: skipping classify, {waiting} videos waiting")
    run_build(argparse.Namespace())
    connect().execute("CHECKPOINT")  # fold the WAL into the .duckdb file before CI uploads it alone
    return 0


DEMO_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "youtube"
DEMO_CHANNEL = {"handle": "@SeattleDataGuy", "group": "creators", "tier": 1}


def run_demo(args: argparse.Namespace) -> int:
    """Fixtures -> offline classify -> site, in a throwaway DB so the real one is never touched."""
    taxonomy, min_duration = load_taxonomy(), load_channels()["defaults"]["min_duration_seconds"]
    fixture = lambda name: json.loads((DEMO_FIXTURES / f"{name}.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        con = connect(Path(tmp) / "demo.duckdb")
        upsert(con, "channels", [_channel_row(item, DEMO_CHANNEL) for item in fixture("channels_found")["items"]])
        upsert(con, "videos", [_video_row(item) for item in fixture("videos")["items"]])
        clf.classify(con, partial(clf.classify_offline, taxonomy=taxonomy), min_duration)
        count = site.build(con, min_duration, taxonomy)
        con.close()
    print(f"demo: built site/ with {count} videos. Open site/index.html in a browser.")
    return 0


HANDLERS = {"resolve": run_resolve, "ingest": run_ingest, "classify": run_classify, "eval": run_eval,
            "rank": run_rank, "build": run_build, "all": run_all, "demo": run_demo}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datatube", description="A curated video library for data engineers.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        cmd = sub.add_parser(name, help=help_text)
        cmd.set_defaults(func=HANDLERS[name])
        if name in ("resolve", "ingest", "all"):
            cmd.add_argument("--refresh", action="store_true", help="re-fetch data already stored")
        if name in ("classify", "eval"):
            cmd.add_argument("--offline", action="store_true", help="use the keyword heuristic, no API calls")
        if name in ("classify", "all"):
            cmd.add_argument("--limit", type=int, help="classify at most N videos")
        if name == "rank":
            cmd.add_argument("--top", type=int, default=20, help="how many videos to print")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except QuotaExceeded as e:
        print(f"error: {e}. Resume tomorrow; finished work is saved.", file=sys.stderr)
        return 1
    except anthropic.AuthenticationError:
        print("error: Anthropic rejected the API key. Check ANTHROPIC_API_KEY.", file=sys.stderr)
        return 1
