"""Templated relational queries over data/graph.duckdb: the only way the agent touches the graph.

The LLM never writes graph queries (Rule 4). Each tool takes resolved player ids / team codes / seasons from
the deterministic frame and returns an `Evidence`: tagged lines [G1].. whose numbers all come from SQL, the
rows behind them, and a small subgraph (nodes + edges) the UI can draw.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from cricrag.graph import explore as gx


@dataclass
class Evidence:
    tool: str
    lines: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""
    gap: str | None = None  # set when the graph has nothing for the question

    def add(self, text: str, row: dict[str, Any] | None = None) -> None:
        self.lines.append(f"{text} [G{len(self.lines) + 1}]")
        if row is not None:
            self.rows.append(row)

    def node(self, pid: int, name: str, role: str, **extra) -> None:
        if all(n["id"] != pid for n in self.nodes):
            self.nodes.append({"id": pid, "label": name, "role": role, **extra})

    def as_payload(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "title": self.title,
            "nodes": self.nodes,
            "edges": self.edges,
            "rows": self.rows,
        }


def _seasons(seasons: list[str] | None, col: str = "season") -> tuple[str, list]:
    if not seasons:
        return "TRUE", []
    return f"{col} IN ({','.join('?' * len(seasons))})", list(seasons)


def _n(k: int, word: str) -> str:
    """'1 six', '2 sixes', '1 time'."""
    if k == 1:
        return f"1 {word}"
    return f"{k} {word}es" if word.endswith(("x", "s", "ch")) else f"{k} {word}s"


def _tie(ev: Evidence, names: list[str], values: list[int], what: str) -> None:
    """State a shared top value as a tie, so the writer can't crown one of several equal leaders."""
    top = [n for n, v in zip(names, values, strict=True) if v == values[0]]
    if len(top) > 1:
        ev.add(f"Tied for the most {what} ({values[0]}): {', '.join(top)}")


def _scope(seasons: list[str] | None) -> str:
    return f"in {', '.join(seasons)}" if seasons else "across all IPL seasons in the archive"


def player_id(con, name: str) -> int | None:
    """Catalog names come from the archive's ESPN spelling, which is people.name; aliases cover the rest."""
    row = con.execute(
        """SELECT player_id FROM people WHERE name = ? OR list_contains(aliases, ?)
           ORDER BY (name = ?) DESC, matches DESC LIMIT 1""",
        [name, name, name],
    ).fetchone()
    return int(row[0]) if row else None


def name_of(con, pid: int) -> str:
    row = con.execute("SELECT name FROM people WHERE player_id = ?", [pid]).fetchone()
    return row[0] if row else str(pid)


def role_balls(con, pid: int) -> tuple[int, int]:
    """(balls faced, balls bowled): which side of a matchup a player is usually on."""
    faced = con.execute("SELECT coalesce(sum(balls), 0) FROM bowled_to WHERE batter_id = ?", [pid]).fetchone()
    bowled = con.execute(
        "SELECT coalesce(sum(balls), 0) FROM bowled_to WHERE bowler_id = ?", [pid]
    ).fetchone()
    return int((faced or (0,))[0]), int((bowled or (0,))[0])


# ------------------------------------------------------------------ matchups


def head_to_head(con, a: int, b: int, seasons=None) -> Evidence:
    """Bowler vs batter. The direction with more balls between the two is the matchup asked about."""
    w, p = _seasons(seasons)
    q = f"""SELECT sum(balls)::INT, sum(runs)::INT, sum(dismissals)::INT, sum(dots)::INT, sum(fours)::INT,
                   sum(sixes)::INT, round(100.0 * sum(runs) / nullif(sum(balls), 0), 1),
                   round(100.0 * sum(dots) / nullif(sum(balls), 0), 1), count(DISTINCT season)::INT
            FROM bowled_to WHERE bowler_id = ? AND batter_id = ? AND {w}"""
    ab = con.execute(q, [a, b, *p]).fetchone()
    ba = con.execute(q, [b, a, *p]).fetchone()
    bowler, batter, r = (a, b, ab) if (ab[0] or 0) >= (ba[0] or 0) else (b, a, ba)
    bn, tn = name_of(con, bowler), name_of(con, batter)
    ev = Evidence("head_to_head", title=f"{bn} vs {tn}")
    if not r or not r[0]:
        ev.gap = f"The archive has no balls between {bn} and {tn} {_scope(seasons)}."
        return ev
    balls, runs, outs, dots, fours, sixes, sr, dotpct, nseason = r
    row = dict(bowler=bn, batter=tn, balls=balls, runs=runs, dismissals=outs, dots=dots, fours=fours,
               sixes=sixes, strike_rate=sr, dot_pct=dotpct, seasons=nseason)  # fmt: skip
    ev.add(
        f"{bn} bowling to {tn} {_scope(seasons)}: {balls} balls, {runs} runs, {_n(outs, 'dismissal')}, "
        f"strike rate {sr}, {dots} dot balls ({dotpct}%), {_n(fours, 'four')}, {_n(sixes, 'six')}, over {_n(nseason, 'season')}",
        row,
    )
    for season, sb, sr_, so in con.execute(
        f"""SELECT season, sum(balls)::INT, sum(runs)::INT, sum(dismissals)::INT FROM bowled_to
            WHERE bowler_id = ? AND batter_id = ? AND {w} GROUP BY 1 HAVING sum(dismissals) > 0 ORDER BY 1""",
        [bowler, batter, *p],
    ).fetchall():
        ev.add(f"{season}: {bn} dismissed {tn} {_n(so, 'time')} in {sb} balls ({sr_} runs)",
               dict(season=season, balls=sb, runs=sr_, dismissals=so))  # fmt: skip
    ev.node(bowler, bn, "bowler")
    ev.node(batter, tn, "batter")
    ev.edges.append({"source": bowler, "target": batter, "kind": "bowled_to", "balls": balls, "runs": runs,
                     "dismissals": outs, "strike_rate": sr})  # fmt: skip
    return ev


