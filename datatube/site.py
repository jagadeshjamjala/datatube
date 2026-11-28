"""Build the static site: a fixed index.html plus data.js holding the ranked videos."""
import base64
import hashlib
import json
import re
import shutil
from datetime import date
from pathlib import Path

from datatube.rank import learning_paths, ranked

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"
PREVIEW = TEMPLATE.with_name("og.png")  # link-preview image named in the page's og:image tag


def _video(r: dict) -> dict:
    """Only what a card needs; the thumbnail URL is derived from the id in the browser."""
    return {
        "id": r["video_id"],
        "title": r["title"],
        "channel": r["channel"] or "",
        "duration": r["duration_seconds"],
        "views": r["views"] or 0,
        "published": r["published_at"].date().isoformat() if r["published_at"] else "",
        "level": r["level"],
        "format": r["format"],
        "topics": r["topics"] or [],
        "evergreen": bool(r["evergreen"]),
        "summary": r["summary"] or "",
        "embeddable": r["embeddable"] is not False,  # unknown (NULL): try the player, it has a YouTube link
    }


def _sha256(page: str, tag: str) -> str:
    """CSP hash of the page's single inline <script> or <style> block."""
    body = re.search(rf"<{tag}>(.*?)</{tag}>", page, re.S).group(1)
    return "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"


def _with_csp(page: str) -> str:
    """Allow only this page's own inline code (by hash), data.js, YouTube thumbnails and the player.
    Hashed at build time, so editing the template never leaves a stale policy."""
    csp = "; ".join([
        "default-src 'none'",
        f"script-src 'self' {_sha256(page, 'script')}",
        f"style-src {_sha256(page, 'style')}",
        "img-src https://i.ytimg.com",
        "frame-src https://www.youtube-nocookie.com",
        "base-uri 'none'",
        "form-action 'none'",
    ])
    return page.replace("__CSP__", csp)


def build(con, min_duration: int, taxonomy: dict, out: Path | None = None) -> int:
    """Write index.html and data.js into `out` (default site/). Returns the number of videos."""
    out = Path(out or SITE_DIR)
    out.mkdir(parents=True, exist_ok=True)
    rows = ranked(con, min_duration)
    videos = [_video(r) for r in rows]
    data = {
        "generated": date.today().isoformat(),
        "topics": [{"id": t["id"], "label": t["label"]} for t in taxonomy["topics"]],
        "levels": taxonomy["levels"],
        "formats": taxonomy["formats"],
        "videos": videos,
        "paths": learning_paths(rows),
        "roadmaps": [{k: r.get(k, "") for k in ("id", "name", "description", "topics")} for r in taxonomy.get("roadmaps") or []],
    }
    # A script file rather than JSON, so the page also works from file:// where fetch() is blocked.
    # json.dumps escapes non-ASCII (incl. U+2028); escaping "<" too keeps "</script>" inert even if inlined.
    payload = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    (out / "data.js").write_text(f"window.DATATUBE = {payload};\n")
    (out / "index.html").write_text(_with_csp(TEMPLATE.read_text()))
    shutil.copyfile(PREVIEW, out / "og.png")
    return len(videos)
