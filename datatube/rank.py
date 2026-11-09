"""Rank classified videos with one SQL query. Tune the weights below until the top 20 look right."""

TIER1_BONUS = 2.0        # core data engineering channel
EVERGREEN_BONUS = 1.5    # flat bonus instead of freshness
FRESH_MAX = 3.0          # a brand-new video's boost, fading by e every year
LIKE_RATE_WEIGHT = 100.0
LIKE_PRIOR_VIEWS = 1000  # damps the like rate of barely-watched videos
PRODUCT_PENALTY = 3.0    # vendor demos and launches
CONFIDENCE_WEIGHT = 5.0  # times (1 - confidence)
MIN_CONFIDENCE = 0.6

SQL = """
SELECT v.video_id, v.title, v.published_at, v.duration_seconds, v.views, v.likes, v.thumbnail_url, v.embeddable,
       c.title AS channel, c.tier,
       k.primary_topic, k.topics, k.level, k.format, k.evergreen, k.confidence, k.summary,
         ln(1 + coalesce(v.views, 0))
       + $like_w * coalesce(v.likes, 0) / (coalesce(v.views, 0) + $prior)
       + CASE WHEN c.tier = 1 THEN $tier1 ELSE 0 END
       + CASE WHEN k.evergreen THEN $evergreen
              ELSE coalesce($fresh * exp(-date_diff('day', v.published_at, current_date) / 365.25), 0) END
       - CASE WHEN k.format = 'product' THEN $product ELSE 0 END
       - $conf_w * (1 - k.confidence)
         AS score
FROM videos v
JOIN classifications k USING (video_id)
LEFT JOIN channels c USING (channel_id)
WHERE k.is_de AND k.confidence >= $min_conf AND v.duration_seconds >= $min_duration
ORDER BY score DESC, v.video_id
"""


def ranked(con, min_duration: int, limit: int | None = None) -> list[dict]:
    """DE videos worth showing, best first, each with its score."""
    params = {"like_w": LIKE_RATE_WEIGHT, "prior": LIKE_PRIOR_VIEWS, "tier1": TIER1_BONUS,
              "evergreen": EVERGREEN_BONUS, "fresh": FRESH_MAX, "product": PRODUCT_PENALTY,
              "conf_w": CONFIDENCE_WEIGHT, "min_conf": MIN_CONFIDENCE, "min_duration": min_duration}
    sql = SQL + (f" LIMIT {int(limit)}" if limit is not None else "")
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
