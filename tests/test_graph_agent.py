"""PanditGraph routing, the relational writer/validator and the /api/graph routes (offline, fixture graph)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cricrag.agent.graph_agent import PanditGraph, route, validate
from cricrag.entities import Catalog, Player
from cricrag.graph.api import router
from cricrag.graph.build import build
from test_graph import CSK, MI, make


@pytest.fixture(scope="module")
def graph_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("agent")
    db, csv = make(tmp)
    out = tmp / "graph.duckdb"
    build(db, out, csv, log=lambda *_: None)
    return out


@pytest.fixture(scope="module")
def cat():
    return Catalog(season_years={"2024": 2024}, team_codes={"MI", "CSK"},
                   players=[Player(n, 1000 - i) for i, n in enumerate((MI | CSK).values())])  # fmt: skip


class FakeLLM:
    available = True

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def chat(self, msgs, max_tokens=400):
        self.calls += 1
        return {"message": {"content": self.replies.pop(0)}, "model": "fake"}


class FakePandit:
    def ask(self, question, history=None, on_step=None):
        from cricrag.agent.pandit import Answer
        from cricrag.agent.validate import Sources

        return Answer("stats answer", "stats", {}, Sources())


@pytest.mark.parametrize(
    ("q", "n", "program"),
    [
        ("Rohit Sharma vs Deepak Chahar", 2, "head_to_head"),
        ("Who is Rohit Sharma's nemesis?", 1, "nemesis"),
        ("Deepak Chahar's favourite victim", 1, "bunnies"),
        ("Which bowler did MS Dhoni take most catches off?", 1, "catch_pairs"),
        ("How are Rohit Sharma and MS Dhoni connected?", 2, "connection"),
        ("Who has Tilak Varma played with most?", 1, "teammates"),
        ("best fielder-bowler combination for CSK", 0, "catch_pairs"),
        ("network rank of bowlers in 2024", 0, "ranking"),
        ("Most runs by Rohit Sharma in 2024", 1, "pandit"),
        ("Where did the 2024 final turn?", 0, "pandit"),
    ],
)
def test_route(q, n, program):
    assert route(q, n, 0)[0] == program


def test_head_to_head_is_written_from_evidence(graph_db, cat):
    llm = FakeLLM(["Deepak Chahar dismissed Rohit Sharma once in 2 balls, conceding 4 runs [G1]."])
    agent = PanditGraph(cat, graph_db, llm)
    ans = agent.ask("Rohit Sharma vs Deepak Chahar")
    assert (ans.program, ans.mode) == ("relational", "llm")
    assert ans.extra["graph"]["tool"] == "head_to_head"
    assert {n["label"] for n in ans.extra["graph"]["nodes"]} == {"Rohit Sharma", "Deepak Chahar"}
    assert any(s.get("rule") == "two_players_matchup_cue" for s in ans.trace)


def test_bad_numbers_retry_then_fall_back_to_evidence(graph_db, cat):
    llm = FakeLLM(["Chahar got Rohit 9 times [G1].", "Still 9 [G1]."])
    ans = PanditGraph(cat, graph_db, llm).ask("Who is Rohit Sharma's nemesis?")
    assert llm.calls == 2 and ans.mode == "extractive"
    assert "numbers not in evidence: ['9']" in ans.problems
    assert ans.text.startswith("Deepak Chahar dismissed Rohit Sharma")


def test_gap_and_delegation(graph_db, cat):
    agent = PanditGraph(cat, graph_db, None, pandit_factory=FakePandit)
    assert agent.ask("Most runs by Rohit Sharma in 2024").text == "stats answer"
    gap = agent.ask("Who is Ravindra Jadeja's nemesis?")  # nobody dismissed him
    assert gap.mode == "gap" and "No bowler has dismissed Ravindra Jadeja" in gap.text
    assert "graph TD" in agent.mermaid() or "graph" in agent.mermaid()


def test_validate_rules():
    lines = ["A Bowler dismissed B Batter 3 times [G1]"]
    assert validate("A Bowler got B Batter 3 times [G1].", lines, "q") == []
    assert validate("A Bowler got B Batter 3 times.", lines, "q") == [
        "uncited numbers: A Bowler got B Batter 3 times."
    ]
    assert "name not in evidence: Virat Kohli" in validate("Virat Kohli 3 [G1].", lines, "q")


def test_api(graph_db):
    app = FastAPI()
    app.include_router(router(lambda: graph_db))
    c = TestClient(app)
    assert c.get("/api/graph/status").json()["counts"]["people"] == 8
    assert c.get("/api/graph/search", params={"q": "dhoni"}).json()[0]["name"] == "MS Dhoni"
    me = c.get("/api/graph/player/12").json()
    assert me["name"] == "MS Dhoni" and me["sections"][0]["tool"] == "nemesis"
    f = c.get("/api/graph/fielding", params={"team": "CSK"}).json()
    assert f["catches"]["rows"][0]["fielder"] == "MS Dhoni"
    assert c.get("/api/graph/rankings", params={"kind": "nope"}).status_code == 400
    assert c.get("/api/graph/communities").status_code == 200


@pytest.mark.parametrize(
    "q",
    [
        "What partnership did Rohit Sharma and Hardik Pandya put on against CSK?",
        "Where did the final turn for Rohit Sharma vs MS Dhoni?",
    ],
)
def test_pandit_first_cues(q):
    assert route(q, 2, 1) == ("pandit", "pandit_first_cue")


def test_teammates_with_no_balls_between_them_go_to_pandit(graph_db, cat):
    agent = PanditGraph(cat, graph_db, None, pandit_factory=FakePandit)
    ans = agent.ask("Rohit Sharma against Hardik Pandya")  # both MI batters: no matchup
    assert ans.text == "stats answer"
    assert any(s.get("rule") == "no_balls_between_players" for s in ans.trace)


def test_validator_reads_number_words_and_llm_punctuation():
    from cricrag.agent.graph_agent import clean

    lines = ["A Bowler dismissed B Batter 7 times [G1]"]
    assert validate(clean("A Bowler got B Batter seven times 【G1】."), lines, "q") == []
    assert validate("A Bowler got B Batter six times [G1].", lines, "q") == ["numbers not in evidence: ['6']"]
    assert validate("", lines, "q") == ["empty reply"]
    assert validate("Mumbai Indians: A Bowler 7 [G1].", lines, "q") == []
