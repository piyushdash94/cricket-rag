"""Checks from the Space review (Nov 2026): rate samples, scorecard initials, run-out names."""

from __future__ import annotations

import duckdb

from cricrag.agent.validate import initials_match
from cricrag.finder import evidence, qualifier, sample_problem
from cricrag.story import facts, match_balls

Q1 = "who had the best strike rate against left arm spinners in 2022?"


def test_rate_ranking_needs_a_qualified_sample():
    bare = sample_problem(Q1, ["player", "strike_rate"], [("Obed McCoy", 350.0)])
    assert bare and "runs, balls" in bare and "at least 60 balls" in bare  # both fixes in one retry
    tiny = sample_problem(Q1, ["player", "runs", "balls", "strike_rate"], [("Obed McCoy", 7, 2, 350.0)])
    assert tiny and "at least 60 balls" in tiny
    assert sample_problem(Q1, ["player", "runs", "balls", "sr"], [("Shubman Gill", 100, 65, 153.8)]) is None
    asked = "best strike rate v left-arm spin in 2022, minimum 30 balls"
    assert qualifier(asked) == 30
    assert sample_problem(asked, ["player", "runs", "balls", "sr"], [("Devon Conway", 71, 29, 244.8)])


def test_single_rate_needs_its_counts_but_no_qualifier():
    q = "how much did MS dhoni average against left arm fast bowlers?"
    assert sample_problem(q, ["average"], [(39.04,)])
    assert sample_problem(q, ["runs", "balls", "dismissals", "average"], [(976, 572, 25, 39.04)]) is None
    assert qualifier(q) is None
    # a year column is not a sample; team averages need a count, not balls
    assert sample_problem("Kohli's strike rate in 2016", ["year", "strike_rate"], [(2016, 152.0)])
    assert (
        sample_problem("average first innings score in 2023", ["innings", "avg_runs"], [(70, 183.2)]) is None
    )


def test_evidence_states_the_qualification():
    res = {
        "cols": ["player", "balls", "sr"],
        "rows": [("Shubman Gill", 65, 153.8)],
        "note": "ranked among players with at least 60 balls",
    }
    assert "at least 60 balls; say so" in evidence(res)


def test_first_initial_must_lead():
    assert initials_match("Krunal Pandya", "Player of the match: KH Pandya.")
    assert not initials_match("Hardik Pandya", "Player of the match: KH Pandya.")
    assert initials_match("Cameron White", "CL White c Rayudu b Bumrah 7")


def _final():
    con = duckdb.connect()
    con.execute(
        "create table matches as select 1::bigint match_id, '2017' season, 'Final' title, "
        "date '2017-05-21' start_date, 'Hyderabad' venue, 'MI' team1, 'RPS' team2, 'MI won by 1 run' status_text"
    )
    con.execute(
        "create table cs_innings_card as select 1::bigint match_id, 1 inning, 'MI' team, 129 runs, 8 wickets, 120 balls"
    )
    con.execute(
        "create table cs_batting_card as select * from (values (1::bigint, 1, 471342::bigint, 'KH Pandya', 47, 38),"
        " (1, 1, 625371, 'HH Pandya', 10, 8)) t(match_id, inning, player_id, player, runs, balls)"
    )
    con.execute(
        "create table cs_bowling_card as select 1::bigint match_id, 1 inning, 1::bigint player_id, "
        "'JD Unadkat' player, 24 balls, 19 conceded, 2 wickets"
    )
    con.execute(
        "create table cs_awards as select 1::bigint match_id, 471342::bigint player_id, 'KH Pandya' player"
    )
    con.execute(
        "create table player_styles as select * from (values (471342::bigint, 'Krunal Pandya'),"
        " (625371, 'Hardik Pandya'), (1, 'Jaydev Unadkat')) t(player_id, name)"
    )
    return con


def test_facts_use_full_names_not_scorecard_initials():
    lines = " ".join(facts(_final(), 1))
    assert "Krunal Pandya 47 (38)" in lines and "Player of the match: Krunal Pandya." in lines
    assert "KH Pandya" not in lines and "Jaydev Unadkat 2/19" in lines


def test_non_striker_run_out_names_the_batter_who_was_out():
    con = duckdb.connect()
    con.execute("""create table balls as select * from (values
        (1::bigint, 2, 1, 20, '19.6', 2, true, false, false, false, false, 'Christian, Johnson', 'Dan Christian',
         'RPS', 'run out', 'Washington Sundar')) t(match_id, inning, sequence, over_number, over_label, runs_total,
         is_wicket, is_four, is_six, is_wide, is_noball, bowler, batter, batting_team, commentary, player_out)""")
    balls, _ = match_balls(con, 1)
    assert balls[0]["out"] == "Washington Sundar"


def test_notes_carry_sample_and_style_filter():
    from cricrag.finder import notes

    sql = "select sum(runs_bat) runs from balls where batter='MS Dhoni' and bowl_arm = 'left' AND bowl_kind='pace'"
    n = notes(
        "how much did MS dhoni average against left arm fast bowlers?", sql, ["runs", "balls", "average"]
    )
    assert "bowl_arm = 'left'" in n and "bowl_kind = 'pace'" in n and "runs, balls and dismissals" in n
    assert "at least" not in n
    assert notes("who scored the most runs in 2016?", "select 1", ["player", "runs"]) == ""


def test_footnote_states_sample_filter_and_qualification():
    from cricrag.finder import footnote

    sql = "select ... where batter='MS Dhoni' and bowl_arm = 'left' and bowl_kind = 'pace'"
    res = {"sql": sql, "cols": ["runs", "balls", "dismissals", "average"], "rows": [(976, 572, 25, 39.04)]}
    assert footnote("how much did MS dhoni average against left arm fast bowlers?", res) == (
        "Sample: 976 runs, 572 balls, 25 dismissals. Bowlers: left-arm pace of every speed (not only fast)."
    )
    sql = "select ... where year = 2022 and bowl_kind = 'spin' and bowl_arm = 'left' having balls >= 60"
    res = {
        "sql": sql,
        "cols": ["player", "runs", "balls", "strike_rate"],
        "rows": [("Shubman Gill", 100, 65, 153.85), ("KL Rahul", 80, 63, 127.0)],
    }
    assert (
        footnote(Q1, res)
        == "Top row: 100 runs, 65 balls. Bowlers: left-arm spin. Qualification: at least 60 balls."
    )
    assert footnote("who hit the most sixes in 2016?", res) == ""
