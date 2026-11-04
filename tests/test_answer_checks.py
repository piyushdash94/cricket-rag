"""Checks from the Space review (Nov 2026): rate samples, scorecard initials, run-out names."""

from __future__ import annotations

import duckdb

from cricrag.agent.validate import initials_match
from cricrag.finder import evidence, qualifier, sample_problem
from cricrag.story import facts, match_balls

Q1 = "who had the best strike rate against left arm spinners in 2022?"


def test_rate_ranking_needs_a_qualified_sample():
    bare = sample_problem(Q1, ["player", "strike_rate"], [("Obed McCoy", 350.0)])
    assert (
        bare and "runs, balls" in bare and "at least 30 balls" in bare
    )  # both fixes in one retry; matchup: 30
    tiny = sample_problem(Q1, ["player", "runs", "balls", "strike_rate"], [("Obed McCoy", 7, 2, 350.0)])
    assert tiny and "at least 30 balls" in tiny
    assert qualifier("best strike rate in 2022") == 60  # not a matchup: the general bar
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
        "Sample: 976 runs, 572 balls, 25 dismissals. Bowlers: left-arm pace."
    )
    sql = "select ... where year = 2022 and bowl_kind = 'spin' and bowl_arm = 'left' having balls >= 60"
    res = {
        "sql": sql,
        "cols": ["player", "runs", "balls", "strike_rate"],
        "rows": [("Shubman Gill", 100, 65, 153.85), ("KL Rahul", 80, 63, 127.0)],
    }
    assert (
        footnote(Q1, res)
        == "Top row: 100 runs, 65 balls. Bowlers: left-arm spin. Qualification: at least 30 balls."
    )
    assert (
        footnote(
            "who hit the most sixes in 2016?", {**res, "sql": "select batter, sum(is_six::int) from balls"}
        )
        == ""
    )


def test_bowler_types_are_pace_or_spin_with_the_arm():
    from types import SimpleNamespace

    from cricrag.finder import guard
    from cricrag.styles import classify

    assert classify("Right arm Medium fast") == ("right arm medium fast", "pace", "right")
    assert classify("left-arm fast") == ("left-arm fast", "pace", "left")
    assert classify("Slow left-arm orthodox")[1:] == ("spin", "left")
    assert classify("legbreak googly")[1] == "spin"
    assert classify("") == ("does not bowl", "none", "")
    frame = SimpleNamespace(players=["MS Dhoni"], teams=[], years=[], question="Dhoni v left-arm fast")
    assert "bowl_kind" in guard(
        "select 1 from balls where batter='MS Dhoni' and bowl_style='left-arm fast'", frame
    )
    assert (
        guard("select 1 from balls where batter='MS Dhoni' and bowl_kind='pace' and bowl_arm='left'", frame)
        is None
    )


