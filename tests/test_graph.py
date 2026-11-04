"""Identity resolution and graph edge tables on a tiny two-innings archive (offline)."""

from __future__ import annotations

import duckdb
import pytest

from cricrag.graph import explore as gx
from cricrag.graph.build import build
from cricrag.graph.identity import Candidate, Sig, resolve

MI = {1: "Rohit Sharma", 2: "Hardik Pandya", 3: "Krunal Pandya", 4: "Jasprit Bumrah", 5: "Tilak Varma"}
CSK = {10: "Deepak Chahar", 11: "Ravindra Jadeja", 12: "MS Dhoni"}

# (inning, over, ball, batter_id, bowler_id, runs_bat, wide, four, six, wicket, bowler_wicket, out_id, how)
BALLS = [
    (1, 1, 1, 1, 10, 4, False, True, False, False, False, None, None),
    (1, 1, 2, 1, 10, 0, False, False, False, True, True, 1, "caught"),
    (1, 1, 3, 2, 10, 0, True, False, False, False, False, None, None),  # wide: not a ball to the batter
    (1, 1, 4, 2, 10, 1, False, False, False, False, False, None, None),
    (1, 2, 1, 5, 11, 6, False, False, True, False, False, None, None),
    (1, 2, 2, 2, 11, 0, False, False, False, True, True, 2, "stumped"),
    (1, 2, 3, 3, 11, 0, False, False, False, True, False, 5, "run out"),  # non-striker run out
    (2, 3, 2, 11, 4, 0, False, False, False, False, False, None, None),
    (2, 4, 1, 11, 4, 2, False, False, False, False, False, None, None),
    (2, 4, 2, 12, 4, 1, False, False, False, False, False, None, None),
    (2, 5, 1, 12, 3, 0, False, False, False, True, True, 12, "caught"),
]
FIELDING = [  # (inning, fielder_id, fielder, kind, player_out_id, substitute)
    (1, 12, "MS Dhoni", "caught", 1, False),
    (1, 12, "MS Dhoni", "stumped", 2, False),
    (1, 11, "RA Jadeja", "run out", 5, False),
    (2, 2, "HH Pandya", "caught", 12, False),
]
DROPS = [  # (inning, over_label, fielder, runs_after_drop)
    (2, "2.2", "Pandya", 5),  # two Pandyas in MI's XI -> ambiguous, no id
    (2, "3.1", "Bumrah", 3),  # bare surname, one candidate
    (2, "3.2", "Boom", 0),  # nickname
    (2, "4.1", "Kholi", 0),  # not in this XI -> unresolved
    (2, "4.1", "", 0),  # unnamed
]


