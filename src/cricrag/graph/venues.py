"""Grounds as a graph feature, and players measured against each ground's par.

Built tables (graph.duckdb, from the archive's balls):
  venue_season   per (venue, season): balls, runs, wickets, boundaries, pace / spin splits, innings totals, chases
  venue_player   per (player, role, venue, season): balls, runs, outs (bat) or wickets (bowl)

Analytics:
  profiles       six numbers per ground: run rate, boundary %, spin share of wickets, spin minus pace economy,
                 first-innings average, chase-win %; z-scored across grounds
  similar        grounds that play alike: cosine similarity of the z-scored profiles (100 = identical)
  families       a 3-nearest-neighbour graph of grounds, Louvain communities, each named by its strongest traits
  masters        batters by runs above the ground's par per 100 balls; bowlers by runs saved per 24 balls
  player_venues  one player's grounds, by the same measures

Par is everyone else at that ground in the same seasons (the player's own balls are left out), so a batter is not
measured against himself and a flat ground does not flatter him. Rates are steadied toward par by PRIOR_BALLS.
Ground profiles are taken against the IPL of the same seasons, so the era a ground was used in does not decide its
type. Runs are off the bat; wickets are the bowler's.
"""

from __future__ import annotations

import math
import re

import networkx as nx

# One name per ground whatever it was called that season (the same mapping as cricbot's ground() macro).
GROUND_SQL = """CASE split_part({v}, ',', 1)
    WHEN 'Feroz Shah Kotla' THEN 'Arun Jaitley Stadium'
    WHEN 'Sardar Patel Stadium' THEN 'Narendra Modi Stadium'
    WHEN 'Punjab Cricket Association Stadium' THEN 'Punjab Cricket Association IS Bindra Stadium'
    WHEN 'Sheikh Zayed Stadium' THEN 'Zayed Cricket Stadium'
    WHEN 'Subrata Roy Sahara Stadium' THEN 'Maharashtra Cricket Association Stadium'
    WHEN 'Andhra Cricket Association-Visakhapatnam District Cricket Association Stadium'
        THEN 'Dr. Y.S. Rajasekhara Reddy ACA-VDCA Cricket Stadium'
    ELSE split_part({v}, ',', 1) END"""

FEATURES = {  # name -> (label, unit, sql over venue_season sums)
    "run_rate": ("Run rate", "runs an over", "6.0 * sum(runs) / sum(balls)"),
    "boundary_pct": ("Boundary balls", "%", "100.0 * sum(boundaries) / sum(balls)"),
    "spin_wkt_share": ("Wickets to spin", "%", "100.0 * sum(spin_wkts) / nullif(sum(wkts), 0)"),
    "spin_minus_pace": (
        "Spin economy minus pace",
        "runs an over",
        "6.0 * sum(spin_runs) / nullif(sum(spin_balls), 0) - 6.0 * sum(pace_runs) / nullif(sum(pace_balls), 0)",
    ),
    "first_inns": ("First-innings average", "runs", "sum(first_total) / nullif(sum(first_n), 0)"),
    "chase_win_pct": ("Chases won", "%", "100.0 * sum(chase_won) / nullif(sum(chases), 0)"),
}
MIN_MATCHES = 8  # a ground with fewer matches has no stable profile
PRIOR_BALLS = 60  # above-par rates are steadied toward par by 60 balls' worth: 106 balls of 150 is not a law
TRAITS = {  # (feature, sign) -> words for a family's name
    ("run_rate", 1): "high-scoring",
    ("run_rate", -1): "low-scoring",
    ("boundary_pct", 1): "boundary-heavy",
    ("boundary_pct", -1): "few boundaries",
    ("spin_wkt_share", 1): "spin takes wickets",
    ("spin_wkt_share", -1): "pace takes wickets",
    ("spin_minus_pace", -1): "spin is hard to hit",
    ("spin_minus_pace", 1): "spin goes for runs",
    ("first_inns", 1): "big first innings",
    ("first_inns", -1): "small first innings",
    ("chase_win_pct", 1): "chasers win",
    ("chase_win_pct", -1): "defenders win",
}

