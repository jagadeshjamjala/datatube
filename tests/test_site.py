import json

import pytest

from datatube import site
from datatube.cli import main
from datatube.config import load_taxonomy
from datatube.db import connect, upsert

TAXONOMY = load_taxonomy()
XSS = '<script>alert("x")</script> & </script>'


def read_data(out):
    text = (out / "data.js").read_text()
    prefix = "window.DATATUBE = "
    assert text.startswith(prefix) and text.rstrip().endswith(";")
    return json.loads(text[len(prefix):].rstrip().rstrip(";"))


@pytest.fixture
def con(tmp_path):
    con = connect(tmp_path / "test.duckdb")
    upsert(con, "channels", [{"channel_id": "c1", "title": "Core DE", "tier": 1}])
    yield con
    con.close()


def add(con, vid, title="Kafka internals", views=1000, is_de=True, embeddable=None):
    upsert(con, "videos", [{"video_id": vid, "channel_id": "c1", "title": title, "views": views, "likes": 10,
                            "duration_seconds": 1800, "published_at": "2024-05-01 10:00:00",
                            "embeddable": embeddable}])
    upsert(con, "classifications", [{"video_id": vid, "is_de": is_de, "confidence": 0.9, "primary_topic": "streaming",
                                     "topics": ["streaming", "sql"], "level": "advanced", "format": "deep_dive",
                                     "evergreen": True, "summary": "How Kafka stores logs."}])


def test_build_writes_page_and_ranked_data(con, tmp_path):
    add(con, "low", views=10)
    add(con, "high", views=100_000)
    add(con, "off_topic", is_de=False)
    out = tmp_path / "site"
    assert site.build(con, 240, TAXONOMY, out=out) == 2
    data = read_data(out)
    assert [v["id"] for v in data["videos"]] == ["high", "low"]  # best first, non-DE dropped
    assert data["videos"][0] == {
        "id": "high", "title": "Kafka internals", "channel": "Core DE", "duration": 1800, "views": 100_000,
        "published": "2024-05-01", "level": "advanced", "format": "deep_dive", "topics": ["streaming", "sql"],
        "evergreen": True, "summary": "How Kafka stores logs.", "embeddable": True}
    assert {"id": "migration", "label": "Migration"} in data["topics"]
    assert data["levels"] == TAXONOMY["levels"] and data["formats"] == TAXONOMY["formats"]
    assert (out / "index.html").read_text() == site.TEMPLATE.read_text()


def test_only_known_blocked_videos_skip_the_player(con, tmp_path):
    add(con, "unknown")
    add(con, "yes", embeddable=True)
    add(con, "no", embeddable=False)
    site.build(con, 240, TAXONOMY, out=tmp_path)
    flags = {v["id"]: v["embeddable"] for v in read_data(tmp_path)["videos"]}
    assert flags == {"unknown": True, "yes": True, "no": False}


def test_hostile_title_stays_inert_data(con, tmp_path):
    add(con, "x", title=XSS)
    site.build(con, 240, TAXONOMY, out=tmp_path)
    raw = (tmp_path / "data.js").read_text()
    assert "<" not in raw and "</script>" not in raw
    assert read_data(tmp_path)["videos"][0]["title"] == XSS


def test_template_never_renders_with_innerhtml():
    page = site.TEMPLATE.read_text()
    assert "innerHTML" not in page and "insertAdjacentHTML" not in page and "document.write" not in page
    assert "youtube-nocookie.com/embed/" in page and '<script src="data.js">' in page


def test_demo_builds_from_fixtures_without_touching_real_db(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "SITE_DIR", tmp_path / "site")
    monkeypatch.setattr("datatube.db.DB_PATH", tmp_path / "must_not_exist.duckdb")
    assert main(["demo"]) == 0
    titles = {v["title"] for v in read_data(tmp_path / "site")["videos"]}
    assert "#shorts SQL tip" not in titles and titles  # short video filtered, something built
    assert not (tmp_path / "must_not_exist.duckdb").exists()
