"""The event layer: every notable thing in every IPL match as one SQL row, linked to its over.

  batting   wicket (batter, bowler, fielder, how, score), fifty, hundred, partnership (50+)
  bowling   haul (4+ wickets), hattrick, maiden, big_over (20+ runs)
  fielding  drop, misfield, overthrow (audited ipl_fielding events)
  match     finish (result, close flag), shift (cricbot's momentum detector)

Built once into data/events.duckdb from the archive (read-only). Moment questions become lookups,
so the over is exact, not searched for; commentary is then fetched by chunk_id for colour.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb

from cricrag.archive import STAGE_SQL
from cricrag.frame import Frame

COLS = [
    "type",
    "match_id",
    "season",
    "year",
    "date",
    "stage",
    "title",
    "inning",
    "over",
    "label",
    "batting_team",
    "bowling_team",
    "phase",
    "player",
    "player2",
    "fielder",
    "detail",
    "value",
    "value2",
    "score",
    "side",
    "note",
    "close",
    "chunk_id",
]
_FIX = "replace({}, 'KXIP', 'PBKS')"  # matches stores KXIP where balls stores PBKS

BASE = f"""
CREATE OR REPLACE TEMP VIEW b AS
SELECT x.*, m.season, year(m.start_date) AS yr, CAST(m.start_date AS VARCHAR) AS dt, m.title, m.status_text,
  {STAGE_SQL.replace("title", "m.title")} AS stage, {_FIX.format("x.batting_team")} AS bat,
  CASE WHEN {_FIX.format("m.team1")} = {_FIX.format("x.batting_team")} THEN {_FIX.format("m.team2")}
       ELSE {_FIX.format("m.team1")} END AS bowl,
  CASE WHEN x.over_number <= 6 THEN 'powerplay' WHEN x.over_number <= 15 THEN 'middle' ELSE 'death' END AS phase,
  sum(x.runs_total) OVER w AS cr, sum(x.is_wicket::INT) OVER w AS cw,
  sum(x.runs_bat) OVER (PARTITION BY x.match_id, x.inning, x.batter ORDER BY x.sequence) AS br,
  sum((NOT x.is_wide)::INT) OVER (PARTITION BY x.match_id, x.inning, x.batter ORDER BY x.sequence) AS bb,
  x.runs_bat + x.wide_runs + x.noball_runs AS charged
FROM cb.balls x JOIN cb.matches m USING (match_id)
WHERE NOT coalesce(m.is_cancelled, false) AND x.inning <= 2
WINDOW w AS (PARTITION BY x.match_id, x.inning ORDER BY x.sequence)
"""
_C = [
    "match_id",
    "season",
    "yr",
    "dt",
    "stage",
    "title",
    "inning",
    "over_number",
    "over_label",
    "bat",
    "bowl",
    "phase",
]
C = ", ".join(_C)
TAIL = """cr || '/' || cw, NULL, NULL, false, match_id || ':' || inning || ':' || over_number"""
CB = ", ".join("b." + c for c in _C)
TAILB = """b.cr || '/' || b.cw, NULL, NULL, false, b.match_id || ':' || b.inning || ':' || b.over_number"""
OVERS = """
CREATE OR REPLACE TEMP VIEW o AS
SELECT match_id, inning, over_number, any_value(season) AS season, any_value(yr) AS yr, any_value(dt) AS dt,
  any_value(stage) AS stage, any_value(title) AS title, max(over_label) AS over_label, any_value(bat) AS bat,
  any_value(bowl) AS bowl, any_value(phase) AS phase, string_agg(DISTINCT bowler, ', ') AS bowler,
  sum(runs_total) AS runs, sum(charged) AS charged, sum(is_legal::INT) AS legal, sum(bowler_wicket::INT) AS bw,
  max_by(cr, sequence) AS cr, max_by(cw, sequence) AS cw
