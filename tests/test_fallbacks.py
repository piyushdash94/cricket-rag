"""Router fallbacks: scope/polarity mismatch, unsupported concepts, event leaderboards, finder guard."""

from cricrag import finder
from cricrag.agent.pandit import Pandit, mismatch
from cricrag.frame import build_frame


class NoLLM:
    available = False


def test_mismatch_names_the_rule():
    assert (
        mismatch("Who has the most ducks in the IPL?", "Ducks - best single seasons") == "scope_single_season"
    )
    assert mismatch("most ducks in a season", "Ducks - best single seasons") is None
    assert mismatch("lowest team total", "Highest team totals") == "polarity_high_for_low"
    assert mismatch("lowest economy in 2024", "Bowlers by economy (min 10 overs)") is None


def test_unsupported_concept_is_a_gap_without_queries(catalog):
    a = Pandit(None, catalog, NoLLM()).ask("Which captain has won the most matches?")
    assert a.program == "gap" and "captain" in a.text and a.trace[-1]["rule"] == "unsupported_concept"


def test_guard_blocks_writes_and_missing_frame_values(catalog):
    f = build_frame("Most runs for RCB in 2016", catalog)
    assert finder.guard("DROP TABLE x", f)
    assert finder.guard("SELECT 1; SELECT 2", f)
    assert "2016" in finder.guard("SELECT player FROM batting WHERE team='RCB'", f)
    assert (
        finder.guard("SELECT player, sum(runs) FROM batting WHERE team='RCB' AND year=2016 GROUP BY 1", f)
        is None
    )
    assert (
        finder.guard("SELECT count(*) FROM events WHERE type='drop' AND team='RCB' AND year=2016", f) is None
    )


def test_leaderboard_counts_events(catalog, archive, tmp_path):
    import duckdb

    from cricrag import events as ev

    path = tmp_path / "e.duckdb"
    ev.build(archive, path, None, log=lambda *_: None)
    con = duckdb.connect(str(path), read_only=True)
    label, rows = ev.leaderboard(con, build_frame("Who has the most ducks?", catalog))
    want = con.execute("SELECT count(*) FROM events WHERE type='wicket' AND value=0").fetchall()[0][0]
    assert label == "ducks" and sum(n for _, n in rows) == want
    assert ev.leaderboard(con, build_frame("Who scored the most runs?", catalog)) is None


def test_other_competition_is_refused(catalog):
    a = Pandit(None, catalog, NoLLM()).ask("Who won the Duleep Trophy in 2024?")
    assert a.program == "refuse" and a.trace[-1]["rule"] == "other_competition"


def test_stage_sql_keeps_2011_playoffs_out_of_finals():
    import duckdb

    from cricrag.archive import STAGE_SQL

    con = duckdb.connect()
    got = {
        t: con.execute(f"SELECT {STAGE_SQL} FROM (SELECT ? AS title)", [t]).fetchall()[0][0]
        for t in [
            "Final",
            "1st Qualifying Final",
            "Elimination Final",
            "2nd Semi-Final",
            "Qualifier 1",
            "Match 58",
        ]
    }
    assert got == {
        "Final": "final",
        "1st Qualifying Final": "playoff",
        "Elimination Final": "playoff",
        "2nd Semi-Final": "playoff",
        "Qualifier 1": "playoff",
        "Match 58": "league",
    }


def test_five_wicket_hauls_are_not_four_wicket_hauls(catalog):
    from cricrag import events as ev

    hit = next(x for x in ev.LEADER if x[1].search("Who has the most five-wicket hauls in the IPL?"))
    assert hit[0] == "5-wicket hauls" and "value >= 5" in hit[2]
    hit = next(x for x in ev.LEADER if x[1].search("most four-wicket hauls"))
    assert hit[0] == "4+ wicket hauls"


def test_result_question_on_a_named_final_uses_match_facts():
    from cricrag.agent.pandit import RESULT

    assert RESULT.search("By how many runs did Mumbai Indians win the 2017 IPL final?")
    assert not RESULT.search("Where did the 2025 final turn?")


def test_semi_final_is_not_the_final(catalog):
    assert build_frame("How was Rahul Dravid out in the IPL 2009 2nd Semi-Final?", catalog).stage == "playoff"
    assert build_frame("Elimination Final 2011", catalog).stage == "playoff"
    assert build_frame("Who won the 2016 final?", catalog).stage == "final"
    assert build_frame("who bowled the final over in 2016", catalog).stage is None


def test_which_season_answered_with_players_is_a_mismatch():
    q = "Which IPL season had the most centuries?"
    assert mismatch(q, "Batters by hundreds - best single seasons: Virat Kohli (2016) 4", True) == (
        "subject_season_got_players"
    )
    assert mismatch(q, "Hundreds by season: 2026 15, 2024 14", False) is None


def _innings(team, overs):
    """overs: list of (runs per ball list, wicket flags list) -> momentum-format balls."""
    balls = []
    for o, (runs, wkts) in enumerate(overs, 1):
        for b, (r, w) in enumerate(zip(runs, wkts, strict=True), 1):
            balls.append(
                {
                    "over": o,
                    "label": f"{o - 1}.{b}",
                    "runs": r,
                    "wicket": w,
                    "boundary": r >= 4,
                    "event": "W" if w else str(r),
                    "title": f"x to {team}{o}{b}",
                }
            )
    return balls


def test_decisive_shift_always_favours_the_winner():
    from cricrag.momentum import match_shifts

    calm = ([1, 1, 1, 1, 1, 1], [False] * 6)
    burst = ([6, 6, 4, 6, 4, 6], [False] * 6)
    collapse = ([0, 0, 0, 0, 0, 0], [True, False, True, False, True, False])
    a = _innings(
        "A", [calm] * 5 + [collapse, collapse] + [calm] * 11 + [burst, burst]
    )  # A collapses, then burst
    b = _innings("B", [calm] * 20)
    for x in a:
        x["inning"] = 1
    for x in b:
        x["inning"] = 2
    inn = [{"team": "A", "runs": sum(x["runs"] for x in a)}, {"team": "B", "runs": sum(x["runs"] for x in b)}]
    shifts = match_shifts(a + b, inn, winner="A")
    kinds = {s["type"] for s in shifts}
    assert {"scoring_burst", "squeeze"} <= kinds  # over-based detectors fire once balls carry the over
    assert any(s["favours"] == "B" for s in shifts)  # the loser's good spells stay in the list
    top = [s for s in shifts if s.get("decisive")]
    assert len(top) == 1 and top[0]["favours"] == "A"
    loser = [s for s in match_shifts(a + b, inn, winner="B") if s.get("decisive")]
    assert all(s["favours"] == "B" for s in loser)
