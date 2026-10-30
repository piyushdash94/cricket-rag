"""What the match page and the rail add on top of cricbot's archive API. All local SQL, no network.

commentary    cricbot fetches ESPN commentary live for Cricsheet matches and shows none when that call
              fails; the archive already stores it, so it is merged in from there
shifts        cricrag's momentum shifts: the decisive one favours the winner (cricbot's may not)
drops         audited dropped catches: fielder, bowler, the batter's score then, runs added afterwards
hall_of_fame  all-time lists for the rail
"""

from __future__ import annotations

import re
from typing import Any

from cricrag.entities import canon


def archive_commentary(con, mid: int) -> list[dict[str, Any]]:
    rows = con.execute(
        "select inning, over_label, commentary from balls where match_id = ? and inning <= 2 order by inning, sequence",
        [mid],
    ).fetchall()
    return [{"inning": i, "label": str(lbl), "text": t} for i, lbl, t in rows if t]


def merge_commentary(balls: list[dict[str, Any]], commentary: list[dict[str, Any]]) -> int:
    """Attach commentary to deliveries by (innings, over label, occurrence); wides repeat a label in both feeds."""
    texts: dict[tuple[int, str, int], str] = {}
    seen: dict[tuple[int, str], int] = {}
    for c in commentary:
        k = (c["inning"], c["label"])
        seen[k] = seen.get(k, 0) + 1
        texts[(*k, seen[k])] = c["text"]
    seen, n = {}, 0
    for b in balls:
        k = (b.get("inning"), str(b.get("label")))
        seen[k] = seen.get(k, 0) + 1
        if (text := texts.get((*k, seen[k]))) and not b.get("commentary"):
            b["commentary"] = text
            n += 1
    return n


def winner_shifts(con, mid: int, drops: dict | None = None) -> list[dict[str, Any]] | None:
    from cricrag.momentum import match_shifts
    from cricrag.story import match_balls

    balls, innings = match_balls(con, mid, drops)
    if len(innings) != 2:
        return None
    row = con.execute("select winner from matches where match_id = ?", [mid]).fetchone()
    winner = canon(row[0]) if row and row[0] else None
    return match_shifts(balls, innings, winner=winner)


DROPS = """select inning, label, player, player2, detail from events
           where type = 'drop' and match_id = ? order by inning, "over", label"""
BALLS = """select inning, sequence, over_label, batter, bowler, coalesce(runs_bat, 0), coalesce(is_legal, true),
                  player_out from balls where match_id = ? and inning <= 2 order by inning, sequence"""


def drops(con, events, mid: int) -> list[dict[str, Any]]:
    """Each audited drop with the bowler and the batter's runs before and after it, all from the balls table."""
    try:
        found = events.execute(DROPS, [mid]).fetchall()
    except Exception:
        return []
    if not found:
        return []
    balls = con.execute(BALLS, [mid]).fetchall()
    out = []
    for inn, label, fielder, batter_hint, confidence in found:
        at = [b for b in balls if b[0] == inn and str(b[2]) == str(label)]
        if not at:
            continue
        ball = next((b for b in reversed(at) if b[6]), at[-1])  # the legal ball under that label
        batter, bowler, seq = ball[3], ball[4], ball[1]
        mine = [b for b in balls if b[0] == inn and b[3] == batter]
        before = sum(b[5] for b in mine if b[1] <= seq)
        total = sum(b[5] for b in mine)
        out_at = next((b for b in balls if b[0] == inn and b[7] == batter), None)
        out.append(
            {
                "inning": inn,
                "over": str(label),
                "fielder": fielder or None,
                "batter": batter or batter_hint,
                "bowler": bowler,
                "score_then": before,
                "final": total,
                "added": total - before,
                "balls_after": sum(1 for b in mine if b[1] > seq and b[6]),
                "out": bool(out_at),
                "confidence": confidence,
            }
        )
    return out


def enrich(detail: dict[str, Any], con, events, mid: int) -> dict[str, Any]:
    """Fill cricbot's match detail in place from the archive; each step is optional and fails quietly."""
    balls = detail.get("balls") or []
    if balls and not detail.get("commentary_count"):
        try:
            n = merge_commentary(balls, archive_commentary(con, mid))
        except Exception:
            n = 0
        if n:
            detail["commentary_count"] = n
            for s in detail.get("sources") or []:
                if s.get("name") == "Commentary":
                    s.update(provider="ESPN commentary (local archive)", available=True)
            cov = detail.get("ball_coverage") or {}
            cov["note"] = (cov.get("note") or "") + f" Commentary from the local archive on {n} deliveries."
    found = drops(con, events, mid) if events is not None else []
    # the audited drops (events) mark missed chances; the commentary regex is only a fallback
    drops_map = (
        {(mid, d["inning"], d["over"]): d["fielder"] or "" for d in found} if events is not None else None
    )
    try:
        shifts = winner_shifts(con, mid, drops_map)
    except Exception:
        shifts = None
    if shifts is not None:
        detail.setdefault("analytics", {})["shifts"] = shifts
    detail["drops"] = found
    return detail


