"""The QuestionFrame: every entity and filter in a question, extracted by code, never by the LLM.

The LLM may choose among what the frame allows (which candidate match, which phase, how to phrase a
search) but it cannot introduce a player, team or year the question did not contain.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from cricrag.archive import STAGE_SQL, connect
from cricrag.entities import Catalog, Player, codes, link_players, link_teams, link_years

PHASES = {
    "powerplay": re.compile(r"\b(power\s*play|first six overs|overs? 1-6)\b", re.I),
    "middle": re.compile(r"\bmiddle overs?\b|\bovers? 7-15\b", re.I),
    "death": re.compile(r"\b(death|last (four|five|4|5) overs|overs? 1[6-9]-20|slog overs?)\b", re.I),
}
STAGES = {  # playoff first: "Semi-Final", "Qualifying Final", "Elimination Final" are not finals
    "playoff": re.compile(
        r"\b(qualifier|qualifying final|eliminator|elimination final|play-?offs?|semi-?finals?|knock-?outs?)\b",
        re.I,
    ),
    "final": re.compile(r"\bfinals?\b(?!\s+(over|ball|delivery|stretch|push))", re.I),  # not "final over"
}
SECOND = re.compile(r"\b(chas(e|ed|ing)|second innings|2nd innings|run chase)\b", re.I)
FIRST = re.compile(r"\b(batting first|batted first|first innings|1st innings|setting a target)\b", re.I)
LAST_OVER = re.compile(r"\b(last|final) over\b", re.I)
NTH_OVER = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th) over\b|\bover (\d{1,2})\b", re.I)


@dataclass
class Frame:
    question: str
    years: list[int] = field(default_factory=list)
    seasons: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)
    players: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    corrected: dict[str, str] = field(default_factory=dict)
    stage: str | None = None
    phase: str | None = None
    inning: int | None = None
    over: int | None = None  # 1-based over number, as stored in the archive

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], {}, "")}


def build_frame(question: str, cat: Catalog, history: list[dict[str, str]] | None = None) -> Frame:
    """Link the question; for a bare follow-up ("And in 2017?") carry entities from earlier user turns."""
    f = _link(question, cat)
    if history and not (f.players or f.teams):
        for turn in reversed(history):
            if turn.get("role") != "user":
                continue
            prev = _link(turn.get("content", ""), cat)
            f.players = f.players or prev.players
            f.teams = f.teams or prev.teams
            if not f.years:
                f.years, f.seasons = prev.years, prev.seasons
            f.stage = f.stage or prev.stage
            break
    return f


def _link(question: str, cat: Catalog) -> Frame:
    years = link_years(question, cat)
    linked = link_players(question, cat)
    f = Frame(
        question=question,
        years=years,
        seasons=[s for y in years if (s := cat.season_for(y))],
        teams=link_teams(question, cat, years),
        players=linked.players,
        ambiguous=linked.ambiguous,
        corrected=linked.corrected,
    )
    f.stage = next((k for k, rx in STAGES.items() if rx.search(question)), None)
    f.phase = next((k for k, rx in PHASES.items() if rx.search(question)), None)
    f.inning = 2 if SECOND.search(question) else 1 if FIRST.search(question) else None
    if LAST_OVER.search(question):
        f.over = 20
    elif m := NTH_OVER.search(question):
        n = int(m.group(1) or m.group(2))
        f.over = n if 1 <= n <= 20 else None
    return f


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    with connect() as con:
        seasons = dict(con.sql("select season, min(year(start_date)) from matches group by 1").fetchall())
        codes = {r[0] for r in con.sql("select distinct batting_team from balls").fetchall()}
        rows = con.sql(
            """select name, sum(n) from (
                 select batter as name, count(*) n from balls group by 1
                 union all select bowler, count(*) from balls group by 1)
               where name is not null and name <> '' group by 1"""
        ).fetchall()
    return Catalog(season_years=seasons, team_codes=codes, players=[Player(n, int(c)) for n, c in rows])


def find_matches(frame: Frame, limit: int = 12) -> list[dict[str, Any]]:
    """Matches consistent with the frame, newest first. Pure SQL; the LLM may only pick from these."""
    where, params = ["not coalesce(is_cancelled, false)"], []
    if frame.seasons:
        where.append(f"season in ({','.join('?' * len(frame.seasons))})")
        params += frame.seasons
    for team in frame.teams[:2]:
        alias = codes(team)
        marks = ",".join("?" * len(alias))
        where.append(f"(team1 in ({marks}) or team2 in ({marks}))")
        params += alias + alias
    if frame.stage:
        where.append(f"({STAGE_SQL}) = ?")
        params.append(frame.stage)
    if len(where) == 1:
        return []
    sql = f"""select match_id, season, title, start_date, team1, team2, venue, status_text, {STAGE_SQL} as stage
              from matches where {" and ".join(where)} order by start_date desc limit {int(limit)}"""
    with connect() as con:
        cols = ["match_id", "season", "title", "date", "team1", "team2", "venue", "result", "stage"]
        return [
            dict(zip(cols, r, strict=True)) | {"date": str(r[3])} for r in con.execute(sql, params).fetchall()
        ]
