"""Match stories from SQL, not similarity search.

    facts  : result, toss, innings totals, top batters/bowlers, player of the match (cricbot's cs_* scorecards)
    shifts : cricbot's momentum detector (collapses, bursts, squeezes, chase swings, missed chances) run on
             the archive's balls; missed chances come from audited fielding_events when available
    overs  : the overs behind each shift, so the agent can quote commentary by id

Every number here comes from SQL or arithmetic on it, so the validator can allow them all.
"""

from __future__ import annotations

from typing import Any

from cricrag.entities import canon
from cricrag.momentum import describe, match_shifts
from cricrag.text import strip_html

BALLS = """select inning, over_number, over_label, runs_total, is_wicket, is_four, is_six, is_wide, is_noball, bowler, batter,
                  batting_team, commentary from balls where match_id = ? and inning <= 2 order by inning, sequence"""


def match_balls(con, match_id: int, drops: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Balls in the momentum detector's format, plus innings [{team, runs}]."""
    balls, innings = [], {}
    for inn, over, label, runs, wkt, four, six, wd, nb, bowler, batter, team, text in con.execute(
        BALLS, [match_id]
    ).fetchall():
        event = (
            "wd" if wd else "nb" if nb else "W" if wkt else "6" if six else "4" if four else str(runs or 0)
        )
        ball = {
            "inning": inn,
            "over": over,  # bursts and squeezes group balls by over; without it they never fire
            "label": label,
            "runs": runs or 0,
            "wicket": bool(wkt),
            "boundary": bool(four or six),
            "event": event,
            "bowler": bowler,
            "title": f"{bowler} to {batter}",
            "text": strip_html(text),
        }
        if drops is not None:  # audited drops replace the regex (fielding audit: bare "dropped" ~25% precise)
            fielder = drops.get((match_id, inn, str(label)))
            ball["chance"], ball["chance_by"] = fielder is not None, fielder or None
        balls.append(ball)
        innings.setdefault(inn, {"team": canon(team), "runs": 0})["runs"] += runs or 0
    return balls, [innings[i] for i in sorted(innings)]


def _rows(con, sql: str, mid: int) -> list[tuple]:
    try:
        return con.execute(sql, [mid]).fetchall()
    except Exception:
        return []


def facts(con, mid: int) -> list[str]:
    m = next(
        iter(
            con.execute(
                "select season, title, start_date, venue, team1, team2, status_text from matches where match_id=?",
                [mid],
            ).fetchall()
        ),
        None,
    )
    if not m:
        return []
    out = [f"IPL {m[0]} {m[1]}: {canon(m[4])} v {canon(m[5])} at {m[3]}, {m[2]}. Result: {m[6]}."]
    for toss, decision, method in _rows(
        con, "select toss_winner, toss_decision, method from cs_match_info where match_id=?", mid
    ):
        out.append(f"Toss: {toss} chose to {decision}." + (f" Method: {method}." if method else ""))
    for inn, team, runs, wkts, balls in _rows(
        con,
        "select inning, team, runs, wickets, balls from cs_innings_card "
        "where match_id=? and inning<=2 order by inning",
        mid,
    ):
        bat = _rows(
            con,
            f"select player, runs, balls from cs_batting_card where match_id=? and inning={inn} "
            "order by runs desc limit 3",
            mid,
        )
        bowl = _rows(
            con,
            f"select player, wickets, conceded, balls from cs_bowling_card where match_id=? and inning={inn} "
            "order by wickets desc, conceded limit 2",
            mid,
        )
        out.append(
            f"Innings {inn}: {team} {runs}/{wkts} in {balls // 6}.{balls % 6} overs. "
            f"Top batters: {', '.join(f'{p} {r} ({b})' for p, r, b in bat)}. "
            f"Top bowlers: {', '.join(f'{p} {w}/{c} ({b // 6}.{b % 6})' for p, w, c, b in bowl)}."
        )
    pom = [r[0] for r in _rows(con, "select player from cs_awards where match_id=?", mid)]
    if pom:
        out.append(f"Player of the match: {', '.join(pom)}.")
    return out


def _overs(event: dict[str, Any], cap: int = 2) -> list[int]:
    """Over numbers (1-based) behind a shift: evidence balls first, then the window's start."""
    labels = [e["label"] for e in event.get("evidence", [])] + [event["from"], event["to"]]
    overs = list(
        dict.fromkeys(int(float(str(lbl))) + 1 for lbl in labels if str(lbl).replace(".", "").isdigit())
    )
    return overs[:cap]


def match_story(con, mid: int, drops: dict | None = None, limit: int = 6) -> dict[str, Any]:
    balls, innings = match_balls(con, mid, drops)
    won = _rows(con, "select winner from matches where match_id=?", mid)
    winner = canon(won[0][0]) if won and won[0][0] else None  # the decisive shift favours the winner
    shifts = match_shifts(balls, innings, limit=limit, winner=winner) if len(innings) == 2 else []
    return {
        "match_id": mid,
        "facts": facts(con, mid),
        "shifts": [
            {
                "line": describe(e),
                "inning": e["inning"],
                "type": e["type"],
                "favours": e["favours"],
                "decisive": bool(e.get("decisive")),
                "over_ids": [f"{mid}:{e['inning']}:{o}" for o in _overs(e)],
            }
            for e in shifts
        ],
    }


def load_drops(path) -> dict[tuple[int, int, str], str]:
    """(match, inning, over_label) -> fielder ('' if unnamed), audited drops at medium confidence or better."""
    import csv
    from pathlib import Path

    if not path or not Path(path).is_file():
        return {}
    with open(path, encoding="utf-8", newline="") as fh:
        return {
            (int(r["match_id"]), int(r["inning"]), r["over_label"]): r["fielder"] or ""
            for r in csv.DictReader(fh)
            if r["label"] == "drop" and r["confidence"] not in ("low", "very_low")
        }
