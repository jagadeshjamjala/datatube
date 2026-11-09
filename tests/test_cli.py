from datatube.db import connect

from datatube import cli
from datatube.cli import COMMANDS, build_parser, main


def test_all_commands_registered():
    help_text = build_parser().format_help()
    assert set(COMMANDS) == {"resolve", "ingest", "classify", "eval", "rank", "build", "all", "demo"}
    for name in COMMANDS:
        assert name in help_text


def run_all_with_fake_steps(monkeypatch, tmp_path, argv):
    calls = []
    for step in ("resolve", "ingest", "classify", "build"):
        monkeypatch.setattr(cli, f"run_{step}", lambda a, step=step: calls.append((step, vars(a))) or 0)
    monkeypatch.setattr(cli, "connect", lambda: connect(tmp_path / "t.duckdb"))
    assert main(argv) == 0
    return calls


def test_all_runs_each_step_in_order_with_its_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert run_all_with_fake_steps(monkeypatch, tmp_path, ["all", "--limit", "1000", "--refresh"]) == [
        ("resolve", {"refresh": False}),
        ("ingest", {"refresh": True}),
        ("classify", {"offline": False, "limit": 1000}),
        ("build", {}),
    ]


def test_all_skips_classify_without_an_anthropic_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    steps = [step for step, _ in run_all_with_fake_steps(monkeypatch, tmp_path, ["all"])]
    assert steps == ["resolve", "ingest", "build"]
    assert "skipping classify, 0 videos waiting" in capsys.readouterr().out