FROM b GROUP BY match_id, inning, over_number
"""
SQL = {
    "wicket": f"""SELECT 'wicket', {CB}, b.player_out, CASE WHEN b.bowler_wicket THEN b.bowler END, f.fielder,
        lower(b.dismissal_type), CASE WHEN b.batter = b.player_out THEN br END, CASE WHEN b.batter = b.player_out THEN bb END, {TAILB}
        FROM b LEFT JOIN cb.cs_fielding f ON f.match_id = b.match_id AND f.inning = b.inning AND f.player_out_id = b.player_out_id
        WHERE b.is_wicket QUALIFY row_number() OVER (PARTITION BY b.match_id, b.inning, b.sequence) = 1""",
    "milestone": f"""SELECT CASE WHEN br >= 100 THEN 'hundred' ELSE 'fifty' END, {C}, batter, bowler, NULL,
        CASE WHEN br >= 100 THEN '100' ELSE '50' END, bb, fin, {TAIL}
        FROM (SELECT *, lag(br, 1, 0) OVER p AS prev, max(br) OVER (PARTITION BY match_id, inning, batter) AS fin
              FROM b WINDOW p AS (PARTITION BY match_id, inning, batter ORDER BY sequence))
        WHERE (prev < 50 AND br >= 50) OR (prev < 100 AND br >= 100)""",
    "haul": f"""SELECT 'haul', {C}, bowler, NULL, NULL, w || '/' || con, w, con, {TAIL}
        FROM (SELECT *, sum(bowler_wicket::INT) OVER p AS k, sum(bowler_wicket::INT) OVER q AS w, sum(charged) OVER q AS con
              FROM b WINDOW p AS (PARTITION BY match_id, inning, bowler ORDER BY sequence), q AS (PARTITION BY match_id, inning, bowler))
        WHERE bowler_wicket AND k = 4 AND w >= 4""",
    "hattrick": f"""SELECT 'hattrick', {C}, bowler, player_out, NULL, NULL, NULL, NULL, {TAIL}
        FROM (SELECT *, lag(bowler_wicket, 1) OVER p AS w1, lag(bowler_wicket, 2) OVER p AS w2
              FROM b WHERE NOT is_wide AND NOT is_noball WINDOW p AS (PARTITION BY match_id, inning, bowler ORDER BY sequence))
        WHERE bowler_wicket AND w1 AND w2""",
    "maiden": f"""SELECT 'maiden', {C}, bowler, NULL, NULL, NULL, bw, NULL, {TAIL} FROM o WHERE charged = 0 AND legal >= 6""",
    "big_over": f"""SELECT 'big_over', {C}, bowler, NULL, NULL, NULL, runs, bw, {TAIL} FROM o WHERE runs >= 20""",
    "partnership": """SELECT 'partnership', match_id, any_value(season), any_value(yr), any_value(dt), any_value(stage),
        any_value(title), inning, max(over_number), max(over_label), any_value(bat), any_value(bowl), any_value(phase),
        min(batter), max(batter), NULL, CAST(wb + 1 AS VARCHAR), sum(runs_total), sum((NOT is_wide)::INT),
        max_by(cr, sequence) || '/' || max_by(cw, sequence), NULL, NULL, false, match_id || ':' || inning || ':' || max(over_number)
        FROM (SELECT *, cw - is_wicket::INT AS wb FROM b) GROUP BY match_id, inning, wb HAVING sum(runs_total) >= 50""",
    "finish": f"""SELECT 'finish', {C}, NULL, NULL, NULL, status_text, NULL, NULL, cr || '/' || cw, NULL, NULL,
        regexp_matches(lower(status_text), 'won by ([1-9]|10) runs?|with [1-6] balls? remaining|tie|super over'),
        match_id || ':' || inning || ':' || over_number
        FROM b WHERE inning = 2 QUALIFY row_number() OVER (PARTITION BY match_id ORDER BY sequence DESC) = 1""",
}
FIELDING = f"""SELECT f.label, {", ".join("b." + c for c in _C)}, f.fielder, f.batter, f.fielder,
    f.confidence, try_cast(f.runs_after_drop AS DOUBLE), b.br,
    b.cr || '/' || b.cw, NULL, b.bowler, false, b.match_id || ':' || b.inning || ':' || b.over_number
    FROM read_csv('{{path}}', all_varchar = true, header = true) f
    JOIN b ON b.match_id = f.match_id::BIGINT AND b.inning = f.inning::INT AND b.over_label = f.over_label
    WHERE f.label IN ('drop', 'misfield', 'overthrow') AND f.confidence NOT IN ('low', 'very_low')
    QUALIFY row_number() OVER (PARTITION BY b.match_id, b.inning, f.over_label, f.label
                               ORDER BY coalesce(b.is_legal, true) DESC, b.sequence) = 1  -- the legal ball, not a wide"""


def build(archive: Path, out: Path, fielding_csv: Path | None = None, log=print) -> int:
    """Rebuild data/events.duckdb. Returns the number of events."""
    from cricrag.momentum import describe, match_shifts
    from cricrag.story import _overs, load_drops, match_balls

    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    con = duckdb.connect(str(out))
    con.execute(f"ATTACH '{archive}' AS cb (READ_ONLY)")
    con.execute(BASE)
    con.execute(OVERS)
    con.execute(f"CREATE TABLE events ({', '.join(f'{c} {t}' for c, t in zip(COLS, _TYPES, strict=True))})")
    for name, sql in SQL.items():
        con.execute(f"INSERT INTO events {sql}")
        log(f"  {name}: {con.execute('select count(*) from events').fetchall()[0][0]} events so far")
    if fielding_csv and Path(fielding_csv).is_file():
        con.execute("INSERT INTO events " + FIELDING.replace("{path}", str(fielding_csv).replace("'", "''")))
        log(f"  fielding: {con.execute('select count(*) from events').fetchall()[0][0]} events so far")
    drops = load_drops(fielding_csv) or None
    meta = {
        r[0]: r
        for r in con.sql("SELECT match_id, season, yr, dt, stage, title FROM b GROUP BY ALL").fetchall()
    }
    from cricrag.entities import canon

    winners = {
        m: canon(w)
        for m, w in con.sql("SELECT match_id, winner FROM cb.matches WHERE winner <> ''").fetchall()
    }
    rows = []
    for mid in meta:  # cricbot's momentum detector, per match, on the archive's balls
        con.execute("USE cb")
        balls, innings = match_balls(con, mid, drops)
        con.execute(f"USE {out.stem}")
        if len(innings) != 2:
            continue
        for e in match_shifts(balls, innings, winner=winners.get(mid)):
            over = (_overs(e, 1) or [1])[0]
            sides = [i["team"] for i in innings]
            bat = sides[e["inning"] - 1]
            rows.append(
                (
                    "shift",
                    *meta[mid],
                    e["inning"],
                    over,
                    str(e["from"]),
                    bat,
                    sides[2 - e["inning"]],
                    "powerplay" if over <= 6 else "middle" if over <= 15 else "death",
                    None,
                    None,
                    None,
                    e["type"],
                    e["magnitude"],
                    None,
                    e["score_after"],
                    e["favours"],
                    describe(e),
                    bool(e.get("decisive")),
                    f"{mid}:{e['inning']}:{over}",
                )
            )
    if rows:
        con.executemany(f"INSERT INTO events VALUES ({', '.join('?' * len(COLS))})", rows)
    n = con.execute("select count(*) from events").fetchall()[0][0]
    con.close()
    log(f"  shifts: {len(rows)}; total {n} events -> {out}")
    return n


_TYPES = [
    "VARCHAR",
    "BIGINT",
    "VARCHAR",
    "INT",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "INT",
    "INT",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "DOUBLE",
    "DOUBLE",
    "VARCHAR",
    "VARCHAR",
    "VARCHAR",
    "BOOLEAN",
    "VARCHAR",
]

# question words -> event types (order matters: the first match wins for ambiguous words)
TYPE_CUES: list[tuple[str, re.Pattern]] = [
    ("hattrick", re.compile(r"\bhat[- ]?tricks?\b", re.I)),
    (
        "drop",
        re.compile(
            r"\b(drop(s|ped)?( a)? (catch|chance)(es)?|dropped|costl\w* drops?|drops? in ipl|put (him |it )?down|grassed|spill\w*|let ?off|reprieve|missed (a )?(catch|chance))\b",
            re.I,
        ),
    ),
    ("misfield", re.compile(r"\b(misfield\w*|fumbl\w*)\b", re.I)),
    ("overthrow", re.compile(r"\boverthrows?\b", re.I)),
    ("hundred", re.compile(r"\b(hundreds?|centur(y|ies)|tons?|100s?)\b", re.I)),
    ("fifty", re.compile(r"\b(fift(y|ies)|half[- ]centur(y|ies)|50s?)\b", re.I)),
    ("haul", re.compile(r"\b((4|5|four|five)[- ]?(wicket|fer|for)\w*|hauls?|(four|five) wickets)\b", re.I)),
    ("maiden", re.compile(r"\bmaidens?\b", re.I)),
    (
        "big_over",
        re.compile(
            r"\b(big|expensive|costly|biggest) overs?\b|\b(2\d|3\d) runs (in|off) (an|one|the|a single) over\b",
            re.I,
        ),
    ),
    ("partnership", re.compile(r"\b(partnerships?|stands?)\b", re.I)),
    (
        "finish",
        re.compile(
            r"\b(finish(es)?|last[- ]ball|super overs?|thrillers?|close (games?|matches|finish\w*)|last[- ]over win)\b",
            re.I,
        ),
    ),
    # a collapse is one kind of shift (wickets in a cluster); ranked by the detector's swing
    ("collapse", re.compile(r"\b(collaps\w*|lost wickets in a heap|wickets? in a heap|procession)\b", re.I)),
    ("shift", re.compile(r"\b(turn(ed|ing)?( point)?|momentum|collapse\w*|comeback|swing)\b", re.I)),
    (
        "wicket",
        re.compile(
            r"\b(out|dismiss\w*|wickets? of|got .{1,30} out|(was|got|clean) bowled|bowled (him|out)|lbw|stumped|run out|caught|how did .{1,30} fall)\b",
            re.I,
        ),
    ),
]
DESC = re.compile(r"\b(highest|biggest|best|largest|most expensive|costl\w*|longest)\b", re.I)
MOMENT_TYPES = {
    "drop",
    "collapse",
    "shift",
    "big_over",
    "misfield",
}  # single moments cricbot's tables don't rank
ASC = re.compile(r"\b(fastest|quickest|earliest|cheapest)\b", re.I)
CLOSE = re.compile(r"\b(close|thrill\w*|last[- ]ball|tight|nail-?biter|super over)\b", re.I)
BOWL_ROLE = re.compile(
    r"\b(bowl\w*|spinner|pacer|seamer|economy|maiden|hat[- ]?trick|haul|took .* wickets)\b", re.I
)
BAT_ROLE = re.compile(
    r"\b(bat\w*|chas\w*|innings|scored|opener|fift\w*|hundred|centur\w*|partnership)\b", re.I
)


def event_types(question: str) -> list[str]:
    return [t for t, rx in TYPE_CUES if rx.search(question)]


def team_role(question: str) -> str | None:
    bowl, bat = bool(BOWL_ROLE.search(question)), bool(BAT_ROLE.search(question))
    return "bowling" if bowl and not bat else "batting" if bat and not bowl else None


def query(con, frame: Frame, types: list[str], limit: int = 8) -> tuple[list[dict[str, Any]], int]:
    """Events matching the frame (code-extracted filters only). Returns (rows, total matching)."""
    plain = [t for t in types if t != "collapse"]
    where, params = [f"type IN ({','.join('?' * len(plain))})" if plain else "false"], list(plain)
    if "collapse" in types:
        where[0] = f"({where[0]} OR (type = 'shift' AND detail = 'wicket_cluster'))"
    for col, vals in (("season", frame.seasons),):
        if vals:
            where.append(f"{col} IN ({','.join('?' * len(vals))})")
            params += vals
    for key in ("stage", "phase", "inning", "over"):
        if (v := getattr(frame, key)) is not None:
            where.append(f"{key} = ?")
            params.append(v)
    role = team_role(frame.question)
    if len(frame.teams) >= 2:  # a fixture: either side batting
        t = frame.teams[:2]
        where.append("batting_team IN (?, ?) AND bowling_team IN (?, ?)")
        params += t + t
    for team in frame.teams[:1] if len(frame.teams) == 1 else []:
        col = {"bowling": ["bowling_team"], "batting": ["batting_team"]}.get(
            role, ["batting_team", "bowling_team"]
        )
        where.append("(" + " OR ".join(f"{c} = ?" for c in col) + ")")
        params += [team] * len(col)
    for p in frame.players:
        where.append("(player = ? OR player2 = ? OR fielder ILIKE ?)")
        params += [p, p, f"%{p.split()[-1]}"]
    if CLOSE.search(frame.question) and "finish" in types:
        where.append("close")
    order = (
        "value DESC NULLS LAST"
        if DESC.search(frame.question)
        else "value ASC NULLS LAST"
        if ASC.search(frame.question)
        else "date DESC, inning, over"
    )
    w = " AND ".join(where)
    total = con.execute(f"SELECT count(*) FROM events WHERE {w}", params).fetchall()[0][0]
    cur = con.execute(f"SELECT * FROM events WHERE {w} ORDER BY {order} LIMIT {int(limit)}", params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()], int(total)


def describe_row(r: dict[str, Any], short: bool = False) -> str:
    """One factual line per event, numbers straight from the row. short: within one match (the story)."""
    at = (
        f"{r['batting_team']} innings, {r['label']}"
        if short
        else f"IPL {r['season']} {r['title']} ({r['batting_team']} v {r['bowling_team']}, {r['date']}), innings {r['inning']}, {r['label']}"
    )
    t = r["type"]
    if t == "wicket":
        how = r["detail"] + (
            f" by {r['fielder']}" if r["fielder"] and r["detail"] in ("caught", "stumped", "run out") else ""
        )
        score = f" for {int(r['value'])} ({int(r['value2'])} balls)" if r["value"] is not None else ""
        return f"{at}: {r['player']} out {how}{' off ' + r['player2'] if r['player2'] else ''}{score}; score {r['score']}."
    if t in ("fifty", "hundred"):
        return f"{at}: {r['player']} reached {r['detail']} off {int(r['value'])} balls (finished on {int(r['value2'])}); score {r['score']}."
    if t == "haul":
        return f"{at}: {r['player']} took his 4th wicket, finishing with {r['detail']}."
    if t == "hattrick":
        return f"{at}: {r['player']} completed a hat-trick (third wicket: {r['player2']})."
    if t == "maiden":
        return (
            f"{at}: maiden over by {r['player']} ({int(r['value'] or 0)} wickets in it); score {r['score']}."
        )
    if t == "big_over":
        return f"{at}: {int(r['value'])} runs off {r['player']} in over {r['over']}; score {r['score']}."
    if t == "partnership":
        return f"{at}: {r['player']} and {r['player2']} added {int(r['value'])} off {int(r['value2'])} balls for wicket {r['detail']}; score {r['score']}."
    if t in ("drop", "misfield", "overthrow"):
        extra = (
            f"; {r['player2']} added {int(r['value'])} more runs"
            if t == "drop" and r["value"] is not None
            else ""
        )
        off = f" off {r['note']}'s bowling" if t == "drop" and r["note"] else ""
        on = f" on {int(r['value2'])}" if t == "drop" and r["value2"] is not None else ""
        return f"{at}: {t} by {r['player'] or 'an unnamed fielder'}{off} ({r['player2']} batting{on}, confidence {r['detail']}){extra}."
    if t == "finish":
        return f"{at}: {r['detail']}; final score {r['score']}{' (close finish)' if r['close'] else ''}."
    return f"{at}: {(r['note'] or '').split(' | ')[0] if short else r['note']}"


FIELD = {
    "catches": "type = 'wicket' AND detail = 'caught'",
    "stumpings": "type = 'wicket' AND detail = 'stumped'",
    "run outs": "type = 'wicket' AND detail = 'run out'",
    "drops": "type = 'drop'",
    "misfields": "type = 'misfield'",
}
FIELD_CUE = re.compile(r"\b(catch(es)?|stumpings?|run[- ]?outs?|drops?|dropped catches|misfields?)\b", re.I)


def fielding_counts(con, frame: Frame, limit: int = 10) -> tuple[str, list[tuple]] | None:
    """Catches, stumpings, run outs (scorecard fielders) and drops/misfields (audited) counted per fielder."""
    m = FIELD_CUE.search(frame.question)
    if not m:
        return None
    word = m.group(1).lower()
    kind = (
        "stumpings"
        if word.startswith("stump")
        else "run outs"
        if word.startswith("run")
        else "drops"
        if word.startswith("drop")
        else "misfields"
        if word.startswith("misf")
        else "catches"
    )
    who = "fielder" if kind in ("catches", "stumpings", "run outs") else "player"
    where, params = [FIELD[kind], f"{who} IS NOT NULL"], []
    if frame.seasons:
        where.append(f"season IN ({','.join('?' * len(frame.seasons))})")
        params += frame.seasons
    for t in frame.teams[:1]:
        where.append("bowling_team = ?")  # the fielding side
        params.append(t)
    for p in frame.players:
        where.append(f"{who} ILIKE ?")
        params.append(f"%{p.split()[-1]}")
    sql = f"SELECT {who}, count(*) n FROM events WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY n DESC, 1 LIMIT {limit}"
    return kind, con.execute(sql, params).fetchall()


LEADER: list[tuple[str, re.Pattern, str, str]] = [  # (label, cue, condition, team column)
    ("ducks", re.compile(r"\bducks?\b", re.I), "type = 'wicket' AND value = 0", "batting_team"),
    (
        "single-digit dismissals",
        re.compile(r"\bsingle[- ]digit", re.I),
        "type = 'wicket' AND value < 10",
        "batting_team",
    ),
    ("maidens", dict(TYPE_CUES)["maiden"], "type = 'maiden'", "bowling_team"),
    ("hat-tricks", dict(TYPE_CUES)["hattrick"], "type = 'hattrick'", "bowling_team"),
    ("hundreds", dict(TYPE_CUES)["hundred"], "type = 'hundred'", "batting_team"),
    (
        "5-wicket hauls",
        re.compile(r"\b((5|five)[- ]?(wicket|fer)\w*|fifers?|five wickets in an innings)\b", re.I),
        "type = 'haul' AND value >= 5",
        "bowling_team",
    ),
    ("4+ wicket hauls", dict(TYPE_CUES)["haul"], "type = 'haul'", "bowling_team"),
]


def leaderboard(con, frame: Frame, limit: int = 10) -> tuple[str, list[tuple]] | None:
    """Count events per player ("most maidens", "how many ducks has X") with the frame's filters, by SQL."""
    hit = next((x for x in LEADER if x[1].search(frame.question)), None)
    if not hit:
        return None
    label, _, cond, team_col = hit
    where, params = [cond], []
    if frame.seasons:
        where.append(f"season IN ({','.join('?' * len(frame.seasons))})")
        params += frame.seasons
    for key in ("stage", "phase"):
        if (v := getattr(frame, key)) is not None:
            where.append(f"{key} = ?")
            params.append(v)
    for team in frame.teams[:1]:
        where.append(f"{team_col} = ?")
        params.append(team)
    if frame.players:
        where.append(f"player IN ({','.join('?' * len(frame.players))})")
        params += frame.players
    sql = f"SELECT player, count(*) n FROM events WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY n DESC, player LIMIT {int(limit)}"
    return label, con.execute(sql, params).fetchall()
