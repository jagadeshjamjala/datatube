import json
from pathlib import Path

import pytest
import requests

from datatube.cli import main
from datatube.db import connect
from datatube.youtube import QuotaExceeded, YouTube, YouTubeError, ingest, parse_duration, resolve

FIXTURES = Path(__file__).parent / "fixtures" / "youtube"
CHANNEL = {"handle": "@SeattleDataGuy", "tier": 1, "group": "creators", "max_videos": 500}
MISSING = {"handle": "@NoSuchChannel", "tier": 2, "group": "creators", "max_videos": 500}


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code, self.body = status_code, body

    def json(self):
        return self.body


class FakeSession:
    """Answers YouTube API calls from tests/fixtures/youtube and records each call."""

    def __init__(self, script=None):
        self.calls = []
        self.script = list(script or [])  # optional canned responses/exceptions, used first

    def get(self, url, params, timeout):
        endpoint = url.rsplit("/", 1)[1]
        self.calls.append((endpoint, params))
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        return FakeResponse(200, self.route(endpoint, params))

    def route(self, endpoint, params):
        if endpoint == "channels":
            return fixture("channels_found" if params["forHandle"] == CHANNEL["handle"] else "channels_not_found")
        if endpoint == "playlistItems":
            return fixture("playlist_page2" if params.get("pageToken") == "PAGE2" else "playlist_page1")
        if endpoint == "videos":
            wanted = params["id"].split(",")
            return {"items": [v for v in fixture("videos")["items"] if v["id"] in wanted]}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    def endpoints(self):
        return [endpoint for endpoint, _ in self.calls]


def make_yt(session):
    waits = []
    return YouTube("SECRET_KEY", session=session, sleep=waits.append), waits


def api_error(status, reason):
    return FakeResponse(status, {"error": {"code": status, "errors": [{"reason": reason}]}})


@pytest.fixture
def con(tmp_path):
    con = connect(tmp_path / "test.duckdb")
    yield con
    con.close()


@pytest.mark.parametrize(
    "value, seconds",
    [("PT1H2M3S", 3723), ("PT4M", 240), ("PT59S", 59), ("PT2H", 7200), ("P1DT2H", 93600),
     ("P0D", 0), (None, None), ("", None), ("4:05", None), ("PT5X", None)],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value) == seconds


def test_retries_server_errors_with_backoff():
    session = FakeSession([FakeResponse(500, {}), api_error(403, "rateLimitExceeded"), FakeResponse(200, {"ok": 1})])
    yt, waits = make_yt(session)
    assert yt.get("videos", id="x") == {"ok": 1}
    assert yt.units == 3
    assert waits == [1, 2]


def test_gives_up_after_retries_without_leaking_key():
    session = FakeSession([requests.ConnectionError("https://...?key=SECRET_KEY")] * 5)
    yt, waits = make_yt(session)
    with pytest.raises(YouTubeError, match="gave up after 5 attempts") as err:
        yt.get("videos", id="x")
    assert "SECRET_KEY" not in str(err.value)
    assert waits == [1, 2, 4, 8]


def test_quota_exceeded_raises_immediately():
    yt, waits = make_yt(FakeSession([api_error(403, "quotaExceeded")]))
    with pytest.raises(QuotaExceeded, match="quota exceeded after 1 units"):
        yt.get("videos", id="x")
    assert waits == []


def test_client_error_is_not_retried():
    yt, _ = make_yt(FakeSession([api_error(404, "playlistNotFound")]))
    with pytest.raises(YouTubeError, match="HTTP 404"):
        yt.get("playlistItems", playlistId="x")
    assert yt.units == 1


def test_resolve_stores_channels_and_reports_unresolved(con):
    session = FakeSession()
    yt, _ = make_yt(session)
    assert resolve(con, yt, [CHANNEL, MISSING]) == ["@NoSuchChannel"]
    assert con.execute("SELECT channel_id, handle, tier, group_name, uploads_playlist, subscribers FROM channels").fetchall() == [
        ("UC_seattle", "@SeattleDataGuy", 1, "creators", "UU_seattle", 120000)
    ]
    resolve(con, yt, [CHANNEL])
    assert yt.units == 2  # already-resolved handle is skipped
    resolve(con, yt, [CHANNEL], refresh=True)
    assert yt.units == 3


def test_ingest_pages_playlist_and_stores_videos(con):
    session = FakeSession()
    yt, _ = make_yt(session)
    resolve(con, yt, [CHANNEL])
    session.calls.clear()

    stats = ingest(con, yt, [{**CHANNEL, "max_videos": 4}])
    assert stats == {"added": 4, "refreshed": 0, "failed_channels": []}
    assert session.endpoints() == ["playlistItems", "playlistItems", "videos"]
    assert session.calls[2][1]["id"] == "vid_1hour,vid_4min,vid_short,vid_live"
    rows = con.execute(
        "SELECT video_id, duration_seconds, views, likes, thumbnail_url, embeddable FROM videos ORDER BY published_at DESC"
    ).fetchall()
    assert rows == [
        ("vid_1hour", 3723, 50000, 2000, "https://i.ytimg.com/vi/vid_1hour/hqdefault.jpg", True),
        ("vid_4min", 240, 1200, None, "https://i.ytimg.com/vi/vid_4min/default.jpg", False),
        ("vid_short", 59, 800, 40, None, None),
        ("vid_live", 0, 300, None, None, None),
    ]


def test_ingest_skips_known_videos_and_refresh_repulls(con):
    session = FakeSession()
    yt, _ = make_yt(session)
    resolve(con, yt, [CHANNEL])
    ingest(con, yt, [CHANNEL])
    session.calls.clear()

    assert ingest(con, yt, [CHANNEL])["added"] == 0
    assert "videos" not in session.endpoints()

    con.execute("UPDATE videos SET views = 1")
    stats = ingest(con, yt, [CHANNEL], refresh=True)
    assert stats["refreshed"] == 5
    assert con.execute("SELECT count(*), min(views) FROM videos").fetchone() == (5, 300)


def test_ingest_continues_past_a_broken_channel(con):
    session = FakeSession()
    yt, _ = make_yt(session)
    resolve(con, yt, [CHANNEL])
    session.script = [api_error(404, "playlistNotFound")]
    stats = ingest(con, yt, [CHANNEL, MISSING])
    assert stats["added"] == 0
    assert stats["failed_channels"] == ["@SeattleDataGuy: playlistItems: HTTP 404 ['playlistNotFound']"]


def test_cli_requires_api_key(monkeypatch):
    monkeypatch.delenv("YT_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="YT_API_KEY is not set"):
        main(["ingest"])