BUILD = """
CREATE OR REPLACE TEMP VIEW vb AS
SELECT b.*, {ground} AS venue, {kind} AS bowl_kind
FROM gb b JOIN cb.matches m USING (match_id) {styles}
WHERE b.bowler_id IS NOT NULL AND b.batter_id IS NOT NULL;

CREATE OR REPLACE TABLE venue_season AS
WITH inns AS (
    SELECT match_id, inning, any_value(venue) AS venue, any_value(season) AS season, sum(runs_total) AS total
    FROM vb GROUP BY 1, 2),
res AS (
    SELECT i.venue, i.season, count(*) FILTER (WHERE i.inning = 1) AS first_n,
           sum(i.total) FILTER (WHERE i.inning = 1) AS first_total,
           count(*) FILTER (WHERE i.inning = 2 AND m.winner IS NOT NULL AND m.winner <> '') AS chases,
           count(*) FILTER (WHERE i.inning = 2 AND replace(m.winner, 'KXIP', 'PBKS') = im.bat) AS chase_won,
           count(DISTINCT i.match_id) AS matches
    FROM inns i JOIN cb.matches m USING (match_id) JOIN inn im ON im.match_id = i.match_id AND im.inning = i.inning
    GROUP BY 1, 2)
SELECT venue, season, any_value(r.matches) AS matches,
       sum((NOT is_wide)::INT) AS balls, sum(runs_bat) AS runs, sum(bowler_wicket::INT) AS wkts,
       sum((is_four OR is_six)::INT) AS boundaries,
       sum((NOT is_wide AND bowl_kind = 'pace')::INT) AS pace_balls, sum(runs_bat) FILTER (WHERE bowl_kind = 'pace') AS pace_runs,
       sum((bowler_wicket AND bowl_kind = 'pace')::INT) AS pace_wkts,
       sum((NOT is_wide AND bowl_kind = 'spin')::INT) AS spin_balls, sum(runs_bat) FILTER (WHERE bowl_kind = 'spin') AS spin_runs,
       sum((bowler_wicket AND bowl_kind = 'spin')::INT) AS spin_wkts,
       any_value(r.first_n) AS first_n, any_value(r.first_total) AS first_total,
       any_value(r.chases) AS chases, any_value(r.chase_won) AS chase_won
FROM vb LEFT JOIN res r USING (venue, season) GROUP BY 1, 2;

CREATE OR REPLACE TABLE venue_player AS
SELECT {bat} AS player_id, 'bat' AS role, venue, season, sum((NOT is_wide)::INT) AS balls, sum(runs_bat) AS runs,
       sum((bowler_wicket AND player_out_id = batter_id)::INT) AS outs
FROM vb GROUP BY 1, 2, 3, 4
UNION ALL
SELECT {bowl}, 'bowl', venue, season, sum((NOT is_wide)::INT), sum(runs_bat), sum(bowler_wicket::INT)
FROM vb GROUP BY 1, 2, 3, 4;
"""

CHECKS = {
    "venue_player bat runs == bowled_to.runs": (
        "SELECT sum(runs) FROM venue_player WHERE role = 'bat'",
        "SELECT sum(runs) FROM bowled_to",
    ),
    "venue_player bowl wickets == dismissed.n": (
        "SELECT sum(outs) FROM venue_player WHERE role = 'bowl'",
        "SELECT sum(n) FROM dismissed",
    ),
}


def build_sql(canon: str, styles: bool = True) -> str:
    return BUILD.format(
        ground=GROUND_SQL.format(v="m.venue"),
        kind="s.bowl_kind" if styles else "NULL",
        styles="LEFT JOIN cb.player_styles s ON s.player_id = b.bowler_id" if styles else "",
        bat=canon.format("batter_id"),
        bowl=canon.format("bowler_id"),
    )


def _where(seasons, col: str = "season") -> tuple[str, list]:
    if not seasons:
        return "TRUE", []
    return f"{col} IN ({','.join('?' * len(seasons))})", list(seasons)


