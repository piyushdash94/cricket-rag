"""Intent router: the LLM fills a fixed-choice form; code turns it into an ordered list of programs.

    question -> form {answer, unit, measure, scope}  (one small LLM call, enums only, no entities)
             -> ROUTES: ordered programs to try
             -> after each program: does the result's shape fit the form? if not, try the next one

Entities and filters still come from the QuestionFrame (code). The form only says what kind of thing is
being asked for, so a wrong form costs a detour to the next program, not a wrong entity.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

ANSWERS = ["count", "rank", "value", "list", "moment", "story", "compare", "define", "predict", "other"]
UNITS = ["player", "team", "season", "match", "innings", "partnership", "venue", "delivery", "none"]
MEASURES = [
    "runs", "wickets", "sixes", "fours", "hundreds", "fifties", "ducks", "single_digit", "maidens",
    "hat_tricks", "four_wicket_hauls", "five_wicket_hauls", "dot_balls", "economy", "strike_rate",
    "average", "run_rate", "boundary_rate", "total", "chase", "aggregate", "margin", "result", "titles",
    "playoffs", "awards", "catches", "stumpings", "run_outs", "drops", "dismissals", "partnership",
    "matches", "toss", "scorecard", "fastest", "other",
]  # fmt: skip
SCOPES = ["career", "season", "seasons", "match", "phase", "fixture", "venue", "team"]

PROMPT = f"""Classify an IPL cricket question. Do not answer it. Reply with JSON only:
{{"answer": one of {ANSWERS},
 "unit": what is counted, ranked or described, one of {UNITS},
 "measure": one of {MEASURES},
 "scope": one of {SCOPES}}}
Meanings: count = a number ("how many"); rank = top/most/highest/lowest/best list; value = one fact about
named things; list = several items ("which players..."); moment = a specific event in a match ("how was
<PLAYER> out", "what partnership did..."); story = how a match unfolded or turned; compare = two or more
named things side by side; define = "best"/"greatest" with no stated measure; predict = future events.
unit is the row being ranked/counted: "which season had the most hundreds" -> season; "highest team total"
-> innings; "highest partnership" -> partnership; "how many sixes were hit in <YEAR>" -> delivery;
"how many 200-plus totals in <YEAR>" -> innings; "most runs in <YEAR>" -> player."""

LEADER_MEASURES = {"ducks", "single_digit", "maidens", "hat_tricks", "four_wicket_hauls", "five_wicket_hauls"}
FIELD_MEASURES = {"catches", "stumpings", "run_outs", "drops"}
RATE_MEASURES = {"run_rate", "boundary_rate", "dot_balls"}


@dataclass
class Intent:
    answer: str = "other"
    unit: str = "none"
    measure: str = "other"
    scope: str = "career"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def parse(text: str) -> Intent:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        raw = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        raw = {}
    pick = lambda key, allowed, default: raw.get(key) if raw.get(key) in allowed else default  # noqa: E731
    return Intent(
        pick("answer", ANSWERS, "other"),
        pick("unit", UNITS, "none"),
        pick("measure", MEASURES, "other"),
        pick("scope", SCOPES, "career"),
    )


def classify(llm, question: str, previous: str | None = None) -> Intent:
    user = f"QUESTION: {question}" + (f"\nPREVIOUS QUESTION (for follow-ups): {previous}" if previous else "")
    out = llm.chat([{"role": "system", "content": PROMPT}, {"role": "user", "content": user}], max_tokens=600)
    return parse(out["message"].get("content") or "")


def routes(it: Intent, one_match: bool, frame) -> list[str]:
    """Programs to try, in order. Named by the rule that chose them (first item of the tuple)."""
    a, u, m = it.answer, it.unit, it.measure
    if a == "predict":
        return ["refuse"]
    if a == "story":
        return ["story", "event", "search"]
    if one_match and a != "moment" and (u == "match" or m in ("margin", "result", "scorecard", "awards")):
        return ["match", "stats", "finder"]
    if a == "moment":
        return ["event", "story", "search", "finder"]
    if m == "fastest":  # balls to a milestone live on the events table
        return ["event", "stats", "finder"]
    if m in FIELD_MEASURES:
        return ["fielding", "stats", "finder"]
    if m in LEADER_MEASURES and u in ("player", "none"):
        return ["leaderboard", "stats", "finder"]
    if u == "partnership":
        return ["stats", "event", "finder"] if a in ("rank", "count") else ["event", "stats", "finder"]
    if u == "team" and (m in RATE_MEASURES or (frame.phase and m in ("runs", "wickets"))):
        return ["team_phase", "stats", "finder"]
    return ["stats", "finder"]


KEYS = {
    "player": re.compile(r"^(player|batter|bowler|fielder|keeper|name|batters|bowlers|pair)$"),
    "season": re.compile(r"^(season|year)$"),
    "team": re.compile(r"^(team|champion|runner_up|side|batting_team|bowling_team)$"),
    "venue": re.compile(r"^(ground|venue|stadium)$"),
}


ONE_TOTAL = re.compile(r"\b(total|in all|combined)\b", re.I)


def table_units(tables: list[dict]) -> set[str]:
    """Which units a cricbot result is keyed by, from its first table's columns."""
    cols = [str(c).lower() for c in (tables[0]["columns"] if tables else [])]
    return {u for u, rx in KEYS.items() if any(rx.match(c) for c in cols)}


def shape_fits(it: Intent, tables: list[dict], frame, text: str = "") -> str | None:
    """None if a cricbot result can answer the form; otherwise the named reason it can't."""
    keys = table_units(tables)
    if not keys:
        return None  # nothing to judge by: the writer and validator decide
    if it.unit == "season" and it.answer in ("rank", "list") and "player" in keys:
        return "shape_wants_seasons_got_players"
    if it.unit == "team" and it.answer == "rank" and keys == {"player"}:
        return "shape_wants_teams_got_players"
    if it.unit == "player" and it.answer in ("rank", "list") and "player" not in keys and "team" in keys:
        return "shape_wants_players_got_teams"
    whole = it.answer == "count" and it.unit in ("innings", "delivery", "match")
    broken_down = keys & {"team", "player"} and tables[0]["rows"] > 1
    if whole and broken_down and not (frame.players or frame.teams) and not ONE_TOTAL.search(text):
        return "shape_wants_one_total_got_breakdown"
    return None
