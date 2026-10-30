"""Finder: the step before "I can't answer". Finding the right data, not a feature per question.

The LLM writes ONE read-only SELECT over five documented views. Code then guards it:
  single SELECT/WITH, no file/attach/settings functions, every frame value present (coverage),
  row cap, timeout. The rows are the only evidence the writer sees, so numbers still come from SQL.
Pandit reaches this only where it would otherwise reply GAP, so verified routes are untouched.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

import duckdb

from cricrag import events as ev
from cricrag.archive import STAGE_SQL
from cricrag.config import settings
from cricrag.frame import Frame

FIX = "replace({}, 'KXIP', 'PBKS')"
_M = f"""SELECT match_id, season, year(start_date) AS year, start_date, venue, {FIX.format("team1")} AS team1,
  {FIX.format("team2")} AS team2, {FIX.format("winner")} AS winner, status_text AS result, title,
  {STAGE_SQL} AS stage, t.toss_winner, t.toss_decision
  FROM a.matches LEFT JOIN a.cs_match_info t USING (match_id) WHERE NOT coalesce(is_cancelled, false)"""
_I = f"""SELECT i.match_id, m.season, m.year, m.stage, i.inning, {FIX.format("i.team")} AS team,
  CASE WHEN m.team1 = {FIX.format("i.team")} THEN m.team2 ELSE m.team1 END AS opponent,
  i.runs, i.wickets, i.balls, i.extras, m.winner = {FIX.format("i.team")} AS won
  FROM a.ref_innings i JOIN matches m USING (match_id) WHERE i.inning <= 2 AND NOT i.is_super_over"""
VIEWS = [
    f"CREATE OR REPLACE TEMP VIEW matches AS {_M}",
    f"CREATE OR REPLACE TEMP VIEW innings AS {_I}",
    """CREATE OR REPLACE TEMP VIEW batting AS SELECT r.match_id, i.season, i.year, i.stage, r.inning, i.team,
       i.opponent, r.position, r.player, r.runs, r.balls, r.fours, r.sixes, r.is_out, r.dismissal
       FROM a.ref_batting r JOIN innings i USING (match_id, inning)""",
    """CREATE OR REPLACE TEMP VIEW bowling AS SELECT r.match_id, i.season, i.year, i.stage, r.inning,
       i.opponent AS team, i.team AS opponent, r.player, r.balls, r.conceded, r.wickets, r.dots, r.wides, r.noballs
       FROM a.ref_bowling r JOIN innings i USING (match_id, inning)""",
    ev.BASE.replace("cb.", "a.")
    .replace("VIEW b AS", "VIEW balls AS")
    .replace(
        "m.season,",
        "m.season, year(m.start_date) AS year, bs.bowl_kind, bs.bowl_style, bs.bowl_arm, bh.bat_hand,",
    )
    .replace(
        "JOIN a.matches m USING (match_id)",
        "JOIN a.matches m USING (match_id) LEFT JOIN a.player_styles bs ON bs.player_id = x.bowler_id"
        " LEFT JOIN a.player_styles bh ON bh.player_id = x.batter_id",
    ),
    "CREATE OR REPLACE TEMP VIEW events AS SELECT * FROM e.events",
]

CARD = """DuckDB views over IPL 2008-2026. Team codes: {codes}.
matches(match): match_id, season, year INT, start_date, venue, team1, team2, winner, result, title, stage ('league'|'playoff'|'final'), toss_winner, toss_decision ('bat'|'field')
innings(team innings, super overs excluded): match_id, season, year, stage, inning (1 or 2), team, opponent, runs, wickets, balls, extras, won BOOL
batting(batter innings): match_id, season, year, stage, inning, team, opponent, position, player, runs, balls, fours, sixes, is_out BOOL, dismissal
bowling(bowler innings): match_id, season, year, stage, inning, team (bowling side), opponent, player, balls, conceded, wickets, dots, wides, noballs
balls(delivery): match_id, year, stage, inning, over_number (1-20), ball_in_over, sequence, bat (batting team), bowl (bowling team), phase ('powerplay' overs 1-6|'middle' 7-15|'death' 16-20), batter, non_striker, bowler, runs_total, runs_bat, extras, wide_runs, noball_runs, charged (runs charged to the bowler: no byes/leg byes), is_legal, is_wide, is_noball, is_four, is_six, is_wicket, bowler_wicket, dismissal_type, player_out,
  bowl_kind ('spin'|'pace'), bowl_style (e.g. 'slow left-arm orthodox', 'left-arm wrist-spin', 'right-arm offbreak', 'legbreak', 'right-arm fast'), bowl_arm ('left'|'right'), bat_hand ('left'|'right')