def profiles(con, seasons=None) -> dict[str, dict]:
    """venue -> {matches, features (raw), delta (v the IPL in the same seasons), z} for grounds with MIN_MATCHES.

    Each feature is taken against the league in the same season, then averaged over the ground's seasons (weighted
    by balls), so a ground used only in a high-scoring era is not mistaken for a high-scoring ground.
    """
    w, p = _where(seasons)
    cols = ", ".join(f"{sql} AS {k}" for k, (_, _, sql) in FEATURES.items())
    vs = con.execute(
        f"SELECT venue, season, sum(matches), sum(balls), {cols} FROM venue_season WHERE {w} GROUP BY 1, 2", p
    ).fetchall()
    league = {
        r[0]: r[1:]
        for r in con.execute(f"SELECT season, {cols} FROM venue_season WHERE {w} GROUP BY 1", p).fetchall()
    }
    raw = {r[0]: r[1:] for r in con.execute(
        f"SELECT venue, sum(matches), {cols} FROM venue_season WHERE {w} GROUP BY 1 HAVING sum(matches) >= ?", [*p, MIN_MATCHES]
    ).fetchall()}  # fmt: skip
    acc: dict[str, dict[str, list[float]]] = {}
    for venue, season, _m, balls, *vals in vs:
        if venue not in raw:
            continue
        for k, v, lv in zip(FEATURES, vals, league[season], strict=True):
            if v is not None and lv is not None:
                a = acc.setdefault(venue, {}).setdefault(k, [0.0, 0.0])
                a[0] += float(balls) * (float(v) - float(lv))
                a[1] += float(balls)
    out = {}
    for venue, (matches, *vals) in raw.items():
        d = {k: (acc.get(venue, {}).get(k, [0, 0])[0] / acc[venue][k][1]) if acc.get(venue, {}).get(k, [0, 0])[1] else 0.0
             for k in FEATURES}  # fmt: skip
        out[venue] = {"matches": int(matches), **{k: (float(v) if v is not None else None) for k, v in zip(FEATURES, vals, strict=True)},
                      "delta": d}  # fmt: skip
    for k in FEATURES:
        vals = [v["delta"][k] for v in out.values()]
        sd = (
            math.sqrt(sum(x * x for x in vals) / len(vals)) if vals else 0.0
        )  # deltas are already centred on the IPL
        for v in out.values():
            v.setdefault("z", {})[k] = v["delta"][k] / sd if sd else 0.0
    return out


def _cos(a: dict, b: dict) -> float:
    dot = sum(a[k] * b[k] for k in FEATURES)
    na, nb = math.sqrt(sum(a[k] ** 2 for k in FEATURES)), math.sqrt(sum(b[k] ** 2 for k in FEATURES))
    return dot / (na * nb) if na and nb else 0.0


def similarity(a: dict, b: dict) -> int:
    """0-100: how alike two grounds' z-scored profiles point (cosine mapped from -1..1)."""
    return round(50 * (1 + _cos(a["z"], b["z"])))


def similar(con, venue: str, seasons=None, top: int = 6) -> list[tuple[str, int]]:
    prof = profiles(con, seasons)
    if venue not in prof:
        return []
    return sorted(
        ((v, similarity(prof[venue], x)) for v, x in prof.items() if v != venue), key=lambda t: -t[1]
    )[:top]


def trait_name(z: dict[str, float], n: int = 2) -> str:
    strongest = sorted(FEATURES, key=lambda k: -abs(z[k]))[:n]
    return (
        ", ".join(TRAITS[(k, 1 if z[k] > 0 else -1)] for k in strongest if abs(z[k]) >= 0.3)
        or "typical IPL ground"
    )


