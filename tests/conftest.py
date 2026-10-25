"""Offline fixtures: a tiny archive with cricbot's column names, and a LanceDB index built with the hash embedder."""

from __future__ import annotations

import duckdb
import pytest

from cricrag.entities import Catalog, Player

MATCHES = [
    (
        1473511,
        "2025",
        "Final",
        "2025-06-03",
        "Narendra Modi Stadium, Ahmedabad",
        "RCB",
        "PBKS",
        "RCB",
        "RCB won by 6 runs",
    ),
    (
        1254071,
        "2021",
        "37th Match",
        "2021-09-25",
        "Sharjah Cricket Stadium",
        "PBKS",
        "SRH",
        "PBKS",
        "PBKS won by 5 runs",
    ),
]
# (match, inning, over, ball, batting, batter, non_striker, bowler, runs, wicket, four, six, out, dismissal, short, commentary)
BALLS = [
    (
        1473511,
        2,
        19,
        1,
        "PBKS",
        "Shashank Singh",
        "Kyle Jamieson",
        "Bhuvneshwar Kumar",
        6,
        False,
        False,
        True,
        "",
        "",
        "Kumar to Shashank Singh, SIX",
        "Bhuvi misses the yorker and gets tonked over long-on",
    ),
    (
        1473511,
        2,
        19,
        2,
        "PBKS",
        "Shashank Singh",
        "Kyle Jamieson",
        "Bhuvneshwar Kumar",
        0,
        False,
        False,
        False,
        "",
        "",
        "Kumar to Shashank Singh, no run",
        "Reversing yorker lands perfectly",
    ),
    (
        1473511,
        2,
        18,
        2,
        "PBKS",
        "Azmatullah Omarzai",
        "Shashank Singh",
        "Yash Dayal",
        0,
        True,
        False,
        False,
        "Azmatullah Omarzai",
        "caught",
        "Yash Dayal to Azmatullah Omarzai, OUT",
        "Proper collapse on now! Taken at short third, Omarzai top edged the cutter",
    ),
    (
        1473511,
        1,
        20,
        1,
        "RCB",
        "Romario Shepherd",
        "Krunal Pandya",
        "Arshdeep Singh",
        4,
        False,
        True,
        False,
        "",
        "",
        "Arshdeep Singh to Shepherd, FOUR",
        "Full and wide, carved over point",
    ),
    (
        1254071,
        1,
        1,
        6,
        "PBKS",
        "Mayank Agarwal",
        "KL Rahul",
        "Abhishek Sharma",
        2,
        False,
        False,
        False,
        "",
        "",
        "Abhishek Sharma to Agarwal, 2 runs",
        "<b>dropped at deep midwicket!</b> Rashid has put in a slide and put it down after getting two hands on it",
    ),
    (
        1254071,
        2,
        20,
        3,
        "SRH",
        "Jason Holder",
        "Rashid Khan",
        "Mohammed Shami",
        1,
        False,
        False,
        False,
        "",
        "",
        "Shami to Holder, 1 run",
        "Yorker dug out to long-on, Holder keeps strike? No, single taken",
    ),
]


