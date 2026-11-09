from datetime import datetime, timedelta

import pytest

from datatube.db import connect, upsert
from datatube.rank import ranked

NOW = datetime.now().replace(microsecond=0)


def video(vid, channel_id="core", views=10_000, likes=300, seconds=1800, days_old=800):
    return {"video_id": vid, "channel_id": channel_id, "title": vid, "views": views, "likes": likes,
            "duration_seconds": seconds, "published_at": NOW - timedelta(days=days_old)}


def label(vid, **overrides):
    return {"video_id": vid, "is_de": True, "confidence": 0.9, "primary_topic": "streaming",
            "topics": ["streaming"], "level": "advanced", "format": "deep_dive", "evergreen": True,
            "summary": "s", "model": "test", **overrides}


@pytest.fixture
def con(tmp_path):
    con = connect(tmp_path / "test.duckdb")
    upsert(con, "channels", [{"channel_id": "core", "title": "Core DE", "tier": 1},
                             {"channel_id": "vendor", "title": "Vendor", "tier": 2}])
    yield con
    con.close()


def ids(con):
    return [r["video_id"] for r in ranked(con, min_duration=240)]


def test_tier1_deep_dive_outranks_newer_more_viewed_vendor_demo(con):
    upsert(con, "videos", [video("deep_dive"),
                           video("demo", channel_id="vendor", views=50_000, likes=500, days_old=10)])
    upsert(con, "classifications", [label("deep_dive"),
                                    label("demo", format="product", evergreen=False)])
    assert ids(con) == ["deep_dive", "demo"]


def test_filters_non_de_low_confidence_short_and_unclassified(con):
    upsert(con, "videos", [video("keep"), video("not_de"), video("unsure"), video("short", seconds=100),
                           video("unclassified")])
    upsert(con, "classifications", [label("keep"), label("not_de", is_de=False),
                                    label("unsure", confidence=0.5), label("short")])
    assert ids(con) == ["keep"]


def test_rows_carry_site_fields_and_limit(con):
    upsert(con, "videos", [video("a", views=100, likes=3), video("b", views=100_000, likes=3_000)])
    upsert(con, "classifications", [label("a"), label("b")])
    [top] = ranked(con, min_duration=240, limit=1)
    assert top["video_id"] == "b" and top["channel"] == "Core DE" and top["topics"] == ["streaming"]
    assert isinstance(top["score"], float)