def families(con, seasons=None, k: int = 3, seed: int = 7) -> list[dict]:
    """Louvain communities on the k-nearest-neighbour graph of grounds; each named by its mean profile's traits."""
    prof = profiles(con, seasons)
    g = nx.Graph()
    g.add_nodes_from(prof)
    for v, x in prof.items():
        for u, s in sorted(
            ((u, _cos(x["z"], y["z"])) for u, y in prof.items() if u != v), key=lambda t: -t[1]
        )[:k]:
            if s > 0:
                g.add_edge(v, u, weight=s)
    groups = nx.community.louvain_communities(g, weight="weight", seed=seed) if g.number_of_edges() else []
    out = []
    for grp in groups:
        members = sorted(grp, key=lambda v: -prof[v]["matches"])
        z = {f: sum(prof[v]["z"][f] for v in members) / len(members) for f in FEATURES}
        out.append(
            {"name": trait_name(z), "venues": members, "matches": sum(prof[v]["matches"] for v in members)}
        )
    return sorted(out, key=lambda f: -f["matches"])


def family_of(con, venue: str, seasons=None) -> dict | None:
    return next((f for f in families(con, seasons) if venue in f["venues"]), None)


PAR = """WITH vs AS (SELECT venue, season, balls, runs, wkts FROM venue_season WHERE {w}),
pv AS (SELECT * FROM venue_player WHERE role = ? AND {w}),
me AS (SELECT p.player_id, p.venue, p.balls, p.runs, p.outs,
              p.balls * (v.runs - p.runs) / nullif(v.balls - p.balls, 0) AS par_runs,
              p.balls * (v.wkts - p.outs) * 1.0 / nullif(v.balls - p.balls, 0) AS par_wkts
       FROM pv p JOIN vs v USING (venue, season))
SELECT player_id, venue, sum(balls)::INT, sum(runs)::INT, sum(outs)::INT, sum(par_runs), sum(par_wkts)
FROM me WHERE {extra} GROUP BY 1, 2 HAVING sum(balls) >= ?"""


def _rows(con, role: str, seasons, extra: str, params: list, min_balls: int) -> list[dict]:
    w, p = _where(seasons)
    q = PAR.format(w=w, extra=extra)
    names = dict(con.execute("SELECT player_id, name FROM people").fetchall())
    out = []
    for pid, venue, balls, runs, outs, par_r, par_w in con.execute(
        q, [*p, role, *p, *params, min_balls]
    ).fetchall():
        par_r, par_w = float(par_r or 0), float(par_w or 0)
        r = {"player": names.get(pid, str(pid)), "venue": venue, "balls": balls, "runs": runs}
        if role == "bat":
            r |= {"outs": outs, "strike_rate": round(100 * runs / balls, 1), "par_strike_rate": round(100 * par_r / balls, 1),
                  "average": round(runs / outs, 1) if outs else None, "par_average": round(par_r / par_w, 1) if par_w else None,
                  "above_par": round(100 * (runs - par_r) / (balls + PRIOR_BALLS), 1)}  # fmt: skip
        else:
            r |= {"wickets": outs, "economy": round(6 * runs / balls, 2), "par_economy": round(6 * par_r / balls, 2),
                  "runs_saved": round(24 * (par_r - runs) / (balls + PRIOR_BALLS), 1), "wickets_above_par": round(outs - par_w, 1)}  # fmt: skip
        out.append(r)
    key = "above_par" if role == "bat" else "runs_saved"
    return sorted(out, key=lambda r: -r[key])


def masters(
    con, venue: str, role: str = "bat", seasons=None, min_balls: int = 120, top: int = 8
) -> list[dict]:
    """The players who beat this ground's par most: batters by runs above par per 100 balls, bowlers by runs saved
    per 24 balls (min balls at the ground)."""
    return _rows(con, role, seasons, "venue = ?", [venue], min_balls)[:top]


def player_venues(con, pid: int, role: str = "bat", seasons=None, min_balls: int = 60) -> list[dict]:
    return _rows(con, role, seasons, "player_id = ?", [pid], min_balls)


# ------------------------------------------------------------------ naming a ground in a question
STOP = {"stadium", "cricket", "international", "association", "sports", "academy", "ground", "the", "and", "of",
        "dr", "y.s.", "aca-vdca", "rajasekhara", "reddy", "is", "sahara", "punjab"}  # fmt: skip