# ---------------------------------------------------------------- hall of fame (all seasons, archive balls)

_B = "balls where inning <= 2"
HALL = {
    "Most runs": (
        "batting",
        f"select any_value(batter), sum(runs_bat) v from {_B} group by batter_id order by v desc limit 5",
        "runs",
    ),
    "Most sixes": (
        "batting",
        f"select any_value(batter), sum(is_six::int) v from {_B} group by batter_id order by v desc limit 5",
        "sixes",
    ),
    "Highest score": (
        "batting",
        f"""select any_value(batter) || ' (' || any_value(year(m.start_date))::varchar || ')', sum(runs_bat) v
            from {_B.replace("balls", "balls b join matches m using (match_id)")}
            group by b.match_id, batter_id order by v desc limit 5""",
        "",
    ),
    "Most wickets": (
        "bowling",
        f"select any_value(bowler), sum(bowler_wicket::int) v from {_B} group by bowler_id order by v desc limit 5",
        "wkts",
    ),
    "Best figures": (
        "bowling",
        f"""select any_value(bowler) || ' (' || any_value(year(m.start_date))::varchar || ')',
                   sum(bowler_wicket::int) || '/' || sum(runs_total - coalesce(bye_runs, 0) - coalesce(legbye_runs, 0)) v,
                   sum(bowler_wicket::int) w, sum(runs_total - coalesce(bye_runs, 0) - coalesce(legbye_runs, 0)) r
            from {_B.replace("balls", "balls b join matches m using (match_id)")}
            group by b.match_id, bowler_id order by w desc, r limit 5""",
        "",
    ),
    "Most catches": (
        "fielding",
        """select coalesce(any_value(p.name), any_value(f.fielder)), count(*) v from cs_fielding f
           left join player_styles p on p.player_id = f.fielder_id
           where kind = 'caught' group by f.fielder_id order by v desc limit 5""",
        "catches",
    ),
    "Most titles": (
        "teams",
        "select winner, count(*) v from matches where title = 'Final' and winner <> '' group by 1 order by v desc, 1 limit 5",
        "",
    ),
    "Highest team total": (
        "teams",
        """select any_value(b.batting_team) || ' v ' ||
                  any_value(case when replace(m.team1, 'KXIP', 'PBKS') = b.batting_team
                                then replace(m.team2, 'KXIP', 'PBKS') else replace(m.team1, 'KXIP', 'PBKS') end) || ' (' || any_value(year(m.start_date))::varchar || ')',
                  sum(runs_total) || '/' || sum(is_wicket::int) v, sum(runs_total) r
           from balls b join matches m using (match_id) where inning <= 2
           group by b.match_id, inning order by r desc limit 5""",
        "",
    ),
    "Most player-of-the-match awards": (
        "awards",
        """select coalesce(any_value(p.name), any_value(a.player)), count(*) v from cs_awards a
           left join player_styles p using (player_id) group by a.player_id order by v desc limit 5""",
        "awards",
    ),
}


def hall_of_fame(con) -> dict[str, Any]:
    lists = []
    for title, (group, sql, unit) in HALL.items():
        try:
            rows = con.execute(sql).fetchall()
        except Exception:
            continue
        lists.append(
            {
                "title": title,
                "group": group,
                "unit": unit,
                "rows": [
                    {"name": str(r[0]), "value": r[1] if isinstance(r[1], str) else int(r[1])} for r in rows
                ],
            }
        )
    lo, hi = con.execute("select min(year(start_date)), max(year(start_date)) from matches").fetchone()
    return {"lists": lists, "span": f"{lo}–{hi}" if lo else ""}


# ---------------------------------------------------------------- front page (one season, plus every season's scoring)

_FIX = "replace({}, 'KXIP', 'PBKS')"
INNINGS = f"""
WITH m AS (SELECT match_id, year(start_date) yr, title, start_date, status_text, {_FIX.format("winner")} winner,
                  {_FIX.format("team1")} t1, {_FIX.format("team2")} t2
           FROM matches WHERE NOT coalesce(is_cancelled, false)),
i AS (SELECT match_id, inning, any_value(batting_team) team, sum(runs_total) runs, sum(is_wicket::INT) wkts,
             sum(is_legal::INT) legal, sum(is_six::INT) sixes, sum(is_four::INT) fours
      FROM balls WHERE inning <= 2 GROUP BY 1, 2)
SELECT m.*, i.inning, i.team, i.runs, i.wkts, i.legal, i.sixes, i.fours FROM m JOIN i USING (match_id)
"""