def make_archive(path) -> None:
    con = duckdb.connect(str(path))
    con.execute("""create table matches (match_id bigint primary key, season varchar, title varchar, start_date date,
                   venue varchar, team1 varchar, team2 varchar, winner varchar, status_text varchar,
                   is_cancelled boolean default false)""")
    con.execute("""create table balls (match_id bigint, inning integer, sequence bigint, over_number integer,
                   ball_in_over integer, over_label varchar, batting_team varchar, batter varchar, non_striker varchar,
                   bowler varchar, runs_total integer, is_wicket boolean, is_four boolean, is_six boolean,
                   player_out varchar, dismissal_type varchar, short_text varchar, commentary varchar)""")
    for m in MATCHES:
        con.execute("insert into matches values (?,?,?,?,?,?,?,?,?,false)", list(m))
    for seq, b in enumerate(sorted(BALLS, key=lambda r: (r[0], r[1], r[2], r[3]))):
        mid, inn, ov, ball = b[:4]
        con.execute(
            "insert into balls values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [mid, inn, seq, ov, ball, f"{ov - 1}.{ball}", *b[4:]],
        )
    con.execute("alter table balls add column is_wide boolean default false")
    con.execute("alter table balls add column is_noball boolean default false")
    for col, expr in (
        ("runs_bat", "runs_total"),
        ("wide_runs", "0"),
        ("noball_runs", "0"),
        ("is_legal", "true"),
        ("bowler_wicket", "is_wicket"),
        ("player_out_id", "case when is_wicket then 101 end"),
    ):
        con.execute(
            f"alter table balls add column {col} {'boolean' if col.startswith(('is_', 'bowler')) else 'bigint'}"
        )
        con.execute(f"update balls set {col} = {expr}")
    con.execute(
        "create table cs_fielding as select 1473511::bigint match_id, 2 inning, 101::bigint player_out_id, "
        "'Manoj Bhandage' fielder, 'caught' kind"
    )
    # minimal cricbot scorecard tables for the story facts
    con.execute(
        "create table cs_match_info as select 1473511::bigint match_id, 'PBKS' toss_winner, 'field' toss_decision, null method"
    )
    con.execute(
        "create table cs_innings_card as select * from (values (1473511::bigint, 1, 'RCB', 190, 9, 120), (1473511, 2, 'PBKS', 184, 7, 120)) t(match_id, inning, team, runs, wickets, balls)"
    )
    con.execute(
        "create table cs_batting_card as select * from (values (1473511::bigint, 1, 'Virat Kohli', 43, 35), (1473511, 2, 'Shashank Singh', 61, 30)) t(match_id, inning, player, runs, balls)"
    )
    con.execute(
        "create table cs_bowling_card as select * from (values (1473511::bigint, 1, 'Arshdeep Singh', 3, 40, 24), (1473511, 2, 'Krunal Pandya', 2, 17, 24)) t(match_id, inning, player, wickets, conceded, balls)"
    )
    con.execute("create table cs_awards as select 1473511::bigint match_id, 'Krunal Pandya' player")
    con.close()


@pytest.fixture(scope="session")
def archive(tmp_path_factory):
    path = tmp_path_factory.mktemp("arch") / "cricbot.duckdb"
    make_archive(path)
    return path


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    seasons = {
        "2007/08": 2008,
        "2009/10": 2010,
        "2012": 2012,
        "2016": 2016,
        "2017": 2017,
        "2020/21": 2020,
        "2021": 2021,
        "2022": 2022,
        "2024": 2024,
        "2025": 2025,
        "2026": 2026,
    }
    players = [
        Player(n, c)
        for n, c in [
            ("Virat Kohli", 7000),
            ("Jasprit Bumrah", 3800),
            ("Rashid Khan", 3100),
            ("Adil Rashid", 40),
            ("KL Rahul", 4200),
            ("Rahul Tripathi", 2100),
            ("Rahul Tewatia", 1500),
            ("Rahul Chahar", 1600),
            ("Yuzvendra Chahal", 3900),
            ("Deepak Chahar", 2200),
            ("Hardik Pandya", 3000),
            ("Krunal Pandya", 2400),
            ("Travis Head", 900),
            ("Cameron Green", 700),
            ("Shubman Gill", 2600),
            ("AB de Villiers", 3600),
            ("Bhuvneshwar Kumar", 4000),
            ("Shashank Singh", 400),
            ("Arshdeep Singh", 1800),
            ("Yash Dayal", 900),
            ("Mayank Agarwal", 2500),
            ("Abhishek Sharma", 1500),
            ("Rohit Sharma", 6500),
            ("Ishant Sharma", 2300),
            ("Suryakumar Yadav", 2800),
            ("Kuldeep Yadav", 2100),
        ]
    ]
    return Catalog(
        season_years=seasons,
        team_codes={
            "CSK",
            "MI",
            "RCB",
            "KKR",
            "SRH",
            "DCH",
            "PBKS",
            "RR",
            "DC",
            "GT",
            "GL",
            "LSG",
            "PWI",
            "RPS",
            "Kochi",
        },
        players=players,
    )