ALIASES = {
    "chepauk": "MA Chidambaram Stadium", "chidambaram": "MA Chidambaram Stadium", "kotla": "Arun Jaitley Stadium",
    "motera": "Narendra Modi Stadium", "uppal": "Rajiv Gandhi International Stadium", "mohali": "Punjab Cricket Association IS Bindra Stadium",
    "eden": "Eden Gardens", "chinnaswamy": "M Chinnaswamy Stadium", "wankhede": "Wankhede Stadium", "brabourne": "Brabourne Stadium",
    "sawai": "Sawai Mansingh Stadium", "mansingh": "Sawai Mansingh Stadium", "ekana": "Bharat Ratna Shri Atal Bihari Vajpayee Ekana Cricket Stadium",
    "mullanpur": "Maharaja Yadavindra Singh International Cricket Stadium", "dharamsala": "Himachal Pradesh Cricket Association Stadium",
    "guwahati": "Barsapara Cricket Stadium", "barsapara": "Barsapara Cricket Stadium", "sharjah": "Sharjah Cricket Stadium",
    "dubai": "Dubai International Cricket Stadium", "patil": "Dr DY Patil Sports Academy", "vizag": "Dr. Y.S. Rajasekhara Reddy ACA-VDCA Cricket Stadium",
}  # fmt: skip


SHORT = {
    "MA Chidambaram Stadium": "Chepauk", "Arun Jaitley Stadium": "Arun Jaitley (Kotla)", "Narendra Modi Stadium": "Narendra Modi",
    "Rajiv Gandhi International Stadium": "Uppal", "Punjab Cricket Association IS Bindra Stadium": "Mohali",
    "Maharaja Yadavindra Singh International Cricket Stadium": "Mullanpur", "M Chinnaswamy Stadium": "Chinnaswamy",
    "Bharat Ratna Shri Atal Bihari Vajpayee Ekana Cricket Stadium": "Ekana", "Himachal Pradesh Cricket Association Stadium": "Dharamsala",
    "Dr. Y.S. Rajasekhara Reddy ACA-VDCA Cricket Stadium": "Visakhapatnam", "Maharashtra Cricket Association Stadium": "Pune (MCA)",
    "Shaheed Veer Narayan Singh International Stadium": "Raipur", "Saurashtra Cricket Association Stadium": "Rajkot",
    "Dr DY Patil Sports Academy": "DY Patil", "Dubai International Cricket Stadium": "Dubai", "Sawai Mansingh Stadium": "Sawai Mansingh",
    "Barsapara Cricket Stadium": "Guwahati", "Holkar Cricket Stadium": "Indore", "Sharjah Cricket Stadium": "Sharjah",
    "Zayed Cricket Stadium": "Abu Dhabi", "Wankhede Stadium": "Wankhede", "Brabourne Stadium": "Brabourne",
}  # fmt: skip


def short(venue: str) -> str:
    """The name fans use, for labels on a picture (tables and evidence keep the full name)."""
    if venue in SHORT:
        return SHORT[venue]
    words = [
        w
        for w in venue.split()
        if w.casefold() not in {"stadium", "cricket", "international", "association", "ground"}
    ]
    return " ".join(words) or venue


def find_venue(con, text: str) -> str | None:
    """The ground a question names (alias or a distinctive word of its name), or None."""
    known = [
        r[0]
        for r in con.execute(
            "SELECT venue, sum(matches) m FROM venue_season GROUP BY 1 ORDER BY m DESC"
        ).fetchall()
    ]
    low = text.casefold()
    for alias, venue in ALIASES.items():
        if re.search(rf"\b{alias}\b", low):
            hit = next((v for v in known if v.casefold().startswith(venue.casefold()[:12])), None)
            if hit:
                return hit
    for v in known:  # most-played first, so a shared word picks the busier ground
        words = [w for w in re.findall(r"[a-z][a-z.\-]{3,}", v.casefold()) if w not in STOP]
        if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
            return v
    return None
