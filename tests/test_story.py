import duckdb

from cricrag.entities import codes
from cricrag.retrieval.hybrid import Filters
from cricrag.story import match_balls, match_story


def test_facts_come_from_scorecards(archive):
    con = duckdb.connect(str(archive), read_only=True)
    st = match_story(con, 1473511)
    con.close()
    facts = "\n".join(st["facts"])
    assert (
        "RCB won by 6 runs" in facts
        and "RCB 190/9 in 20.0 overs" in facts
        and "Player of the match: Krunal Pandya" in facts
    )


def test_balls_feed_the_momentum_detector(archive):
    con = duckdb.connect(str(archive), read_only=True)
    balls, innings = match_balls(con, 1473511, drops={(1473511, 2, "18.2"): "Rajat Patidar"})
    con.close()
    assert [i["team"] for i in innings] == ["RCB", "PBKS"]
    assert balls[0]["event"] == "4" and any(b["event"] == "W" for b in balls)
    chance = next(b for b in balls if b["label"] == "18.2")
    assert chance["chance"] and chance["chance_by"] == "Rajat Patidar"


def test_franchise_codes_are_expanded_in_filters():
    assert codes("PBKS") == ["PBKS", "KXIP"]
    assert "team1 IN ('PBKS','KXIP')" in Filters(teams=["PBKS"]).where()
