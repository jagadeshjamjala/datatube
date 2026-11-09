"""YouTube Data API v3: resolve channel handles and ingest their uploads.

Only 1-unit endpoints are used (channels, playlistItems, videos); never search.list.
"""
import re
import time
from datetime import datetime, timezone

import requests

from datatube.db import upsert

BASE_URL = "https://www.googleapis.com/youtube/v3/"
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError"}
BATCH = 50  # max ids per videos.list call and items per playlistItems page


class YouTubeError(RuntimeError):
    pass


class QuotaExceeded(RuntimeError):
    pass


class YouTube:
    def __init__(self, api_key: str, session=None, sleep=time.sleep, retries: int = 4):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.sleep = sleep
        self.retries = retries
        self.units = 0

    def get(self, endpoint: str, **params) -> dict:
        problem = ""
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(2 ** (attempt - 1))
            self.units += 1
            try:
                resp = self.session.get(BASE_URL + endpoint, params={**params, "key": self.api_key}, timeout=30)
            except requests.RequestException as e:
                # Not str(e): requests puts the full URL, including the API key, in its messages.
                problem = type(e).__name__
                continue
            if resp.status_code == 200:
                return resp.json()
            reasons = _error_reasons(resp)
            if "quotaExceeded" in reasons:
                raise QuotaExceeded(f"YouTube daily quota exceeded after {self.units} units this run")
            problem = f"HTTP {resp.status_code} {sorted(reasons)}"
            if resp.status_code not in RETRY_STATUS and not reasons & RETRY_REASONS:
                raise YouTubeError(f"{endpoint}: {problem}")
        raise YouTubeError(f"{endpoint}: gave up after {self.retries + 1} attempts ({problem})")


def _error_reasons(resp) -> set[str]:
    try:
        return {e.get("reason", "") for e in resp.json()["error"]["errors"]}
    except (ValueError, KeyError, TypeError):
        return set()


_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")


def parse_duration(value: str | None) -> int | None:
    """ISO-8601 duration ("PT1H2M3S") to seconds; None if missing or malformed."""
    match = _DURATION.fullmatch(value or "")
    if not match:
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _int(value) -> int | None:
    return int(value) if value is not None else None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).replace(tzinfo=None) if value else None


def _channel_row(item: dict, cfg: dict) -> dict:
    return {
        "channel_id": item["id"],
        "handle": cfg["handle"],
        "title": item["snippet"]["title"],
        "group_name": cfg["group"],
        "tier": cfg["tier"],
        "uploads_playlist": item["contentDetails"]["relatedPlaylists"]["uploads"],
        "subscribers": _int(item.get("statistics", {}).get("subscriberCount")),  # absent when hidden
        "fetched_at": _now(),
    }


def _video_row(item: dict) -> dict:
    snippet, stats = item["snippet"], item.get("statistics", {})
    thumbs = snippet.get("thumbnails", {})
    thumb = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}
    return {
        "video_id": item["id"],
        "channel_id": snippet["channelId"],
        "title": snippet["title"],
        "description": snippet.get("description", ""),
        "published_at": _timestamp(snippet.get("publishedAt")),
        "duration_seconds": parse_duration(item.get("contentDetails", {}).get("duration")),
        "views": _int(stats.get("viewCount")),
        "likes": _int(stats.get("likeCount")),  # absent when likes are hidden
        "comments": _int(stats.get("commentCount")),
        "thumbnail_url": thumb.get("url"),
        "embeddable": item.get("status", {}).get("embeddable"),
        "fetched_at": _now(),
    }


def resolve(con, yt: YouTube, channels: list[dict], refresh: bool = False) -> list[str]:
    """Store channel ID and uploads playlist for each handle. Returns handles not found."""
    known = {h for (h,) in con.execute("SELECT lower(handle) FROM channels").fetchall()}
    unresolved = []
    for cfg in channels:
        if not refresh and cfg["handle"].lower() in known:
            continue
        data = yt.get("channels", part="snippet,contentDetails,statistics", forHandle=cfg["handle"])
        items = data.get("items") or []
        if items:
            upsert(con, "channels", [_channel_row(items[0], cfg)])
        else:
            unresolved.append(cfg["handle"])
    return unresolved


def _playlist_video_ids(yt: YouTube, playlist_id: str, limit: int) -> list[str]:
    ids, token = [], None
    while len(ids) < limit:
        page = {"pageToken": token} if token else {}
        data = yt.get("playlistItems", part="contentDetails", playlistId=playlist_id, maxResults=BATCH, **page)
        ids += [item["contentDetails"]["videoId"] for item in data.get("items", [])]
        token = data.get("nextPageToken")
        if not token:
            break
    return ids[:limit]


def fetch_videos(con, yt: YouTube, video_ids: list[str]) -> int:
    """videos.list in batches of 50, upserting what comes back. Returns rows stored."""
    stored = 0
    for i in range(0, len(video_ids), BATCH):
        data = yt.get("videos", part="snippet,contentDetails,statistics,status", id=",".join(video_ids[i:i + BATCH]))
        stored += upsert(con, "videos", [_video_row(item) for item in data.get("items", [])])
    return stored


def ingest(con, yt: YouTube, channels: list[dict], refresh: bool = False) -> dict:
    """Fetch each resolved channel's uploads. Skips stored videos unless refresh."""
    playlists = dict(con.execute("SELECT lower(handle), uploads_playlist FROM channels").fetchall())
    known = {v for (v,) in con.execute("SELECT video_id FROM videos").fetchall()}
    stats = {"added": 0, "refreshed": 0, "failed_channels": []}
    for cfg in channels:
        playlist = playlists.get(cfg["handle"].lower())
        if not playlist:
            continue  # not resolved yet
        try:
            ids = _playlist_video_ids(yt, playlist, cfg["max_videos"])
            new = [v for v in ids if v not in known]
            fetch_videos(con, yt, ids if refresh else new)
        except YouTubeError as e:
            # One broken channel shouldn't stop the run; a re-run picks up where it left off.
            stats["failed_channels"].append(f"{cfg['handle']}: {e}")
            continue
        stats["added"] += len(new)
        stats["refreshed"] += len(ids) - len(new) if refresh else 0
        known.update(ids)
    return stats
