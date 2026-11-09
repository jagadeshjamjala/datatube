"""Load and validate config/channels.yaml and config/taxonomy.yaml."""
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CHANNEL_GROUPS = ("creators", "vendors_and_projects", "conferences")
TIERS = (1, 2)


class ConfigError(ValueError):
    pass


def _read(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise ConfigError(f"{Path(path).name}: expected a mapping at top level")
    return data


def _require(item, fields: tuple[str, ...], where: str) -> None:
    if not isinstance(item, dict):
        raise ConfigError(f"{where}: expected a mapping, got {item!r}")
    for field in fields:
        if item.get(field) in (None, ""):
            raise ConfigError(f"{where}: missing '{field}'")


def _positive_int(value, where: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{where}: must be a positive integer, got {value!r}")


def load_channels(path: Path = CONFIG_DIR / "channels.yaml") -> dict:
    data = _read(path)
    name = Path(path).name
    unknown = set(data) - {"defaults", *CHANNEL_GROUPS}
    if unknown:
        raise ConfigError(f"{name}: unknown group(s) {sorted(unknown)}")
    defaults = data.get("defaults") or {}
    _require(defaults, ("max_videos", "min_duration_seconds"), f"{name}: defaults")
    for key in ("max_videos", "min_duration_seconds"):
        _positive_int(defaults[key], f"{name}: defaults.{key}")

    channels, seen = [], set()
    for group in CHANNEL_GROUPS:
        for i, ch in enumerate(data.get(group) or []):
            where = f"{name}: {group}[{i}]"
            _require(ch, ("handle", "tier"), where)
            handle = ch["handle"].lower()
            if handle in seen:
                raise ConfigError(f"{where}: duplicate handle '{ch['handle']}'")
            seen.add(handle)
            if ch["tier"] not in TIERS:
                raise ConfigError(f"{where}: tier must be one of {TIERS}, got {ch['tier']!r}")
            ch = {"max_videos": defaults["max_videos"], **ch, "group": group}
            _positive_int(ch["max_videos"], f"{where}: max_videos")
            channels.append(ch)
    if not channels:
        raise ConfigError(f"{name}: no channels defined")
    return {"defaults": defaults, "channels": channels}


def load_taxonomy(path: Path = CONFIG_DIR / "taxonomy.yaml") -> dict:
    data = _read(path)
    name = Path(path).name
    seen = set()
    for i, topic in enumerate(data.get("topics") or []):
        where = f"{name}: topics[{i}]"
        _require(topic, ("id", "label", "hint"), where)
        if topic["id"] in seen:
            raise ConfigError(f"{where}: duplicate topic id '{topic['id']}'")
        seen.add(topic["id"])
    if not seen:
        raise ConfigError(f"{name}: no topics defined")
    for key in ("levels", "formats"):
        values = data.get(key)
        if not isinstance(values, list) or not values:
            raise ConfigError(f"{name}: '{key}' must be a non-empty list")
        if len(set(values)) != len(values):
            raise ConfigError(f"{name}: '{key}' has duplicates")
    return data