events(notable moments): type ('wicket','fifty','hundred','partnership','haul','hattrick','maiden','big_over','drop','misfield','overthrow','finish','shift'), match_id, season, year, title, stage, inning, over, label, batting_team, bowling_team, phase, player, player2, fielder, detail, value, value2, side, note, close
  drop: player = fielder (NULL if unnamed), player2 = batter, note = bowler, value2 = batter's score then, value = runs he added after
  shift: detail = 'wicket_cluster'|'scoring_burst'|'squeeze'|'chase_swing'|'missed_chance', value = swing in runs, side = team it favoured, close = decisive shift of the match"""

PROMPT = """Write ONE DuckDB SELECT that answers the question from these views only.
{card}
Rules (data conventions):
- Filter years with the INTEGER column year. Use the exact names/codes given under FRAME.
- A batter "score"/"innings" is one row of batting; career totals sum all rows. batting lists only players who batted;
  a player "played" a match if named as batter, non_striker or bowler in balls.
- Lowest team totals count completed innings only (wickets = 10 OR balls >= 120); rain-shortened ones are not records.
- A batter's first ball faced in an innings: the first non-wide ball for that batter in (match_id, inning) by sequence.
- Fastest fifty/hundred = fewest balls to REACH it: running sum of runs_bat and non-wide balls per
  (match_id, inning, batter) in balls; the batting row's balls is the whole innings, not balls to the milestone.
  Balls faced exclude wides only (a no-ball counts as a ball faced).
- Career milestones ("fewest balls/innings to N runs/sixes/wickets"): a running total per player over the whole
  career in time order (join matches for start_date; then inning, sequence); the answer is the balls/innings count at
  the first row where the running total reaches N. Never answer with a rate or N/rate.
- Rates (economy, average, strike rate) in a ranking need a sample: at least 60 balls unless the question sets one.
- Bowler types: spinners = bowl_kind 'spin'; left-arm spinners = bowl_kind 'spin' AND bowl_arm 'left';
  left-arm pacers = bowl_kind 'pace' AND bowl_arm 'left'; left-handers = bat_hand 'left'.
