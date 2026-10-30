"""The UI's API, offline: a real Pandit over the fixture archive, no LLM, no cricbot checkout."""

import json

import pytest
from fastapi.testclient import TestClient

from cricrag import server
from cricrag.agent.pandit import Pandit, Trace
from cricrag.agent.validate import Sources
from test_events_and_pandit import NoLLM, edb, retriever  # noqa: F401  (fixtures)


@pytest.fixture
def client(edb, retriever, catalog, archive, monkeypatch, tmp_path):  # noqa: F811
    from cricrag.config import settings

    monkeypatch.chdir(tmp_path)  # no ./.env: a developer's CRICBOT_ROOT must not leak into these tests
    monkeypatch.setenv("CRICBOT_DB", str(archive))
    monkeypatch.delenv("CRICBOT_ROOT", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    settings.cache_clear()
    yield TestClient(server.create_app(lambda: Pandit(retriever, catalog, NoLLM(), edb)))
    settings.cache_clear()


def stream(client, message, **ui):
    r = client.post("/api/agent/chat/stream", json={"message": message, "ui_context": ui})
    assert r.status_code == 200
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def test_trace_sees_every_step_as_it_is_appended():
    seen = []
    t = Trace([{"step": "frame"}], seen.append)
    t.append({"step": "route"})
    assert seen == list(t) == [{"step": "frame"}, {"step": "route"}]


def test_story_streams_trace_then_opens_the_match(client, monkeypatch):
    monkeypatch.setattr("cricrag.agent.pandit.find_matches", lambda f: [{"match_id": 1473511}])
    lines = stream(client, "Where did the 2025 final turn?")
    kinds = [x["event"] for x in lines]
    assert kinds[-1] == "result" and kinds.count("result") == 1 and kinds[0] == "trace"
    nodes = [x["data"]["node"] for x in lines[:-1]]
    assert nodes[0] == "frame" and nodes[-1] == "finalize" and "program" in nodes
    assert [x["data"]["sequence"] for x in lines[:-1]] == list(range(1, len(nodes) + 1))
    result = lines[-1]["data"]
    assert "RCB won by 6 runs" in result["reply"] and result["model_used"] is False
    assert result["ui_actions"] == [
        {"type": "open_match", "series_slug": "ipl-2025", "match_slug": "rcb-vs-pbks-1473511"}
    ]


def test_ambiguous_name_comes_back_with_options(client):
    lines = stream(client, "How many runs did Pandya score in 2024?")
    card = next(c for c in lines[-1]["data"]["cards"] if c["type"] == "clarify")
    assert {o["label"] for o in card["options"]} == {"Hardik Pandya", "Krunal Pandya"}
    assert "Hardik Pandya" in next(o["question"] for o in card["options"] if o["label"] == "Hardik Pandya")


def test_open_match_names_itself_for_this_match_questions():
    q, added = server.with_match_context(
        "Where did this match turn?", {"teams": ["RCB", "PBKS"], "match_season": 2025, "match_label": "Final"}
    )
    assert q == "Where did this match turn? (RCB vs PBKS, IPL 2025, Final)" and added
    assert (
        server.with_match_context("Most sixes in 2024", {"teams": ["RCB"], "match_season": 2025})[1] is None
    )


def test_stats_answers_carry_their_tables():
    class Ans:
        text, program, mode, model, problems, seconds = "x [S1]", "stats", "llm", "m", [], 0.1
        frame: dict = {}  # noqa: RUF012
        trace = [{"step": "stats", "calls": [{"name": "leaders", "args": {"year": 2024}}]}]  # noqa: RUF012
        sources = Sources(
            tables=[{"type": "table", "title": "Most sixes", "columns": ["player"], "rows": []}]
        )

    card = server.result_of(Ans(), "Most sixes in 2024", None)["cards"][0]
    assert card["type"] == "stats" and card["blocks"][0]["title"] == "Most sixes"
    assert card["calls"][0]["name"] == "leaders"


def test_archive_routes_explain_a_missing_checkout(client):
    r = client.get("/api/ipl/matches?season=2025")
    assert r.status_code == 503 and "CRICBOT_ROOT" in r.json()["detail"]
    assert client.get("/api/agent/status").json()["provider"] is None
    assert next(n["id"] for n in client.get("/api/agent/graph").json()["nodes"]) == "frame"
    assert client.get("/api/docs/catalog").json()["nodes"]


def test_match_page_extras_come_from_the_archive(archive, tmp_path):
    import duckdb

    from cricrag import matchpage

    ev = duckdb.connect(str(tmp_path / "e.duckdb"))
    ev.execute(
        "create table events as select 'drop' type, 1254071::bigint match_id, 1 inning, 1 \"over\", '0.6' label, "
        "'Rashid Khan' player, 'Mayank Agarwal' player2, 'high' detail"
    )
    con = duckdb.connect(str(archive), read_only=True)
    balls = [{"inning": 1, "label": "0.6"}, {"inning": 2, "label": "19.3"}]
    detail = {
        "balls": balls,
        "commentary_count": 0,
        "sources": [{"name": "Commentary", "available": False}],
        "analytics": {"shifts": ["cricbot's"]},
    }
    matchpage.enrich(detail, con, ev, 1254071)
    assert detail["commentary_count"] == 2 and "dropped at deep midwicket" in balls[0]["commentary"]
    assert detail["sources"][0]["available"] is True
    (d,) = detail["drops"]
    assert (d["fielder"], d["batter"], d["bowler"]) == ("Rashid Khan", "Mayank Agarwal", "Abhishek Sharma")
    assert (d["score_then"], d["added"], d["out"]) == (2, 0, False)
    assert detail["analytics"]["shifts"] != ["cricbot's"]


def test_hall_of_fame_lists(archive):
    import duckdb

    from cricrag.matchpage import hall_of_fame

    con = duckdb.connect(str(archive), read_only=True)
    hof = hall_of_fame(con)
    runs = next(x for x in hof["lists"] if x["title"] == "Most runs")
    assert runs["rows"][0] == {"name": "Shashank Singh", "value": 6}
    titles = next(x for x in hof["lists"] if x["title"] == "Most titles")
    assert titles["rows"] == [{"name": "RCB", "value": 1}]


def test_news_keeps_ipl_stories_and_sorts_them():
    from cricrag.news import articles

    feed = {
        "articles": [
            {
                "headline": "IPL retains the Impact Player rule",
                "description": "",
                "links": {"web": {"href": "http://www.espn.com/cricket/story/_/id/1/x"}},
            },
            {
                "headline": "Root's 40th Test hundred",
                "description": "",
                "links": {"web": {"href": "https://www.espn.com/cricket/story/_/id/2/y"}},
            },
            {
                "headline": "Fastest fifty in IPL history",
                "description": "",
                "links": {"web": {"href": "https://www.espn.com/cricket/story/_/id/3/z"}},
            },
        ]
    }
    got = articles(None, lambda: feed)
    assert [(a["kind"], a["url"][:12]) for a in got["live"]] == [
        ("rules", "https://www."),
        ("records", "https://www."),
    ]
    assert {r["kind"] for r in got["reading"]} == {"rules", "trends", "records"}
    assert all(r["url"].startswith("https://") for r in got["reading"])
    assert articles(None, lambda: 1 / 0)["live"] == []  # offline: the reading list still shows
