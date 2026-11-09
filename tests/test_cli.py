from datatube.db import connect

from datatube import cli
from datatube.cli import COMMANDS, build_parser, main


def test_all_commands_registered():
    help_text = build_parser().format_help()
    assert set(COMMANDS) == {"resolve", "ingest", "classify", "eval", "rank", "build", "all", "demo"}
    for name in COMMANDS:
        assert name in help_text


def test_all_runs_each_step_in_order_with_its_flags(monkeypatch, tmp_path):
    calls = []
    for step in ("resolve", "ingest", "classify", "build"):
        monkeypatch.setattr(cli, f"run_{step}", lambda a, step=step: calls.append((step, vars(a))) or 0)
    monkeypatch.setattr(cli, "connect", lambda: connect(tmp_path / "t.duckdb"))
    assert main(["all", "--limit", "1000", "--refresh"]) == 0
    assert calls == [
        ("resolve", {"refresh": False}),
        ("ingest", {"refresh": True}),
        ("classify", {"offline": False, "limit": 1000}),
        ("build", {}),
    ]
