"""DuckDB storage: one file, four tables. Raw facts and LLM output stay separate."""
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "datatube.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id       VARCHAR PRIMARY KEY,
    handle           VARCHAR,
    title            VARCHAR,
    group_name       VARCHAR,
    tier             INTEGER,
    uploads_playlist VARCHAR,
    subscribers      BIGINT,
    fetched_at       TIMESTAMP
);
CREATE TABLE IF NOT EXISTS videos (
    video_id         VARCHAR PRIMARY KEY,
    channel_id       VARCHAR,
    title            VARCHAR,
    description      VARCHAR,
    published_at     TIMESTAMP,
    duration_seconds INTEGER,
    views            BIGINT,
    likes            BIGINT,
    comments         BIGINT,
    thumbnail_url    VARCHAR,
    fetched_at       TIMESTAMP
);
-- Added after the first release; NULL means not fetched yet (ingest --refresh fills it).
ALTER TABLE videos ADD COLUMN IF NOT EXISTS embeddable BOOLEAN;
CREATE TABLE IF NOT EXISTS classifications (
    video_id      VARCHAR PRIMARY KEY,
    is_de         BOOLEAN,
    confidence    DOUBLE,
    primary_topic VARCHAR,
    topics        VARCHAR[],
    level         VARCHAR,
    format        VARCHAR,
    evergreen     BOOLEAN,
    summary       VARCHAR,
    model         VARCHAR,
    classified_at TIMESTAMP
);
"""


def connect(path: Path | str = DB_PATH) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def _columns(con, table: str) -> list[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table]
    ).fetchall()
    if not rows:
        raise ValueError(f"unknown table '{table}'")
    return [r[0] for r in rows]


def upsert(con, table: str, rows: list[dict]) -> int:
    """Insert rows, replacing any with the same primary key. Returns rows written."""
    if not rows:
        return 0
    cols = list(rows[0])
    # Identifiers can't be bound as parameters, so check them against the schema.
    unknown = set(cols) - set(_columns(con, table))
    if unknown:
        raise ValueError(f"{table}: unknown column(s) {sorted(unknown)}")
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    con.executemany(sql, [[row[c] for c in cols] for row in rows])
    return len(rows)