- A batter against a bowler type (spin, left-arm, legbreak...) or a bowler against left/right-handers is ball-level:
  sum runs_bat and non-wide balls in balls with the style filter; dismissals = rows where player_out is that batter
  (bowler_wicket for the bowler's share). Never use whole-innings rows from batting for these.
- Bowler wickets = bowler_wicket (run outs are not the bowler's); runs conceded = charged (byes and leg byes excluded).
- "minimum N", "at least N", "min N" in the question is a HAVING filter on the count it names.
- Strike rate = 100*runs/balls, economy = 6*conceded/balls, average = runs/dismissals.
- Join balls or events to innings on (match_id, inning) to get the batting side's result (won).
- Rankings: ORDER BY the measure, LIMIT 10, return the name plus the measure (and counts it depends on).
- If the needed data is not in these views (captaincy, auctions, umpires, injuries...), set "sql" to null.
Reply with JSON only: {{"sql": "...", "missing": null}} or {{"sql": null, "missing": "<what is missing>"}}"""

BAN = re.compile(
    r"\b(attach|detach|copy|export|import|install|load|pragma|set|create|insert|update|delete|drop|alter|call|"
    r"read_\w+|glob|getenv|system)\s*[\s(]",
    re.I,
)


MIN_N = re.compile(r"\b(?:minimum(?: of)?|at least|min\.?)\s+(\d+)\b", re.I)


def guard(sql: str, frame: Frame) -> str | None:
    """Return a problem, or None if the SQL may run."""
    s = sql.strip().rstrip(";")
    if ";" in s or not re.match(r"(?is)^\s*(with|select)\b", s):
        return "must be a single SELECT (or WITH ... SELECT) statement"
    if BAN.search(s.replace("'drop'", "")):
        return "only reads are allowed"
    low = s.lower()
    miss = [p for p in frame.players if p.split()[-1].lower() not in low]
    miss += [t for t in frame.teams if t.lower() not in low]
    miss += [str(y) for y in frame.years if str(y) not in s]
    miss += [f"minimum {n}" for n in MIN_N.findall(frame.question) if not re.search(rf"\b{n}\b", s)]
    return f"the query must filter on {', '.join(miss)}" if miss else None


def run(con, sql: str, limit: int = 15, timeout: float = 10.0) -> tuple[list[str], list[tuple]]:
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        cur = con.execute(f"SELECT * FROM ({sql.strip().rstrip(';')}) LIMIT {limit}")
        return [d[0] for d in cur.description], cur.fetchall()
    finally:
        timer.cancel()


def connect(archive=None, events_db=None):
    s = settings()
    con = duckdb.connect()
    con.execute(f"ATTACH '{archive or s.db_path}' AS a (READ_ONLY)")
    con.execute(f"ATTACH '{events_db or s.events_db}' AS e (READ_ONLY)")
    for v in VIEWS:
        con.execute(v)
    return con


def team_codes(con) -> str:
    return " ".join(r[0] for r in con.execute("SELECT DISTINCT team FROM innings ORDER BY 1").fetchall())


def _fmt(v: Any) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def find(llm, con, frame: Frame, hint: dict | None = None) -> dict[str, Any]:
    """{"kind": "rows", "sql", "cols", "rows"} | {"kind": "gap", "reason"}."""
    given = {
        k: v
        for k, v in frame.as_dict().items()
        if k in ("players", "teams", "years", "stage", "phase", "inning")
    }
    msgs = [
        {"role": "system", "content": PROMPT.format(card=CARD.format(codes=team_codes(con)))},
        {
            "role": "user",
            "content": f"QUESTION: {frame.question}\nFRAME: {json.dumps(given)}"
            + (f"\nFORM (what is asked): {json.dumps(hint)}" if hint else ""),
        },
    ]
    for _ in range(2):
        out = (llm.chat(msgs, max_tokens=1200)["message"].get("content") or "").strip()
        plan = parse(out)
        sql = plan.get("sql")
        if not sql:
            if plan.get("missing"):
                return {"kind": "gap", "reason": plan["missing"]}
            problem = "reply with the JSON object only"
            msgs += [
                {"role": "assistant", "content": out or "(empty)"},
                {"role": "user", "content": f"Fix: {problem}."},
            ]
            continue
        problem = guard(sql, frame)
        if not problem:
            try:
                cols, rows = run(con, sql)
                if rows and not (len(rows) == 1 and all(v in (0, None) for v in rows[0])):
                    return {"kind": "rows", "sql": sql, "cols": cols, "rows": rows}
                problem = "the query matched nothing; check filters and spellings (names/venues: use ILIKE '%word%')"
            except duckdb.Error as exc:
                problem = f"DuckDB error: {str(exc).splitlines()[0][:200]}"
        msgs += [
            {"role": "assistant", "content": out},
            {"role": "user", "content": f"Fix: {problem}. JSON only."},
        ]
    return {"kind": "gap", "reason": problem, "raw": out[:200]}


def parse(out: str) -> dict[str, Any]:
    """JSON object, or a bare ```sql block as a fallback."""
    if m := re.search(r"\{.*\}", out, re.S):
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    if m := re.search(r"```(?:sql)?\s*((?:with|select)\b.*?)```", out, re.S | re.I):
        return {"sql": m.group(1)}
    return {}


def evidence(res: dict[str, Any]) -> str:
    head = " | ".join(res["cols"])
    body = "; ".join(" | ".join(_fmt(v) for v in r) for r in res["rows"])
    return f"QUERY RESULT computed for this question, {len(res['rows'])} row(s) ({head}): {body}"
