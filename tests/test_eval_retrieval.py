import duckdb

from cricrag.eval.retrieval import build_set, evaluate, summarise
from cricrag.index.commentary import build
from cricrag.retrieval.hybrid import Retriever
from cricrag.retrieval.models import HashEmbedder


def test_set_gold_points_at_the_wicket_over(archive):
    con = duckdb.connect(str(archive), read_only=True)
    items = build_set(con, None, n=10, allow_no_fielding=True)
    con.close()
    by = {i["kind"]: i for i in items}
    assert by["wicket"]["gold"] == ["1473511:2:18"] and "Omarzai" in by["wicket"]["question"]
    assert "caught by" not in by["wicket"]["question"]  # the bowler is never named as the catcher
    chases = sorted(i["gold"] for i in items if i["kind"] == "chase")  # last two overs of each chase
    assert chases == [["1254071:2:19", "1254071:2:20"], ["1473511:2:18", "1473511:2:19"]]


def test_evaluate_and_summarise(archive, catalog, tmp_path):
    con = duckdb.connect(str(archive), read_only=True)
    tbl, _ = build(con, HashEmbedder(), tmp_path / "l", tmp_path / "c", log=lambda *_: None)
    items = build_set(con, None, n=10, allow_no_fielding=True)
    con.close()
    for i in items:
        i["split"] = "test"
    res = evaluate(items, Retriever(tbl, HashEmbedder()), catalog, ["bm25", "hybrid"], [False])
    rows = summarise(res, "test")
    assert {r["mode"] for r in rows} == {"bm25", "hybrid"}
    assert all(r["hit@10"] == 1.0 for r in rows)
