"""What the match page and the rail add on top of cricbot's archive API. All local SQL, no network.

commentary    cricbot fetches ESPN commentary live for Cricsheet matches and shows none when that call
              fails; the archive already stores it, so it is merged in from there
shifts        cricrag's momentum shifts: the decisive one favours the winner (cricbot's may not)
drops         audited dropped catches: fielder, bowler, the batter's score then, runs added afterwards
hall_of_fame  all-time lists for the rail
"""

from __future__ import annotations

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