def _retry_once(con, side: str, pid: int, w: str, p: list, top: int, min_d: int) -> list:
    """No pair reached the threshold: fall back to single dismissals (sparse seasons, short careers)."""
    if min_d <= 1:
        return []
    other = "bowler" if side == "batter" else "batter"
    return con.execute(
        f"""SELECT t.{other}_id, q.name, sum(t.dismissals)::INT d, sum(t.balls)::INT b, sum(t.runs)::INT r,
                   round(100.0 * sum(t.runs) / nullif(sum(t.balls), 0), 1) sr
            FROM bowled_to t JOIN people q ON q.player_id = t.{other}_id
            WHERE t.{side}_id = ? AND {w} GROUP BY 1, 2 HAVING d >= 1 ORDER BY d DESC, sr ASC LIMIT {int(top)}""",
        [pid, *p],
    ).fetchall()


def _team(w: str, p: list, col: str, team: str | None) -> tuple[str, list]:
    return (f"{w} AND {col} = ?", [*p, team]) if team else (w, p)


def nemesis(
    con, batter: int, seasons=None, top: int = 6, min_dismissals: int = 2, team: str | None = None
) -> Evidence:
    """Bowlers who dismissed one batter most; `team` = the batter's side (e.g. Warner for SRH only)."""
    w, p = _team(*_seasons(seasons, "t.season"), "t.bat_team", team)
    tn = name_of(con, batter)
    ev = Evidence("nemesis", title=f"Bowlers who dismissed {tn} most")
    ev.node(batter, tn, "batter", center=True)
    rows = con.execute(
        f"""SELECT t.bowler_id, p.name, sum(t.dismissals)::INT d, sum(t.balls)::INT b, sum(t.runs)::INT r,
                   round(100.0 * sum(t.runs) / nullif(sum(t.balls), 0), 1) sr
            FROM bowled_to t JOIN people p ON p.player_id = t.bowler_id
            WHERE t.batter_id = ? AND {w} GROUP BY 1, 2 HAVING d >= ? ORDER BY d DESC, sr ASC LIMIT {int(top)}""",
        [batter, *p, min_dismissals],
    ).fetchall() or _retry_once(con, "batter", batter, w, p, top, min_dismissals)
    if not rows:
        ev.gap = f"No bowler has dismissed {tn} {_scope(seasons)}."
        return ev
    _tie(ev, [r[1] for r in rows], [r[2] for r in rows], "dismissals")
    for pid, bn, d, b, r, sr in rows:
        ev.add(f"{bn} dismissed {tn} {_n(d, 'time')}{f' while {tn} batted for {team}' if team else ''} {_scope(seasons)}, conceding {r} runs in {b} balls (strike rate {sr})",
               dict(bowler=bn, dismissals=d, balls=b, runs=r, strike_rate=sr))  # fmt: skip
        ev.node(pid, bn, "bowler")
        ev.edges.append({"source": pid, "target": batter, "kind": "dismissed", "dismissals": d, "balls": b,
                         "runs": r, "strike_rate": sr})  # fmt: skip
    return ev