def make(tmp_path):
    db = tmp_path / "arch.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""create table matches (match_id bigint, season varchar, title varchar, start_date date, venue varchar,
                   team1 varchar, team2 varchar, winner varchar, status_text varchar, is_cancelled boolean)""")
    con.execute(
        "insert into matches values (9, '2024', '1st Match', '2024-03-22', 'Wankhede', 'MI', 'CSK', 'MI', 'MI won', false)"
    )
    con.execute("""create table balls (match_id bigint, inning int, sequence bigint, over_number int, ball_in_over int,
        over_label varchar, batting_team varchar, batter_id bigint, batter varchar, non_striker varchar, bowler_id bigint,
        bowler varchar, runs_bat int, runs_total int, is_wide boolean, is_noball boolean, is_legal boolean, is_four boolean,
        is_six boolean, is_wicket boolean, bowler_wicket boolean, player_out_id bigint, player_out varchar,
        dismissal_type varchar)""")
    names = MI | CSK
    for seq, (inn, ov, ball, bat, bowl, r, wide, four, six, wk, bw, out, how) in enumerate(BALLS):
        con.execute(
            "insert into balls values (9,?,?,?,?,?,?,?,?,'',?,?,?,?,?,false,?,?,?,?,?,?,?,?)",
            [
                inn,
                seq,
                ov,
                ball,
                f"{ov - 1}.{ball}",
                "MI" if inn == 1 else "CSK",
                bat,
                names[bat],
                bowl,
                names[bowl],
                r,
                r + int(wide),
                wide,
                not wide,
                four,
                six,
                wk,
                bw,
                out,
                names.get(out),
                how,
            ],
        )
    con.execute("""create table cs_fielding (match_id bigint, inning int, sequence bigint, fielder_id bigint,
        fielder varchar, substitute boolean, kind varchar, player_out_id bigint, player_out varchar)""")
    for inn, fid, f, kind, out, sub in FIELDING:
        con.execute(
            "insert into cs_fielding values (9,?,0,?,?,?,?,?,?)", [inn, fid, f, sub, kind, out, names[out]]
        )
    # Cricsheet copy of one ball: 'T Varma' has no Cricinfo id in the register -> negative id
    con.execute("""create table cricsheet_balls as select match_id, inning, batter_id, batter, bowler_id, bowler
                   from balls where batter_id = 5""")
    con.execute("update cricsheet_balls set batter_id = -255, batter = 'T Varma'")
    con.close()
    csv = tmp_path / "fielding.csv"
    csv.write_text(
        "match_id,inning,over_label,label,confidence,fielder,batter,runs_after_drop\n"
        + "".join(f"9,{i},{o},drop,high,{f},x,{r}\n" for i, o, f, r in DROPS)
        + "9,2,4.1,drop,low,Bumrah,x,9\n",  # low confidence: excluded
        encoding="utf-8",
    )
    return db, csv


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("graph")
    db, csv = make(tmp)
    out = tmp / "graph.duckdb"
    report = build(db, out, csv, log=lambda *_: None)
    con = duckdb.connect(str(out), read_only=True)
    yield con, report
    con.close()


def cands(**names) -> list[Candidate]:
    return [Candidate(i, {n}) for i, n in enumerate(names.values(), 1)]


def test_signature_handles_initials_and_particles():
    assert Sig.of("MS Dhoni").initials == ("m", "s")
    assert Sig.of("Quinton de Kock").surname == "de kock"
    assert Sig.of("M.S. Dhoni").initials == ("m", "s")


@pytest.mark.parametrize(
    ("mention", "expect", "method"),
    [
        ("MS Dhoni", 1, "surname_initials"),
        ("Dhoni", 1, "single_token"),
        ("de Kock", 2, "single_token"),
        ("Bumrha", 3, "fuzzy"),
        ("Boom", 3, "nickname"),
        ("Pandya", None, "ambiguous"),
        ("Rashid", None, "ambiguous"),  # Rashid Khan's first name, Adil Rashid's surname
        ("Kohli", None, "unresolved"),
        ("", None, "unnamed"),
    ],
)
def test_resolve_within_one_roster(mention, expect, method):
    roster = cands(
        a="Mahendra Singh Dhoni",
        b="Quinton de Kock",
        c="Jasprit Bumrah",
        d="Hardik Pandya",
        e="Krunal Pandya",
        f="Rashid Khan",
        g="Adil Rashid",
    )
    r = resolve(mention, roster)
    assert (r.player_id, r.method) == (expect, method)


def test_unregistered_cricsheet_id_is_merged(graph):
    con, report = graph
    assert con.execute("select from_id, to_id from id_merge").fetchall() == [(-255, 5)]
    assert report["identity"]["unregistered_ids_left"] == 0
    aliases = con.execute("select aliases from people where player_id = 5").fetchone()[0]
    assert set(aliases) == {"Tilak Varma", "T Varma"}


def test_fielding_mentions_name_their_method(graph):
    con, _ = graph
    got = con.execute(
        "select fielder, player_id, method from fielding_mentions order by over_label, fielder"
    ).fetchall()
    assert got == [
        ("Pandya", None, "ambiguous"),
        ("Bumrah", 4, "single_token"),
        ("Boom", 4, "nickname"),
        ("Kholi", None, "unresolved"),
        (None, None, "unnamed"),
    ]


def test_every_edge_table_agrees_with_the_archive(graph):
    _, report = graph
    assert report["parity"], "no parity checks ran"
    assert all(ok for *_, ok in report["parity"].values()), report["parity"]


def test_edges(graph):
    con, _ = graph
    assert con.execute(
        "select sum(balls), sum(runs) from bowled_to where bowler_id = 10 and batter_id = 2"
    ).fetchone() == (1, 1)
    assert con.execute("select batter_id, mode from dismissed order by batter_id").fetchall() == [
        (1, "caught"),
        (2, "stumped"),
        (12, "caught"),
    ]
    assert con.execute(
        "select fielder_id, batter_id, bowler_id, kind from fielding_dismissal order by batter_id"
    ).fetchall() == [
        (12, 1, 10, "caught"),
        (12, 2, 11, "stumped"),
        (11, 5, 11, "run out"),
        (2, 12, 3, "caught"),
    ]
    # two resolved drops, both off Bumrah's bowling; the batter comes from the ball, not the CSV
    assert con.execute(
        "select fielder_id, batter_id, bowler_id, sum(n) from dropped group by all order by 2"
    ).fetchall() == [
        (4, 11, 4, 1),
        (4, 12, 4, 1),
    ]
    assert con.execute("select team, matches from played_for where player_id = 5").fetchall() == [("MI", 1)]


def test_explore(graph):
    con, _ = graph
    assert gx.nemesis(con, 1, min_dismissals=1) == [("Deepak Chahar", 1, 2, 4)]
    pairs = {(f, b) for f, b, _ in gx.fielder_bowler(con)}
    assert pairs == {
        ("MS Dhoni", "Deepak Chahar"),
        ("MS Dhoni", "Ravindra Jadeja"),
        ("Hardik Pandya", "Krunal Pandya"),
    }
    ranked = gx.bowler_rank(con, min_balls=0)
    assert {r["player"] for r in ranked} >= {"Deepak Chahar", "Ravindra Jadeja"}
    assert (
        sum(r["wickets"] for r in ranked) == 3
    )  # every bowler-credited wicket; the run out is the fielder's
    assert gx.batter_rank(con, min_balls=0)[0]["player"]
    prof = gx.dismissal_profile(con, 1)
    assert prof["batter"] == [("unknown", "caught", 1)]  # no styles in this archive: the type is unknown
    assert gx.find_player(con, "varma")[0][1] == "Tilak Varma"


def test_keeper_is_whoever_stumped_for_that_side_that_season(graph):
    from cricrag.graph import tools as gt

    con, _ = graph
    assert con.execute("select player_id, team, season from keepers").fetchall() == [(12, "CSK", "2024")]
    ev = gt.catch_pairs(con, team="CSK")
    kept = {r["fielder"]: r["keeper"] for r in ev.rows}
    assert kept == {"MS Dhoni": True}
    assert all(
        "(wicketkeeper)" in line for line in ev.lines if line.startswith("caught or stumped by MS Dhoni")
    )
    assert next(n for n in ev.nodes if n["label"] == "MS Dhoni")["keeper"] is True
    assert gt.catch_pairs(con, team="MI").rows[0]["keeper"] is False  # Hardik caught, never stumped


def test_connection_runs_on_the_teammate_graph(graph):
    from cricrag.graph import tools as gt

    con, _ = graph
    assert gx.teammate_graph(con).has_edge(1, 2)  # Rohit and Hardik, MI 2024
    ev = gt.connection(con, 1, 10)  # Rohit (MI) to Deepak Chahar (CSK): only a match links them
    assert ev.tool == "connection"
