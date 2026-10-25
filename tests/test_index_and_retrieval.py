import duckdb
import pytest

from cricrag.index.commentary import BALLS_SQL, build, build_chunks
from cricrag.retrieval.hybrid import Filters, Retriever, rrf
from cricrag.retrieval.models import HashEmbedder, OverlapReranker
from cricrag.text import numbers


@pytest.fixture(scope="module")
def chunks(archive):
    con = duckdb.connect(str(archive), read_only=True)
    out = build_chunks(con.sql(BALLS_SQL).fetchall())
    con.close()
    return {c["id"]: c for c in out}


@pytest.fixture(scope="module")
def retriever(archive, tmp_path_factory):
    con = duckdb.connect(str(archive), read_only=True)
    tbl, n = build(
        con,
        HashEmbedder(),
        tmp_path_factory.mktemp("lance"),
        tmp_path_factory.mktemp("cache"),
        log=lambda *_: None,
    )
    con.close()
    assert n == 5  # five distinct overs in the fixture
    return Retriever(tbl, HashEmbedder(), OverlapReranker())


def test_chunk_header_is_sql_facts(chunks):
    c = chunks["1473511:2:19"]
    assert c["stage"] == "final" and c["phase"] == "death" and c["bowling_team"] == "RCB"
    assert "This over: 6 runs, 0 wickets" in c["header"]
    assert c["players"] == "|Shashank Singh|Kyle Jamieson|Bhuvneshwar Kumar|"
    assert c["events"] == "|six|"


def test_bold_and_html_stripped(chunks):
    c = chunks["1254071:1:1"]
    assert "<b>" not in c["body"] and "bold" in c["events"]
    assert c["bold"] == "dropped at deep midwicket!"


def test_cumulative_score_and_wickets(chunks):
    assert chunks["1473511:2:18"]["score"] == "0/1"
    assert chunks["1473511:2:19"]["score"] == "6/1"


def test_rrf_rewards_agreement():
    a = [{"id": "x"}, {"id": "y"}]
    b = [{"id": "y"}, {"id": "z"}]
    assert rrf({"bm25": a, "dense": b})[0].id == "y"


def test_filters_sql_is_quoted():
    w = Filters(seasons=["2025"], teams=["RCB"], stage="final", players=["O'Brien"]).where()
    assert "season IN ('2025')" in w and "stage = 'final'" in w and "O''Brien" in w


@pytest.mark.parametrize("mode", ["bm25", "dense", "hybrid", "hybrid_rerank"])
def test_retrieval_modes_find_the_drop(retriever, mode):
    res = retriever.search("dropped at deep midwicket", Filters(seasons=["2021"]), k=3, mode=mode)
    assert res.hits and res.hits[0].id == "1254071:1:1"


def test_prefilter_excludes_other_matches(retriever):
    res = retriever.search("yorker", Filters(seasons=["2025"], stage="final"), k=10, mode="hybrid")
    assert {h.row["match_id"] for h in res.hits} == {1473511}


def test_soft_player_filter_relaxes(retriever):
    res = retriever.search("yorker", Filters(seasons=["2021"], players=["Nobody Here"]), k=2, mode="hybrid")
    assert res.relaxed and res.hits


def test_numbers_normalised():
    assert numbers("Kohli scored 9,336 runs at 7.50 in 2016") == {"9336", "7.5", "2016"}
