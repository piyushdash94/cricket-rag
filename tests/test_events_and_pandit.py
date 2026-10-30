import duckdb
import pytest

from cricrag import events as ev
from cricrag.agent.pandit import Pandit
from cricrag.frame import build_frame
from cricrag.index.commentary import build as build_index
from cricrag.retrieval.hybrid import Retriever
from cricrag.retrieval.models import HashEmbedder


@pytest.fixture(scope="module")
def edb(archive, tmp_path_factory):
    path = tmp_path_factory.mktemp("ev") / "events.duckdb"
    assert ev.build(archive, path, None, log=lambda *_: None) > 0
    con = duckdb.connect(str(path), read_only=True)
    yield con
    con.close()


@pytest.fixture(scope="module")
def retriever(archive, tmp_path_factory):
    con = duckdb.connect(str(archive), read_only=True)
    tbl, _ = build_index(
        con, HashEmbedder(), tmp_path_factory.mktemp("l"), tmp_path_factory.mktemp("c"), log=lambda *_: None
    )
    con.close()
    return Retriever(tbl, HashEmbedder())


class NoLLM:
    available = False


class FakeLLM:
    available = True

    def __init__(self, text):
        self.text = text

    def chat(self, messages, tools=None, max_tokens=900):
        return {"message": {"content": self.text}, "model": "fake"}

    def evict(self, *a, **k):
        pass


def test_wicket_event_has_fielder_and_over(edb, catalog):
    rows, total = ev.query(
        edb, build_frame("How was Azmatullah Omarzai out in the 2025 final?", catalog), ["wicket"]
    )
    assert total == 1 and rows[0]["chunk_id"] == "1473511:2:18"
    assert "caught by Manoj Bhandage off Yash Dayal" in ev.describe_row(rows[0])


def test_finishes_flag_close_results(edb, catalog):
    rows, _ = ev.query(edb, build_frame("close finishes in 2025", catalog), ["finish"])
    assert rows and all(r["close"] for r in rows) and "RCB won by 6 runs" in ev.describe_row(rows[0])


def test_cues_and_roles():
    assert ev.event_types("who dropped Mayank?") == ["drop"]
    assert ev.event_types("fastest fifty in 2024")[0] == "fifty"
    assert (
        ev.team_role("RCB's best death bowler") == "bowling"
        and ev.team_role("how did RCB chase") == "batting"
    )


def test_event_program_writes_from_evidence(edb, retriever, catalog, monkeypatch):
    monkeypatch.setattr("cricrag.agent.pandit.find_matches", lambda f: [])
    p = Pandit(retriever, catalog, FakeLLM("Omarzai was caught by Manoj Bhandage off Yash Dayal [E1]."), edb)
    ans = p.ask("How was Azmatullah Omarzai out in the 2025 final?")
    assert (ans.program, ans.mode, ans.problems) == ("event", "llm", [])


def test_story_program_without_llm(edb, retriever, catalog, archive, monkeypatch):
    from cricrag.config import settings

    monkeypatch.setenv("CRICBOT_DB", str(archive))
    settings.cache_clear()
    monkeypatch.setattr("cricrag.agent.pandit.find_matches", lambda f: [{"match_id": 1473511}])
    ans = Pandit(retriever, catalog, NoLLM(), edb).ask("Where did the 2025 final turn?")
    assert ans.program == "story" and ans.mode == "extractive" and "RCB won by 6 runs" in ans.text
    settings.cache_clear()


def test_invented_number_falls_back(edb, retriever, catalog, monkeypatch):
    monkeypatch.setattr("cricrag.agent.pandit.find_matches", lambda f: [])
    p = Pandit(retriever, catalog, FakeLLM("Omarzai was out for 99 [E1]."), edb)
    ans = p.ask("How was Azmatullah Omarzai out in the 2025 final?")
    assert ans.mode == "extractive" and any("99" in x for x in ans.problems)


def test_citation_styles_are_normalised(edb, retriever, catalog, monkeypatch):
    monkeypatch.setattr("cricrag.agent.pandit.find_matches", lambda f: [])
    p = Pandit(
        retriever,
        catalog,
        FakeLLM("Azmatullah Omarzai was caught by Manoj Bhandage off Yash Dayal【E1】."),
        edb,
    )
    ans = p.ask("How was Azmatullah Omarzai out in the 2025 final?")
    assert ans.mode == "llm" and ans.text.endswith("[E1].")


def test_per_year_tables_get_code_totals():
    from cricrag.cricbot import totals

    block = {
        "type": "table",
        "columns": ["year", "balls", "runs", "outs", "sr"],
        "rows": [[2013, 5, 12, 1, 240.0], [2014, 15, 18, 2, 120.0]],
    }
    assert totals(block) == "\nAll years combined (computed): balls 20, runs 30, outs 3, strike rate 150.0."
    assert totals({**block, "rows": [*block["rows"], ["all", 20, 30, 3, 150.0]]}) == ""


def test_team_phase_fallback_without_stats_engine(edb, retriever, catalog, archive, monkeypatch):
    from cricrag.config import settings

    monkeypatch.setenv("CRICBOT_DB", str(archive))
    settings.cache_clear()
    monkeypatch.setattr("cricrag.agent.pandit.find_matches", lambda f: [])
    ans = Pandit(retriever, catalog, NoLLM(), edb).ask("What was PBKS's death-overs run rate in 2025?")
    assert ans.program == "team_phase" and "PBKS batting, death, 2025: run rate" in ans.text
    settings.cache_clear()


def test_fielding_counts_from_events(edb, catalog):
    kind, rows = ev.fielding_counts(edb, build_frame("Who took the most catches in 2025?", catalog))
    assert kind == "catches" and rows == [("Manoj Bhandage", 1)]


def test_moment_cues_do_not_swallow_rankings():
    assert "finish" not in ev.event_types("Who finished second in the run charts in 2022?")
    assert "wicket" not in ev.event_types("What was the fastest delivery bowled in 2024?")
    assert "wicket" in ev.event_types("How was Kohli bowled in the final?") or "wicket" in ev.event_types(
        "Kohli was bowled"
    )


def test_superlative_event_questions_route_to_events(edb, retriever, catalog, monkeypatch):
    monkeypatch.setattr("cricrag.agent.pandit.find_matches", lambda f: [])
    a = Pandit(retriever, catalog, NoLLM(), edb).ask("Biggest finish of 2025?")
    assert a.program == "event"


def test_collapse_means_a_wicket_cluster_and_follow_ups_find_the_match(catalog, archive, monkeypatch):
    from cricrag.config import settings
    from cricrag.frame import build_frame

    assert "collapse" in ev.event_types("which match had the biggest batting collapse in 2026?")
    monkeypatch.setenv("CRICBOT_DB", str(archive))
    settings.cache_clear()
    hist = [
        {"role": "user", "content": "where did the 2025 final turn?"},
        {
            "role": "assistant",
            "content": "In IPL 2025 Final (RCB v PBKS), Krunal Pandya's spell turned it [E1].",
        },
    ]
    f = build_frame("give me some commentary snippets from this match", catalog, hist)
    assert f.match_id == 1473511 and set(f.teams) == {"RCB", "PBKS"}
    assert build_frame("give me commentary from this match", catalog, hist[:1]).match_id is None
    settings.cache_clear()


def test_collapse_query_filters_wicket_clusters(edb, catalog):
    from cricrag.frame import build_frame

    rows, _ = ev.query(edb, build_frame("biggest collapse in 2025", catalog), ["collapse"])
    assert all(r["type"] == "shift" and r["detail"] == "wicket_cluster" for r in rows)
