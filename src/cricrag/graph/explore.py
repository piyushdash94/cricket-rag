"""NetworkX analytics over data/graph.duckdb (step 2 exploration; the agent's templated tools come later).

Every function returns plain rows whose numbers come from the edge tables, so a writer can cite them.
Rankings are network *heuristics*, not official stats; label them that way in any answer.

  bowler_rank      PageRank on batter -> bowler edges weighted by dismissals: a wicket of a batter whom
                   many good bowlers struggle to dismiss is worth more (after Mukherjee's Test-cricket networks)
  batter_rank      PageRank on bowler -> batter edges weighted by runs per dismissal against that bowler
  nemesis          the bowlers who dismissed one batter most, with balls/runs between them
  fielder_bowler   caught / stumped pairs ("c X b Y")
  communities      Louvain clusters on the teammate graph (played for the same team in the same season)
"""

from __future__ import annotations

import networkx as nx

SEASONS = "({col} IN ({ph}))"


def _where(seasons: list[str] | None, col: str = "season") -> tuple[str, list]:
    if not seasons:
        return "TRUE", []
    return SEASONS.format(col=col, ph=",".join("?" * len(seasons))), list(seasons)


def pagerank(g: nx.DiGraph, alpha: float = 0.85, tol: float = 1e-10, max_iter: int = 200) -> dict:
    """Weighted PageRank by power iteration (networkx's own needs scipy; this keeps the install light).
    Dangling nodes spread their rank uniformly, as in networkx."""
    nodes = list(g)
    if not nodes:
        return {}
    n = len(nodes)
    out_w = {u: sum(d.get("weight", 1.0) for _, _, d in g.out_edges(u, data=True)) for u in nodes}
    rank = dict.fromkeys(nodes, 1.0 / n)
    for _ in range(max_iter):
        dangling = alpha * sum(rank[u] for u in nodes if out_w[u] == 0) / n
        new = dict.fromkeys(nodes, (1 - alpha) / n + dangling)
        for u, v, d in g.edges(data=True):
            new[v] += alpha * rank[u] * d.get("weight", 1.0) / out_w[u]
        if sum(abs(new[u] - rank[u]) for u in nodes) < n * tol:
            return new
        rank = new
    return rank


def qualify(seasons, min_balls: int | None) -> int:
    """Balls needed to be ranked: 300 across seasons, 120 (20 overs) inside one or two seasons."""
    if min_balls is not None:
        return min_balls
    return 120 if seasons and len(seasons) <= 2 else 300


def names(con) -> dict[int, str]:
    return dict(con.execute("SELECT player_id, name FROM people").fetchall())


def bowler_rank(
    con, seasons=None, min_balls: int | None = None, top: int = 15
) -> list[tuple[str, float, int]]:
    w, p = _where(seasons)
    edges = con.execute(
        f"SELECT batter_id, bowler_id, sum(n) FROM dismissed WHERE {w} GROUP BY 1, 2", p
    ).fetchall()
    g = nx.DiGraph()
    g.add_weighted_edges_from(edges)
    pr = pagerank(g)
    vol = dict(con.execute(f"SELECT bowler_id, sum(balls) FROM bowled_to WHERE {w} GROUP BY 1", p).fetchall())
    wk = dict(con.execute(f"SELECT bowler_id, sum(n) FROM dismissed WHERE {w} GROUP BY 1", p).fetchall())
    nm = names(con)
    rows = [
        (nm.get(b, str(b)), round(s * 1000, 3), int(wk.get(b, 0)))
        for b, s in pr.items()
        if vol.get(b, 0) >= qualify(seasons, min_balls)
    ]
    return sorted(rows, key=lambda r: -r[1])[:top]


def batter_rank(
    con, seasons=None, min_balls: int | None = None, top: int = 15
) -> list[tuple[str, float, int]]:
    w, p = _where(seasons)
    edges = con.execute(
        f"""SELECT bowler_id, batter_id, sum(runs) * 1.0 / greatest(sum(dismissals), 1)
            FROM bowled_to WHERE {w} GROUP BY 1, 2 HAVING sum(balls) >= 6""",
        p,
    ).fetchall()
    g = nx.DiGraph()
    g.add_weighted_edges_from(edges)
    pr = pagerank(g)
    vol = con.execute(
        f"SELECT batter_id, sum(balls), sum(runs) FROM bowled_to WHERE {w} GROUP BY 1", p
    ).fetchall()
    faced = {b: (int(n), int(r)) for b, n, r in vol}
    nm = names(con)
    rows = [
        (nm.get(b, str(b)), round(s * 1000, 3), faced[b][1])
        for b, s in pr.items()
        if faced.get(b, (0, 0))[0] >= qualify(seasons, min_balls)
    ]
    return sorted(rows, key=lambda r: -r[1])[:top]


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


def teammate_graph(con, seasons=None) -> nx.Graph:
    w, p = _where(seasons)
    pairs = con.execute(
        f"""SELECT a.player_id, b.player_id, count(*) FROM played_for a JOIN played_for b
            ON a.team = b.team AND a.season = b.season AND a.player_id < b.player_id
            WHERE {w.replace("season", "a.season")} GROUP BY 1, 2""",
        p,
    ).fetchall()
    g = nx.Graph()
    g.add_weighted_edges_from(pairs)
    return g


def communities(con, seasons=None, min_size: int = 8, seed: int = 7) -> list[list[str]]:
    """Louvain clusters on the teammate graph, largest first; members sorted by matches played."""
    g = teammate_graph(con, seasons)
    if not g:
        return []
    groups = nx.community.louvain_communities(g, weight="weight", seed=seed)
    played = dict(con.execute("SELECT player_id, matches FROM people").fetchall())
    nm = names(con)
    out = [sorted(c, key=lambda x: -played.get(x, 0)) for c in groups if len(c) >= min_size]
    return [[nm.get(x, str(x)) for x in c] for c in sorted(out, key=len, reverse=True)]


def find_player(con, name: str) -> list[tuple[int, str, int]]:
    """People whose display name or any alias contains the text (for the CLI; the agent uses entities.py)."""
    q = f"%{name.casefold()}%"
    return con.execute(
        """SELECT player_id, name, matches FROM people
           WHERE lower(name) LIKE ? OR lower(array_to_string(aliases, '|')) LIKE ?
           ORDER BY matches DESC LIMIT 5""",
        [q, q],
    ).fetchall()
