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
from decimal import Decimal
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
        "m.season, year(m.start_date) AS year, bs.bowl_kind, bs.bowl_arm, bh.bat_hand,",
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
  bowl_kind ('pace'|'spin'), bowl_arm ('left'|'right'), bat_hand ('left'|'right')
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
- Bowler types are two classes only: pace (fast, quick, medium, seam: every speed) = bowl_kind 'pace';
  spin (slow, spinners of any kind) = bowl_kind 'spin'. Add bowl_arm for left-arm/right-arm:
  left-arm fast bowlers = bowl_kind 'pace' AND bowl_arm 'left'; left-handers = bat_hand 'left'.
- A batter against a bowler type (spin, left-arm, legbreak...) or a bowler against left/right-handers is ball-level:
  sum runs_bat and non-wide balls in balls with the style filter; dismissals = rows with bowler_wicket AND
  player_out = that batter (run outs are not the bowler type's). Never use whole-innings rows from batting for these.
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
    if re.search(r"\bis_legal\b", s, re.I) and re.search(
        r"\bbatter\b\s*(=|in\b|ilike\b)|group by\s+(\w+\.)?batter\b", s, re.I
    ):  # a no-ball is a ball faced (Kohli v left-arm pace came out 1433 off 1019, not 1437 off 1023)
        return "balls faced exclude wides only: count NOT is_wide, never is_legal, for a batter"
    if re.search(r"count\s*\(\s*distinct\b(?:(?!\bmatch_id\b)[^)])*\bsequence\b", s, re.I):
        return "sequence restarts in every match: count rows, or DISTINCT (match_id, inning, sequence)"
    if STYLE_FILTER.search(s) and re.search(r"\bplayer_out\b", s) and not re.search(r"\bbowler_wicket\b", s):
        return "a batter's dismissals against a bowler type are bowler_wicket AND player_out = the batter (run outs are not the bowler's)"
    if "bowl_style" in s.lower():  # two classes only: pace and spin (with the arm), never a finer style
        return "filter bowler types with bowl_kind ('pace' or 'spin') and bowl_arm only"
    low = s.lower()
    miss = [p for p in frame.players if p.split()[-1].lower() not in low]
    miss += [t for t in frame.teams if t.lower() not in low]
    miss += [str(y) for y in frame.years if str(y) not in s]
    miss += [f"minimum {n}" for n in MIN_N.findall(frame.question) if not re.search(rf"\b{n}\b", s)]
    return f"the query must filter on {', '.join(miss)}" if miss else None


RATE = re.compile(r"\b(strike[ -]?rates?|averages?|averaged|economy|economies)\b", re.I)
RANKED = re.compile(r"\b(best|worst|highest|lowest|top|most|least|fastest|slowest|better|worse)\b", re.I)
RATE_COL = re.compile(r"rate|avg|average|economy|\bsr\b|per_", re.I)
BALLS_COL = re.compile(r"ball", re.I)
NOT_COUNT = re.compile(
    r"^(year|season|inning|over|over_number|ball_in_over|position|rank|id)$|_id$", re.I
)  # numeric, not a sample
MIN_BALLS = 60  # the PROMPT's rule; code holds it, since the model can skip a prompt rule
ASKED_BALLS = re.compile(r"\b(?:minimum(?: of)?|at least|min\.?)\s+(\d+)\s+balls\b", re.I)


def qualifier(question: str) -> int | None:
    """Minimum balls for a rate ranking: the question's own, else MIN_BALLS. None when not a rate ranking."""
    if not (RATE.search(question) and RANKED.search(question)):
        return None
    return max((int(n) for n in ASKED_BALLS.findall(question)), default=MIN_BALLS)


def sample_problem(question: str, cols: list[str], rows: list[tuple], sql: str = "") -> str | None:
    """A rate answer must carry what it is built on, and a rate ranking must be qualified.

    'Best strike rate v left-arm spin in 2022' once returned Obed McCoy, 7 off 2 balls (350), and an average came
    back as a bare 39.04 with no runs, balls or dismissals to say how big the sample was.
    """
    if (
        STYLE_FILTER.search(sql)
        and re.search(r"\bbatter\s*(=|ilike\b)", sql, re.I)
        and not any(re.search(r"dismiss|outs?$|wicket", c, re.I) for c in cols)
    ):  # a batter against a bowler type: runs, balls and dismissals are the answer
        return (
            "the query must also return the batter's dismissals (bowler_wicket AND player_out = the batter)"
        )
    if not (m := RATE.search(question)):
        return None
    counts = [
        i
        for i, c in enumerate(cols)
        if not RATE_COL.search(c)
        and not NOT_COUNT.search(c)
        and all(isinstance(r[i], (int, float, Decimal)) or r[i] is None for r in rows)
    ]
    balls = [i for i in counts if BALLS_COL.search(cols[i])]
    need = qualifier(question)
    rank = f"rank only players with at least {need} balls (HAVING balls >= {need})" if need else ""
    if not counts or (not balls and not m.group(1).lower().startswith("aver")):  # SR and economy are per ball
        what = "runs, balls, and dismissals or wickets" if not counts else "balls"
        return (
            "the query must return the "
            + what
            + " the rate is built on next to it"
            + (f", and {rank}" if rank else "")
        )
    if balls and need and any((r[balls[0]] or 0) < need for r in rows):
        return f"the query must {rank}"
    return None


STYLE_FILTER = re.compile(r"\b(bowl_kind|bowl_arm|bat_hand)\s*(=|ILIKE|IN)\s*('[^']*'|\([^)]*\))", re.I)


def notes(question: str, sql: str, cols: list[str]) -> str:
    """What the writer must say beside a rate: its sample, and the bowler type exactly as the query filtered it
    ('left-arm fast' was answered over all left-arm pace, fast to medium, with nothing in the reply to say so)."""
    out = []
    if (need := qualifier(question)) and any(BALLS_COL.search(c) for c in cols):
        out.append(f"ranked among players with at least {need} balls")
    if RATE.search(question):
        out.append("give the runs, balls and dismissals or wickets beside the rate")
    if styles := sorted({" ".join(m.group(1, 2, 3)) for m in STYLE_FILTER.finditer(sql)}):
        out.append("bowler/batter type as filtered: " + ", ".join(styles) + " (describe it in these words)")
    return "; ".join(out)


def footnote(question: str, res: dict[str, Any]) -> str:
    """'Sample: 976 runs, 572 balls, 25 dismissals. Bowlers: left-arm pace.' Built from the result
    and the SQL, not by the writer, so a rate never reaches the reader without what it rests on."""
    if not (RATE.search(question) or STYLE_FILTER.search(res.get("sql", ""))) or not res.get("rows"):
        return ""
    cols, top = res["cols"], res["rows"][0]
    counts = [
        f"{_fmt(top[i])} {c.replace('_', ' ')}"
        for i, c in enumerate(cols)
        if not RATE_COL.search(c) and not NOT_COUNT.search(c) and isinstance(top[i], (int, float, Decimal))
    ]
    out = [("Top row: " if len(res["rows"]) > 1 else "Sample: ") + ", ".join(counts) + "."] if counts else []
    f = {m.group(1).lower(): m.group(3).strip("'").lower() for m in STYLE_FILTER.finditer(res.get("sql", ""))}
    if kind := f.get("bowl_kind"):
        out.append(f"Bowlers: {f['bowl_arm'] + '-arm ' if f.get('bowl_arm') else ''}{kind}.")
    if (need := qualifier(question)) and any(BALLS_COL.search(c) for c in cols):
        out.append(f"Qualification: at least {need} balls.")
    return " ".join(out)


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
            + (f"\nFORM (what is asked): {json.dumps(hint)}" if hint else "")
            + (
                f"\nSAMPLE: return runs and balls with the rate; rank only players with at least {need} balls."
                if (need := qualifier(frame.question))
                else ""
            ),
        },
    ]
    for _ in range(3):  # each guard can cost a retry; a third attempt only runs after two rejections
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
                if not rows or (len(rows) == 1 and all(v in (0, None) for v in rows[0])):
                    problem = "the query matched nothing; check filters and spellings (names/venues: use ILIKE '%word%')"
                elif not (problem := sample_problem(frame.question, cols, rows, sql)):
                    out_ = {"kind": "rows", "sql": sql, "cols": cols, "rows": rows}
                    if note := notes(frame.question, sql, cols):
                        out_["note"] = note
                    return out_
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
    note = f" ({res['note']}; say so)" if res.get("note") else ""
    return f"QUERY RESULT computed for this question{note}, {len(res['rows'])} row(s) ({head}): {body}"