def eras(con) -> list[dict[str, Any]]:
    """Every season's scoring: average completed first-innings total, sixes and run rate per match."""
    rows = con.execute(
        f"""WITH x AS ({INNINGS})
        SELECT yr, round(avg(runs) FILTER (WHERE inning = 1 AND (wkts = 10 OR legal >= 120)), 1) first,
               round(sum(sixes) / count(DISTINCT match_id), 1) sixes,
               round(6.0 * sum(runs) / sum(legal), 2) rr, count(DISTINCT match_id) matches
        FROM x GROUP BY yr ORDER BY yr"""
    ).fetchall()
    return [{"year": y, "first": f, "sixes": s, "run_rate": r, "matches": n} for y, f, s, r, n in rows]


def _close(r: dict[str, Any]) -> tuple[int, str] | None:
    """(closeness, how) for a finished match: tie/super over, a small runs margin, or a chase won at the death."""
    status = (r["status_text"] or "").lower()
    if re.search(r"d/l|dls|vjd|no result|abandon", status) or r.get("r2") is None:
        return None
    if "tied" in status or "super over" in status:
        return 0, "Tied, decided by a super over" if "super over" in status else "Tied"
    if r["winner"] == r["team1"] and r["r1"] - r["r2"] <= 5:
        n = r["r1"] - r["r2"]
        return n, f"Won by {n} run{'s' if n != 1 else ''}"
    if r["winner"] == r["team2"] and 120 - r["legal2"] <= 1:
        left = 120 - r["legal2"]
        return left, "Chased down off the last ball" if left == 0 else "Chased down with one ball left"
    return None


def season_home(con, year: int) -> dict[str, Any]:
    """The front page for one season: final, caps, totals and its closest finishes. All SQL on the archive."""
    rows = con.execute(
        f"WITH x AS ({INNINGS}) SELECT * FROM x WHERE yr = ? ORDER BY start_date, match_id, inning", [year]
    )
    cols = [d[0] for d in rows.description]
    inns = [dict(zip(cols, r, strict=True)) for r in rows.fetchall()]
    if not inns:
        return {"year": year, "available": False}
    per: dict[int, dict[str, Any]] = {}
    for r in inns:
        m = per.setdefault(
            r["match_id"], {k: r[k] for k in ("match_id", "title", "start_date", "status_text", "winner")}
        )
        n = r["inning"]
        m[f"team{n}"], m[f"r{n}"], m[f"w{n}"], m[f"legal{n}"] = r["team"], r["runs"], r["wkts"], r["legal"]
    top = max(inns, key=lambda r: r["runs"])
    final = next((m for m in per.values() if m["title"] == "Final"), None)

    def leader(sql: str) -> dict[str, Any] | None:
        got = con.execute(sql, [year]).fetchone()
        return {"name": got[0], "value": int(got[1])} if got else None

    yb = "FROM balls b JOIN matches m USING (match_id) WHERE b.inning <= 2 AND year(m.start_date) = ?"
    close = sorted(
        ({**m, "how": c[1], "closeness": c[0]} for m in per.values() if (c := _close(m)) is not None),
        key=lambda m: (m["closeness"], m["start_date"]),
    )[:6]
    return {
        "year": year,
        "available": True,
        "matches": len(per),
        "runs": sum(r["runs"] for r in inns),
        "sixes": sum(r["sixes"] for r in inns),
        "fours": sum(r["fours"] for r in inns),
        "two_hundreds": sum(1 for r in inns if r["runs"] >= 200),
        "highest": {
            "team": top["team"],
            "runs": top["runs"],
            "wickets": top["wkts"],
            "match_id": top["match_id"],
            "opponent": next(
                (
                    x["team"]
                    for x in inns
                    if x["match_id"] == top["match_id"] and x["inning"] != top["inning"]
                ),
                "",
            ),
        },
        "final": final
        and {
            "match_id": final["match_id"],
            "winner": final["winner"],
            "result": final["status_text"],
            "date": str(final["start_date"]),
            "teams": [final.get("team1"), final.get("team2")],
        },
        "orange_cap": leader(
            f"SELECT any_value(batter), sum(runs_bat) v {yb} GROUP BY batter_id ORDER BY v DESC LIMIT 1"
        ),
        "purple_cap": leader(
            f"SELECT any_value(bowler), sum(bowler_wicket::INT) v {yb} GROUP BY bowler_id ORDER BY v DESC LIMIT 1"
        ),
        "most_sixes": leader(
            f"SELECT any_value(batter), sum(is_six::INT) v {yb} GROUP BY batter_id ORDER BY v DESC LIMIT 1"
        ),
        "thrillers": [
            {
                "match_id": m["match_id"],
                "title": m["title"],
                "date": str(m["start_date"]),
                "how": m["how"],
                "teams": [m.get("team1"), m.get("team2")],
                "scores": [f"{m.get('r1')}/{m.get('w1')}", f"{m.get('r2')}/{m.get('w2')}"],
                "winner": m["winner"],
                "result": m["status_text"],
            }
            for m in close
        ],
    }