def bunnies(
    con, bowler: int, seasons=None, top: int = 6, min_dismissals: int = 2, team: str | None = None
) -> Evidence:
    """Batters one bowler dismissed most; `team` = the bowler's side."""
    w, p = _team(*_seasons(seasons, "t.season"), "t.bowl_team", team)
    bn = name_of(con, bowler)
    ev = Evidence("bunnies", title=f"Batters {bn} dismissed most")
    ev.node(bowler, bn, "bowler", center=True)
    rows = con.execute(
        f"""SELECT t.batter_id, p.name, sum(t.dismissals)::INT d, sum(t.balls)::INT b, sum(t.runs)::INT r,
                   round(100.0 * sum(t.runs) / nullif(sum(t.balls), 0), 1) sr
            FROM bowled_to t JOIN people p ON p.player_id = t.batter_id
            WHERE t.bowler_id = ? AND {w} GROUP BY 1, 2 HAVING d >= ? ORDER BY d DESC, sr ASC LIMIT {int(top)}""",
        [bowler, *p, min_dismissals],
    ).fetchall() or _retry_once(con, "bowler", bowler, w, p, top, min_dismissals)
    if not rows:
        ev.gap = f"{bn} has not dismissed any batter {_scope(seasons)}."
        return ev
    _tie(ev, [r[1] for r in rows], [r[2] for r in rows], "dismissals")
    for pid, tn, d, b, r, sr in rows:
        ev.add(f"{bn} dismissed {tn} {_n(d, 'time')}{f' while bowling for {team}' if team else ''} {_scope(seasons)}, conceding {r} runs in {b} balls (strike rate {sr})",
               dict(batter=tn, dismissals=d, balls=b, runs=r, strike_rate=sr))  # fmt: skip
        ev.node(pid, tn, "batter")
        ev.edges.append({"source": bowler, "target": pid, "kind": "dismissed", "dismissals": d, "balls": b,
                         "runs": r, "strike_rate": sr})  # fmt: skip
    return ev


# ------------------------------------------------------------------ fielding


def catch_pairs(con, fielder: int | None = None, bowler: int | None = None, team: str | None = None,
                seasons=None, top: int = 8) -> Evidence:  # fmt: skip
    """'c X b Y' dismissals (caught + stumped), filtered by any of fielder, bowler, fielding team, seasons."""
    w, p = _seasons(seasons, "d.season")
    where, params = [w, "d.kind IN ('caught', 'stumped')", "d.fielder_id <> d.bowler_id"], list(p)
    for col, val in (("d.fielder_id", fielder), ("d.bowler_id", bowler), ("d.team", team)):
        if val is not None:
            where.append(f"{col} = ?")
            params.append(val)
    rows = con.execute(
        f"""SELECT d.fielder_id, f.name, d.bowler_id, b.name, sum(d.n)::INT n,
                   sum(CASE WHEN d.kind = 'stumped' THEN d.n ELSE 0 END)::INT st
            FROM fielding_dismissal d JOIN people f ON f.player_id = d.fielder_id
            JOIN people b ON b.player_id = d.bowler_id
            WHERE {" AND ".join(where)} GROUP BY 1, 2, 3, 4 ORDER BY n DESC, 2 LIMIT {int(top)}""",
        params,
    ).fetchall()
    who = [name_of(con, x) for x in (fielder, bowler) if x is not None] + ([team] if team else [])
    ev = Evidence("catch_pairs", title="Fielder-bowler dismissals" + (f": {', '.join(who)}" if who else ""))
    if not rows:
        ev.gap = f"No caught or stumped dismissals match that {_scope(seasons)}."
        return ev
    _tie(ev, [f"{r[1]} off {r[3]}" for r in rows], [r[4] for r in rows], "dismissals")
    for fid, fn, bid, bn, n, st in rows:
        extra = f" ({st} stumpings)" if st else ""
        ev.add(f"caught or stumped by {fn} off {bn}: {_n(n, 'dismissal')}{extra} {_scope(seasons)}",
               dict(fielder=fn, bowler=bn, dismissals=n, stumpings=st))  # fmt: skip
        ev.node(fid, fn, "fielder")
        ev.node(bid, bn, "bowler")
        ev.edges.append(
            {"source": fid, "target": bid, "kind": "caught_off", "dismissals": n, "stumpings": st}
        )
    return ev


def drops(con, fielder: int | None = None, bowler: int | None = None, team: str | None = None,
          seasons=None, top: int = 8) -> Evidence:  # fmt: skip
    """Audited dropped catches (ipl_fielding, medium confidence or better), with runs the batter added after."""
    w, p = _seasons(seasons, "d.season")
    where, params = [w, "d.label = 'drop'"], list(p)
    for col, val in (("d.fielder_id", fielder), ("d.bowler_id", bowler), ("d.team", team)):
        if val is not None:
            where.append(f"{col} = ?")
            params.append(val)
    rows = con.execute(
        f"""SELECT d.fielder_id, f.name, d.bowler_id, b.name, sum(d.n)::INT n, sum(d.runs_after)::INT ra
            FROM dropped d JOIN people f ON f.player_id = d.fielder_id JOIN people b ON b.player_id = d.bowler_id
            WHERE {" AND ".join(where)} GROUP BY 1, 2, 3, 4 ORDER BY n DESC, ra DESC LIMIT {int(top)}""",
        params,
    ).fetchall()
    ev = Evidence("drops", title="Dropped catches")
    if not rows:
        ev.gap = f"No audited dropped catches match that {_scope(seasons)}."
        return ev
    _tie(ev, [f"{r[1]} off {r[3]}" for r in rows], [r[4] for r in rows], "drops")
    for fid, fn, bid, bn, n, ra in rows:
        ev.add(f"{fn} dropped {_n(n, 'catch')} off {bn}'s bowling {_scope(seasons)}; the batters added {ra} runs after",
               dict(fielder=fn, bowler=bn, drops=n, runs_after=ra))  # fmt: skip
        ev.node(fid, fn, "fielder")
        ev.node(bid, bn, "bowler")
        ev.edges.append({"source": fid, "target": bid, "kind": "dropped_off", "drops": n, "runs_after": ra})
    return ev


