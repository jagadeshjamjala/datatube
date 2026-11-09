import pytest
import yaml

from datatube.config import ConfigError, load_channels, load_taxonomy

GOOD_CHANNELS = {
    "defaults": {"max_videos": 500, "min_duration_seconds": 240},
    "creators": [{"handle": "@a", "tier": 1}],
    "conferences": [{"handle": "@b", "tier": 2, "max_videos": 50}],
}
GOOD_TAXONOMY = {
    "topics": [{"id": "sql", "label": "SQL", "hint": "queries"}],
    "levels": ["beginner"],
    "formats": ["talk"],
}


def write(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_real_channels_load():
    cfg = load_channels()
    assert cfg["defaults"] == {"max_videos": 500, "min_duration_seconds": 240}
    assert {c["group"] for c in cfg["channels"]} == {"creators", "vendors_and_projects", "conferences"}


def test_real_taxonomy_loads_and_has_migration():
    tax = load_taxonomy()
    assert "migration" in {t["id"] for t in tax["topics"]}
    assert 14 <= len(tax["topics"]) <= 18
    assert tax["levels"] == ["beginner", "intermediate", "advanced"]
    assert tax["formats"] == ["tutorial", "talk", "deep_dive", "project", "opinion", "interview", "product"]


def test_channel_defaults_applied(tmp_path):
    channels = load_channels(write(tmp_path, GOOD_CHANNELS))["channels"]
    assert [(c["handle"], c["max_videos"], c["group"]) for c in channels] == [
        ("@a", 500, "creators"),
        ("@b", 50, "conferences"),
    ]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"creators": [{"tier": 1}]}, r"creators\[0\]: missing 'handle'"),
        ({"creators": [{"handle": "@a", "tier": 1}, {"handle": "@A", "tier": 2}]}, "duplicate handle '@A'"),
        ({"creators": [{"handle": "@a", "tier": 5}]}, "tier must be one of"),
        ({"creators": [{"handle": "@a", "tier": 1, "max_videos": 0}]}, "max_videos: must be a positive integer"),
        ({"podcasts": []}, r"unknown group\(s\) \['podcasts'\]"),
        ({"defaults": {"max_videos": 500}}, "defaults: missing 'min_duration_seconds'"),
    ],
)
def test_bad_channels(tmp_path, change, message):
    with pytest.raises(ConfigError, match=message):
        load_channels(write(tmp_path, {**GOOD_CHANNELS, **change}))


@pytest.mark.parametrize(
    "change, message",
    [
        ({"topics": [{"id": "sql", "label": "A", "hint": "h"}, {"id": "sql", "label": "B", "hint": "h"}]},
         "duplicate topic id 'sql'"),
        ({"topics": [{"id": "sql", "hint": "h"}]}, r"topics\[0\]: missing 'label'"),
        ({"topics": []}, "no topics defined"),
        ({"formats": []}, "'formats' must be a non-empty list"),
    ],
)
def test_bad_taxonomy(tmp_path, change, message):
    with pytest.raises(ConfigError, match=message):
        load_taxonomy(write(tmp_path, {**GOOD_TAXONOMY, **change}))
