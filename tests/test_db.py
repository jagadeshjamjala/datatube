import pytest

from datatube.db import connect, upsert

VIDEOS = [
    {"video_id": "v1", "channel_id": "c1", "title": "Intro to dbt", "views": 100},
    {"video_id": "v2", "channel_id": "c1", "title": "Kafka deep dive", "views": 200},
]


@pytest.fixture
def con(tmp_path):
    con = connect(tmp_path / "test.duckdb")
    yield con
    con.close()


def count(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_connect_creates_tables_and_is_rerunnable(tmp_path):
    path = tmp_path / "test.duckdb"
    connect(path).close()
    con = connect(path)
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert tables == {"channels", "videos", "classifications"}


def test_upsert_twice_does_not_duplicate_and_latest_wins(con):
    upsert(con, "videos", VIDEOS)
    assert upsert(con, "videos", [{**VIDEOS[0], "views": 999}, VIDEOS[1]]) == 2
    assert count(con, "videos") == 2
    assert con.execute("SELECT views FROM videos WHERE video_id = 'v1'").fetchone()[0] == 999


def test_partial_upsert_keeps_other_columns(con):
    upsert(con, "videos", VIDEOS)
    upsert(con, "videos", [{"video_id": "v1", "views": 5}])
    assert con.execute("SELECT title, views FROM videos WHERE video_id = 'v1'").fetchone() == ("Intro to dbt", 5)


def test_list_column_round_trips(con):
    upsert(con, "classifications", [{"video_id": "v1", "topics": ["sql", "migration"]}])
    assert con.execute("SELECT topics FROM classifications").fetchone()[0] == ["sql", "migration"]


def test_upsert_empty_is_noop(con):
    assert upsert(con, "videos", []) == 0


@pytest.mark.parametrize(
    "table, rows, message",
    [
        ("nope", [{"a": 1}], "unknown table 'nope'"),
        ("videos", [{"video_id": "v1", "bogus; DROP": 1}], r"unknown column\(s\)"),
    ],
)
def test_upsert_rejects_unknown_names(con, table, rows, message):
    with pytest.raises(ValueError, match=message):
        upsert(con, table, rows)


def test_connect_adds_embeddable_to_an_older_database(tmp_path):
    import duckdb
    path = tmp_path / "old.duckdb"
    old = duckdb.connect(str(path))
    old.execute("CREATE TABLE videos (video_id VARCHAR PRIMARY KEY, title VARCHAR)")
    old.execute("INSERT INTO videos VALUES ('v1', 'Intro to dbt')")
    old.close()
    con = connect(path)
    assert con.execute("SELECT video_id, embeddable FROM videos").fetchall() == [("v1", None)]
    con.close()