# ------------------------------------------------------------------ teammates


def teammates(con, pid: int, seasons=None, top: int = 8) -> Evidence:
    w, p = _seasons(seasons, "a.season")
    pn = name_of(con, pid)
    rows = con.execute(
        f"""SELECT b.player_id, q.name, count(*)::INT seasons, string_agg(DISTINCT a.team, '/' ORDER BY a.team) teams
            FROM played_for a JOIN played_for b ON a.team = b.team AND a.season = b.season AND b.player_id <> a.player_id
            JOIN people q ON q.player_id = b.player_id
            WHERE a.player_id = ? AND {w} GROUP BY 1, 2 ORDER BY seasons DESC, 2 LIMIT {int(top)}""",
        [pid, *p],
    ).fetchall()
    ev = Evidence("teammates", title=f"{pn}'s most frequent teammates")
    ev.node(pid, pn, "player", center=True)
    if not rows:
        ev.gap = f"No teammates found for {pn} {_scope(seasons)}."
        return ev
    for qid, qn, ns, teams in rows:
        ev.add(f"{pn} and {qn} played in the same team in {_n(ns, 'season')} ({teams})",
               dict(teammate=qn, seasons=ns, teams=teams))  # fmt: skip
        ev.node(qid, qn, "player")
        ev.edges.append({"source": pid, "target": qid, "kind": "teammates", "seasons": ns, "teams": teams})
    return ev


def connection(con, a: int, b: int) -> Evidence:
    """Shortest chain of teammates linking two players; each link names the team and season it rests on."""
    g = gx.teammate_graph(con)
    an, bn = name_of(con, a), name_of(con, b)
    ev = Evidence("connection", title=f"How {an} links to {bn}")
    if a not in g or b not in g:
        ev.gap = f"{an if a not in g else bn} has no teammate links in the archive."
        return ev
    try:
        path = nx.shortest_path(g, a, b)
    except nx.NetworkXNoPath:
        ev.gap = f"No chain of teammates links {an} and {bn}."
        return ev
    ev.add(
        f"{an} and {bn} are linked in {_n(len(path) - 1, 'teammate step')}",
        dict(a=an, b=bn, steps=len(path) - 1),
    )
    for x, y in itertools.pairwise(path):
        team, season = con.execute(
            """SELECT a.team, a.season FROM played_for a JOIN played_for b ON a.team = b.team AND a.season = b.season
               WHERE a.player_id = ? AND b.player_id = ? ORDER BY a.season LIMIT 1""",
            [x, y],
        ).fetchone() or ("?", "?")
        xn, yn = name_of(con, x), name_of(con, y)
        ev.add(
            f"{xn} and {yn} were teammates at {team} in {season}", dict(a=xn, b=yn, team=team, season=season)
        )
        ev.node(x, xn, "player")
        ev.node(y, yn, "player")
        ev.edges.append({"source": x, "target": y, "kind": "teammates", "team": team, "season": season})
    return ev


# ------------------------------------------------------------------ network rankings


def ranking(con, kind: str = "bowler", seasons=None, top: int = 10) -> Evidence:
    fn = gx.bowler_rank if kind == "bowler" else gx.batter_rank
    rows = fn(con, seasons, top=top)
    what = "wickets" if kind == "bowler" else "runs"
    least = gx.qualify(seasons, None)
    ev = Evidence(
        "ranking",
        title=f"Network rank of {kind}s, min {least} balls (PageRank heuristic, not an official stat)",
    )
    if rows:
        ev.add(
            f"Network ranking of {kind}s {_scope(seasons)}, minimum {least} balls (PageRank heuristic, not an official stat)"
        )
    for i, (name, score, n) in enumerate(rows, 1):
        ev.add(f"#{i} {name}: network score {score} ({n} {what} {_scope(seasons)})",
               dict(rank=i, player=name, score=score, **{what: n}))  # fmt: skip
    if not rows:
        ev.gap = f"Not enough {kind} data {_scope(seasons)} for a network ranking."
    return ev
