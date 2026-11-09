import json
from functools import partial
from types import SimpleNamespace

import pytest

from datatube import classify as clf
from datatube.config import load_taxonomy
from datatube.db import connect, upsert

TAXONOMY = load_taxonomy()


def video(vid, title="Kafka streaming deep dive", seconds=1800, views=100, description="", channel_id="c1"):
    return {"video_id": vid, "channel_id": channel_id, "title": title, "description": description,
            "duration_seconds": seconds, "views": views}


def answer(vid, **overrides):
    return {"video_id": vid, "is_de": True, "confidence": 0.9, "primary_topic": "streaming",
            "topics": ["streaming"], "level": "advanced", "format": "talk", "evergreen": True,
            "summary": " How Kafka works. ", **overrides}


class FakeClient:
    """Stands in for anthropic.Anthropic(): records requests, answers via `respond(videos)`."""

    def __init__(self, respond=None, stop_reason="tool_use"):
        self.requests = []
        self.respond = respond or (lambda videos: [answer(v["video_id"]) for v in videos])
        self.stop_reason = stop_reason
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        videos = json.loads(kwargs["messages"][0]["content"])
        content = [SimpleNamespace(type="tool_use", name=clf.TOOL_NAME,
                                   input={"classifications": self.respond(videos)})]
        return SimpleNamespace(content=content, stop_reason=self.stop_reason,
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


@pytest.fixture
def con(tmp_path):
    con = connect(tmp_path / "test.duckdb")
    upsert(con, "channels", [{"channel_id": "c1", "handle": "@c1", "title": "Channel One"}])
    yield con
    con.close()


def test_request_is_a_forced_tool_call(monkeypatch):
    monkeypatch.setenv("DATATUBE_MODEL", "claude-test-model")
    client = FakeClient()
    clf.LLMClassifier(client, TAXONOMY)([{**video("v1", description="x" * 2000), "channel": "Channel One"}])
    request = client.requests[0]
    assert request["model"] == "claude-test-model"
    assert request["tool_choice"] == {"type": "tool", "name": "record_classifications"}
    assert request["tools"][0]["strict"] is True
    assert "temperature" not in request
    sent = json.loads(request["messages"][0]["content"])[0]
    assert sent == {"video_id": "v1", "title": "Kafka streaming deep dive", "channel": "Channel One",
                    "minutes": 30, "description": "x" * 700}


def test_default_model(monkeypatch):
    monkeypatch.delenv("DATATUBE_MODEL", raising=False)
    assert clf.default_model() == "claude-haiku-5-5"


def test_tool_schema_uses_taxonomy_enums():
    item = clf.tool(TAXONOMY)["input_schema"]["properties"]["classifications"]["items"]
    assert "migration" in item["properties"]["primary_topic"]["enum"]
    assert item["properties"]["format"]["enum"] == TAXONOMY["formats"]
    assert set(item["required"]) == set(item["properties"])


def test_classify_batches_skips_done_and_short_videos(con):
    upsert(con, "videos", [video(f"v{i:02}", views=i) for i in range(45)] + [video("short", seconds=59)])
    client = FakeClient()
    fn = clf.LLMClassifier(client, TAXONOMY)
    assert clf.classify(con, fn, min_duration=240, limit=30) == 30
    assert [len(json.loads(r["messages"][0]["content"])) for r in client.requests] == [20, 10]
    assert json.loads(client.requests[0]["messages"][0]["content"])[0]["video_id"] == "v44"  # most viewed first

    assert clf.classify(con, fn, min_duration=240) == 15  # re-run only does the rest
    assert clf.classify(con, fn, min_duration=240) == 0
    assert con.execute("SELECT count(*) FROM classifications WHERE video_id = 'short'").fetchone()[0] == 0
    assert (fn.input_tokens, fn.output_tokens) == (3000, 600)


def test_model_output_is_cleaned(con, monkeypatch):
    monkeypatch.delenv("DATATUBE_MODEL", raising=False)
    upsert(con, "videos", [video("v1"), video("v2")])

    def respond(videos):
        return [answer("v1", confidence=1.7, primary_topic="sql", topics=["streaming", "sql", "lakehouse", "migration"]),
                answer("v1", summary="duplicate, ignored"),
                answer("not-requested")]  # v2 missing from the answer

    clf.classify(con, clf.LLMClassifier(FakeClient(respond), TAXONOMY), min_duration=240)
    rows = con.execute("SELECT video_id, confidence, primary_topic, topics, summary, model FROM classifications").fetchall()
    assert rows == [("v1", 1.0, "sql", ["sql", "streaming", "lakehouse"], "How Kafka works.", "claude-haiku-5-5")]
    assert [v["video_id"] for v in clf.pending_videos(con, 240)] == ["v2"]  # retried next run


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unfinished_response_leaves_batch_for_next_run(con, stop_reason):
    upsert(con, "videos", [video("v1")])
    fn = clf.LLMClassifier(FakeClient(stop_reason=stop_reason), TAXONOMY)
    assert clf.classify(con, fn, min_duration=240) == 0
    assert fn.failed_batches == 1
    assert len(clf.pending_videos(con, 240)) == 1


def test_array_sent_as_json_string_is_parsed(con):
    upsert(con, "videos", [video("v1")])
    fn = clf.LLMClassifier(FakeClient(lambda videos: json.dumps([answer("v1"), "junk"])), TAXONOMY)
    assert clf.classify(con, fn, min_duration=240) == 1


@pytest.mark.parametrize("bad", ["not json", "{}", None])
def test_malformed_classifications_fail_the_batch_not_the_run(con, bad):
    upsert(con, "videos", [video("v1")])
    fn = clf.LLMClassifier(FakeClient(lambda videos: bad), TAXONOMY)
    assert clf.classify(con, fn, min_duration=240) == 0
    assert fn.failed_batches == 1


def test_llm_run_replaces_offline_rows(con):
    upsert(con, "videos", [video("v1")])
    clf.classify(con, partial(clf.classify_offline, taxonomy=TAXONOMY), min_duration=240)
    assert clf.classify(con, partial(clf.classify_offline, taxonomy=TAXONOMY), min_duration=240) == 0
    assert clf.classify(con, clf.LLMClassifier(FakeClient(), TAXONOMY, model="m"), 240, redo_offline=True) == 1
    assert con.execute("SELECT model FROM classifications").fetchall() == [("m",)]


def test_offline_heuristic():
    rows = clf.classify_offline([
        video("kafka", title="Apache Kafka tutorial for beginners"),
        video("cook", title="Best chocolate cake recipe", description="baking at home"),
        video("mig", title="Teradata to Snowflake migration lessons", description="moving a legacy warehouse"),
    ], TAXONOMY)
    by_id = {r["video_id"]: r for r in rows}
    assert (by_id["kafka"]["is_de"], by_id["kafka"]["primary_topic"]) == (True, "streaming")
    assert (by_id["kafka"]["level"], by_id["kafka"]["format"]) == ("beginner", "tutorial")
    assert by_id["cook"]["is_de"] is False
    assert (by_id["mig"]["is_de"], by_id["mig"]["primary_topic"]) == (True, "migration")
    assert all(r["model"] == "offline" and len(r["topics"]) <= 3 for r in rows)


def test_evaluate_scores_is_de_and_topic():
    items = [{"video_id": "a", "is_de": True, "primary_topic": "sql"},
             {"video_id": "b", "is_de": True, "primary_topic": "streaming"},
             {"video_id": "c", "is_de": False, "primary_topic": None},
             {"video_id": "d", "is_de": False, "primary_topic": None}]
    preds = {"a": {"video_id": "a", "is_de": True, "primary_topic": "sql"},
             "b": {"video_id": "b", "is_de": True, "primary_topic": "sql"},
             "c": {"video_id": "c", "is_de": True, "primary_topic": "sql"}}  # d: no prediction
    result = clf.evaluate(items, lambda batch: [preds[i["video_id"]] for i in batch if i["video_id"] in preds])
    assert (result["is_de_accuracy"], result["topic_accuracy"], result["missing"]) == (0.5, 0.5, 1)
    assert [m["item"]["video_id"] for m in result["misses"]] == ["b", "c", "d"]


def test_eval_set_labels_are_valid():
    items = clf.load_eval()
    topic_ids = {t["id"] for t in TAXONOMY["topics"]}
    assert 25 <= len(items) <= 40
    assert len({i["video_id"] for i in items}) == len(items)
    for item in items:
        assert isinstance(item["is_de"], bool), item["video_id"]
        assert (item["primary_topic"] in topic_ids) if item["is_de"] else item["primary_topic"] is None, item["video_id"]


def test_eval_runs_offline():
    result = clf.evaluate(clf.load_eval(), partial(clf.classify_offline, taxonomy=TAXONOMY))
    assert result["n"] == len(clf.load_eval()) and result["missing"] == 0
