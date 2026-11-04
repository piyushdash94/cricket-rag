"""Analytics over data/graph.duckdb. Every number comes from the edge tables, so a writer can cite it.

  bowler_rank        quality wickets: each wicket weighted by the victim's average in the same seasons
                     (a wicket of a batter averaging 40 counts twice one averaging 20), plus top/middle/tail split
  batter_rank        quality runs: runs weighted by how hard each bowler was to score off per wicket
  dismissal_profile  how a batter gets out (bowling type x mode) and whom a bowler dismisses (batting order x mode)
  nemesis            the bowlers who dismissed one batter most, with balls/runs between them
  fielder_bowler     caught / stumped pairs ("c X b Y")

PageRank was dropped: on this graph bowlers are also batters, so a bowler's score drained to whoever dismissed
him when he batted, and a bowler who rarely batted ranked high (Naveen-ul-Haq, 14 wickets, topped 2024 above
Bumrah's 20). The quality weights are one transparent step instead.
"""

from __future__ import annotations

SEASONS = "({col} IN ({ph}))"
SHRINK = 3  # pseudo-dismissals pulling a small-sample average toward the league's ("60 off 1 out" is not 60)
BANDS = ("top order", "middle order", "tail")


def _where(seasons: list[str] | None, col: str = "season") -> tuple[str, list]:
    if not seasons:
        return "TRUE", []
    return SEASONS.format(col=col, ph=",".join("?" * len(seasons))), list(seasons)


def qualify(seasons, min_balls: int | None) -> int:
    """Balls needed to be ranked: 300 across seasons, 120 (20 overs) inside one or two seasons."""
    if min_balls is not None:
        return min_balls
    return 120 if seasons and len(seasons) <= 2 else 300


def names(con) -> dict[int, str]:
    return dict(con.execute("SELECT player_id, name FROM people").fetchall())


def _averages(con, w: str, p: list, by: str) -> tuple[dict[int, float], float]:
    """Shrunk runs per dismissal by batter_id or bowler_id in the seasons, and the league's."""
    runs, outs = con.execute(f"SELECT sum(runs), sum(dismissals) FROM bowled_to WHERE {w}", p).fetchone() or (
        0,
        0,
    )
    league = (runs or 0) / max(outs or 0, 1)
    rows = con.execute(
        f"SELECT {by}, sum(runs), sum(dismissals) FROM bowled_to WHERE {w} GROUP BY 1", p
    ).fetchall()
    return {k: (r + SHRINK * league) / (o + SHRINK) for k, r, o in rows}, league


def bowler_rank(con, seasons=None, min_balls: int | None = None, top: int = 15) -> list[dict]:
    """Bowlers by quality wickets (min balls bowled): wickets, quality wickets, and top / middle / tail wickets."""
    w, p = _where(seasons)
    avg, league = _averages(con, w, p, "batter_id")
    vol = dict(con.execute(f"SELECT bowler_id, sum(balls) FROM bowled_to WHERE {w} GROUP BY 1", p).fetchall())
    nm, least = names(con), qualify(seasons, min_balls)
    out: dict[int, dict] = {}
    for b, v, n, band in con.execute(
        f"SELECT bowler_id, batter_id, sum(n), order_band FROM wicket_detail WHERE {w} GROUP BY 1, 2, 4", p
    ).fetchall():
        if vol.get(b, 0) < least:
            continue
        r = out.setdefault(
            b, {"player": nm.get(b, str(b)), "score": 0.0, "wickets": 0, **dict.fromkeys(BANDS, 0)}
        )
        r["score"] += n * avg.get(v, league) / league
        r["wickets"] += int(n)
        if band:
            r[band] += int(n)
    rows = sorted(out.values(), key=lambda r: (-r["score"], -r["wickets"]))[:top]
    return [r | {"score": round(r["score"], 1)} for r in rows]


def batter_rank(con, seasons=None, min_balls: int | None = None, top: int = 15) -> list[dict]:
    """Batters by quality runs (min balls faced): runs weighted by the bowler's (shrunk) average v the league's."""
    w, p = _where(seasons)
    avg, league = _averages(con, w, p, "bowler_id")
    nm, least = names(con), qualify(seasons, min_balls)
    out: dict[int, dict] = {}
    for bat, bowl, runs, balls, outs in con.execute(
        f"SELECT batter_id, bowler_id, sum(runs), sum(balls), sum(dismissals) FROM bowled_to WHERE {w} GROUP BY 1, 2",
        p,
    ).fetchall():
        r = out.setdefault(
            bat, {"player": nm.get(bat, str(bat)), "score": 0.0, "runs": 0, "balls": 0, "outs": 0}
        )
        r["score"] += runs * league / avg.get(bowl, league)
        r["runs"] += int(runs)
        r["balls"] += int(balls)
        r["outs"] += int(outs)
    rows = sorted((r for r in out.values() if r["balls"] >= least), key=lambda r: (-r["score"], -r["runs"]))[
        :top
    ]
    return [r | {"score": round(r["score"])} for r in rows]


def dismissal_profile(con, pid: int, seasons=None) -> dict[str, list[tuple[str, str, int]]]:
    """(group, mode, n) rows: as a batter by bowling type, as a bowler by the batter's place in the order."""
    w, p = _where(seasons)
    q = """SELECT {g}, mode, sum(n)::INT FROM wicket_detail WHERE {who} = ? AND {w} AND {g} IS NOT NULL
           GROUP BY 1, 2 ORDER BY 3 DESC"""
    return {
        "batter": con.execute(q.format(g="bowl_type", who="batter_id", w=w), [pid, *p]).fetchall(),
        "bowler": con.execute(q.format(g="order_band", who="bowler_id", w=w), [pid, *p]).fetchall(),
    }


def nemesis(con, batter_id: int, min_dismissals: int = 2, seasons=None) -> list[tuple[str, int, int, int]]:
    """(bowler, dismissals, balls, runs) for one batter, most dismissals first."""
    w, p = _where(seasons)
    return con.execute(
        f"""SELECT p.name, sum(t.dismissals)::INT d, sum(t.balls)::INT b, sum(t.runs)::INT r
            FROM bowled_to t JOIN people p ON p.player_id = t.bowler_id
            WHERE t.batter_id = ? AND {w} GROUP BY 1 HAVING d >= ? ORDER BY d DESC, r ASC""",
        [batter_id, *p, min_dismissals],
    ).fetchall()


def fielder_bowler(con, seasons=None, top: int = 15) -> list[tuple[str, str, int]]:
    w, p = _where(seasons)
    return con.execute(
        f"""SELECT f.name, b.name, sum(d.n)::INT n FROM fielding_dismissal d
            JOIN people f ON f.player_id = d.fielder_id JOIN people b ON b.player_id = d.bowler_id
            WHERE d.kind IN ('caught', 'stumped') AND d.fielder_id <> d.bowler_id AND {w}
            GROUP BY 1, 2 ORDER BY n DESC LIMIT {int(top)}""",
        p,
    ).fetchall()


def find_player(con, name: str) -> list[tuple[int, str, int]]:
    """People whose display name or any alias contains the text (for the CLI; the agent uses entities.py)."""
    q = f"%{name.casefold()}%"
    return con.execute(
        """SELECT player_id, name, matches FROM people
           WHERE lower(name) LIKE ? OR lower(array_to_string(aliases, '|')) LIKE ?
           ORDER BY matches DESC LIMIT 5""",
        [q, q],
    ).fetchall()