def test_fill_gives_every_player_a_hand_and_a_bowling_type(tmp_path):
    from cricrag.styles import fill

    db = tmp_path / "a.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""create table player_styles (player_id bigint, name varchar, bat_hand varchar, bowl_style varchar,
                   bowl_kind varchar, bowl_arm varchar, source varchar)""")
    con.execute("""insert into player_styles values (1, 'Rishabh Pant', 'left', '', '', '', 'espn'),
        (2, 'Ben Rohrer', 'left', '', '', '', 'espn'), (3, 'Nitin Menon', 'right', '', '', '', 'espn')""")
    con.execute("create table balls as select 1::bigint batter_id, 9::bigint bowler_id union all select 9, 2")
    con.execute("create table cs_batting_card as select 1::bigint player_id")
    con.execute("create table cs_bowling_card as select 2::bigint player_id")
    con.execute(
        "insert into player_styles values (9, 'A Bowler', 'right', 'right-arm fast', 'pace', 'right', 'espn')"
    )
    con.close()
    manual = tmp_path / "m.csv"
    manual.write_text("player_id,name,bat_hand,bowl_style,source\n2,Ben Rohrer,left,right-arm medium,wiki\n")
    assert fill(db, manual) == {
        "manual": 1,
        "marked_does_not_bowl": 1,
        "no_batting_hand": [],
        "no_bowling_type": [],
    }
    con = duckdb.connect(str(db), read_only=True)
    got = dict(con.execute("select name, bowl_kind from player_styles").fetchall())
    assert got == {
        "Rishabh Pant": "none",
        "Ben Rohrer": "pace",
        "Nitin Menon": "",
        "A Bowler": "pace",
    }  # umpire untouched


def test_player_against_a_bowling_type_is_a_matchup():
    from cricrag.agent.pandit import TYPE_VS

    for q in (
        "how does Rohit Sharma bat against left arm spin?",
        "Kohli vs pace in the powerplay",
        "Dhoni against left-arm fast bowlers",
        "Bumrah against left-handers",
    ):
        assert TYPE_VS.search(q), q
    assert not TYPE_VS.search("how did Rohit Sharma do against CSK?")
    assert not TYPE_VS.search("Kohli v Bumrah")


def test_matchup_dismissals_are_bowler_credited_and_counted_per_match():
    from types import SimpleNamespace

    from cricrag.finder import guard

    f = SimpleNamespace(players=["Rohit Sharma"], teams=[], years=[], question="Rohit v left-arm spin")
    base = "select sum(runs_bat) from balls where batter = 'Rohit Sharma' and bowl_kind = 'spin' and bowl_arm = 'left'"
    assert "restarts in every match" in guard(
        base.replace(
            "sum(runs_bat)", "count(distinct case when player_out = 'Rohit Sharma' then sequence end)"
        ),
        f,
    )
    assert "bowler_wicket" in guard(
        base.replace("sum(runs_bat)", "sum((player_out = 'Rohit Sharma')::int)"), f
    )
    ok = base.replace("sum(runs_bat)", "sum((bowler_wicket and player_out = 'Rohit Sharma')::int)")
    assert guard(ok, f) is None
    assert guard(base.replace("sum(runs_bat)", "count(distinct (match_id, inning, sequence))"), f) is None


def test_footnote_on_a_matchup_without_a_rate_word():
    from cricrag.finder import footnote

    sql = "select ... where batter = 'Rohit Sharma' and bowl_kind = 'spin' and bowl_arm = 'left'"
    res = {
        "sql": sql,
        "cols": ["runs", "balls", "dismissals", "strike_rate"],
        "rows": [(895, 795, 22, 112.58)],
    }
    assert footnote("how does Rohit Sharma bat against left arm spin?", res) == (
        "Sample: 895 runs, 795 balls, 22 dismissals. Bowlers: left-arm spin."
    )


def test_batter_matchup_must_return_dismissals():
    sql = "select sum(runs_bat) runs from balls where batter = 'Rohit Sharma' and bowl_kind = 'spin'"
    q = "how does Rohit Sharma bat against spin?"
    assert "dismissals" in sample_problem(q, ["runs", "balls", "sr"], [(895, 795, 112.58)], sql)
    assert sample_problem(q, ["runs", "balls", "dismissals", "sr"], [(895, 795, 22, 112.58)], sql) is None


def test_spin_types_map_every_archive_style():
    from cricrag.styles import spin_type_sql

    con = duckdb.connect()
    con.execute("""create table s as select * from (values ('spin', 'right-arm offbreak'), ('spin', 'legbreak googly'),
        ('spin', 'legbreak'), ('spin', 'slow left-arm orthodox'), ('spin', 'left-arm wrist-spin'),
        ('pace', 'right-arm fast')) t(bowl_kind, bowl_style)""")
    got = [r[0] for r in con.execute(f"select {spin_type_sql('s')} from s").fetchall()]
    assert got == ["off-spin", "leg-spin", "leg-spin", "left-arm orthodox", "left-arm wrist-spin", None]


def test_footnote_names_spin_type_and_hand():
    from cricrag.finder import footnote

    res = {
        "sql": "select ... where batter = 'Virat Kohli' and spin_type = 'leg-spin'",
        "cols": ["runs", "balls", "dismissals"],
        "rows": [(700, 520, 12)],
    }
    assert (
        footnote("Kohli against leg-spin", res)
        == "Sample: 700 runs, 520 balls, 12 dismissals. Bowlers: leg-spin."
    )
    res = {
        "sql": "select ... where bowler = 'Jasprit Bumrah' and bat_hand = 'left'",
        "cols": ["balls", "runs", "wickets", "economy"],
        "rows": [(900, 1000, 40, 6.67)],
    }
    assert footnote("Bumrah economy against left-handers", res).endswith("Batters: left-handed.")


def test_bowlers_have_arms_and_all_time_means_every_season():
    from cricrag.agent.pandit import bowler_arm, mismatch

    assert bowler_arm("which left handed fast bowler has taken most wickets at death?") == (
        "which left-arm fast bowler has taken most wickets at death?"
    )
    assert bowler_arm("best left-handed spinner in 2024") == "best left-arm spinner in 2024"
    assert (
        bowler_arm("Bumrah against left-handed batters") == "Bumrah against left-handed batters"
    )  # batters keep hands
    s = "Bowlers by wickets - death overs (16-20), pace bowlers (2008-2025)"
    assert mismatch("most death wickets in all seasons", s, latest=2026) == "all_time_got_closed_range"
    assert mismatch("most death wickets from 2008 to 2025", s, latest=2026) is None
    assert mismatch("most death wickets in all seasons", s.replace("2025", "2026"), latest=2026) is None


def test_ranking_answer_is_the_first_row():
    from cricrag.finder import evidence, top_line, top_name

    res = {"cols": ["bowler", "balls", "wickets", "balls_per_wicket"],
           "rows": [("Krunal Pandya", 44, 2, 22.0), ("Ravindra Jadeja", 81, 3, 27.0)]}  # fmt: skip
    q = "which left arm spinner has the best strike rate against Suryakumar Yadav?"
    assert top_name(q, res) == "Krunal Pandya"
    assert top_line(res) == "Krunal Pandya (balls 44, wickets 2, balls per wicket 22) [S1]"
    assert "the first row is the answer" in evidence(res)
    assert top_name("how did Kohli bat against leg-spin?", res) is None  # not a ranking


def test_batter_matchup_needs_runs_too():
    sql = "select ... where batter = 'Virat Kohli' and spin_type = 'leg-spin'"
    q = "what was Virat Kohli's strike rate against leg spinners in 2025?"
    assert "runs" in sample_problem(q, ["balls", "dismissals", "strike_rate"], [(70, 1, 140.0)], sql)
    assert (
        sample_problem(q, ["runs", "balls", "dismissals", "strike_rate"], [(98, 70, 1, 140.0)], sql) is None
    )


def test_planner_on_the_wrong_side_of_the_ball():
    from cricrag.agent.pandit import role_mismatch

    bat = [
        {"name": "slice_stats", "args": {"subject": "batter", "metric": "runs", "player": ["Jasprit Bumrah"]}}
    ]
    bowl = [
        {
            "name": "slice_stats",
            "args": {"subject": "bowler", "metric": "bowling_average", "player": ["MS Dhoni"]},
        }
    ]
    q1 = "What is Jasprit Bumrah's economy against left-handed batters in the death overs?"
    assert role_mismatch(q1, bat) == "bowling_measure_on_batter_slice"
    q2 = "how much does Dhoni average against left arm bowlers at Chepauk?"
    assert role_mismatch(q2, bowl) == "batting_matchup_on_bowler_slice"
    assert (
        role_mismatch(
            "how does Kohli bat against spin?", [{"name": "slice_stats", "args": {"subject": "batter"}}]
        )
        is None
    )
    assert (
        role_mismatch("Bumrah's economy in 2024", [{"name": "slice_stats", "args": {"subject": "bowler"}}])
        is None
    )
    assert role_mismatch("most runs in 2016", [{"name": "batting_leaders", "args": {}}]) is None
