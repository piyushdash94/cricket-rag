"""Answer checks. A reply that fails is corrected once, then replaced by an extractive answer.

1. Every [C#]/[S#] cited exists; a reply that used tools must cite something.
2. Numbers: only from SQL-derived chunk headers, ball labels of cited overs, stats results, or the
   question itself. Never from commentary text (it carries mid-innings scores and speeds).
3. Players named must appear in the cited chunks (as batters, bowlers, fielding-event fielders, or in the
   commentary text itself), the stats results, or the question. Text is evidence for names, not numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cricrag.entities import Catalog, link_players
from cricrag.text import numbers

CITE = re.compile(r"\[((?:[CSME]\d+)(?:\s*,\s*[CSME]\d+)*)\]")  # C: commentary, S: stats, M: match story


@dataclass
class Sources:
    chunks: dict[str, dict[str, Any]] = field(default_factory=dict)  # "C1" -> chunk row
    stats: dict[str, dict[str, Any]] = field(default_factory=dict)  # "S1"/"M1" -> stats result or story sheet
    tables: list[dict[str, Any]] = field(default_factory=list)  # cricbot's result blocks, for display only


def normalise(text: str) -> str:
    """LLMs write 【S1】, [ E1 ], [S1†L3] and narrow no-break spaces: map them to plain [S1] and spaces."""
    text = (
        text.replace("【", "[")
        .replace("】", "]")
        .replace("\u202f", " ")
        .replace("\u00a0", " ")
        .replace("\u2011", "-")
    )
    text = re.sub(
        r"\s*\[(DECISIVE|VERIFIED ANSWER)\]|^(DECISIVE( SHIFT)?|VERIFIED ANSWER):\s*", "", text
    )  # copied labels
    return re.sub(r"\[\s*[?*#]?\s*([CSME]\d+)(?:†[^\]]*)?\s*\]", r"[\1]", text)  # also [?S1]


FAKE_TAG = re.compile(r"\[(?![CSME]\d+\])([A-Z][A-Z _-]{2,})\]")


def initials_match(name: str, text: str) -> bool:
    """A full name the writer used for a scorecard's initials form: same surname, first initial among the initials."""
    parts = name.split()
    if len(parts) < 2:
        return False
    return bool(
        re.search(rf"\b[A-Z]{{0,3}}{re.escape(parts[0][0])}[A-Z]{{0,3}}\.?\s+{re.escape(parts[-1])}\b", text)
    )


_NAME_PARTS: dict[int, tuple[set[str], set[str]]] = {}
PAIR = re.compile(r"\b([A-Z][a-z]{2,}) ([A-Z][a-z]{2,})\b")


def invented_names(text: str, shown: str, cat: Catalog) -> list[str]:
    """'Kieron Pandya' for 'KH Pandya': a real first name on a real surname that no player has."""
    if id(cat) not in _NAME_PARTS:
        parts = [p.name.split() for p in cat.players if len(p.name.split()) >= 2]
        _NAME_PARTS[id(cat)] = ({p[0].casefold() for p in parts}, {p[-1].casefold() for p in parts})
    firsts, lasts = _NAME_PARTS[id(cat)]
    low = shown.casefold()
    return sorted(
        {
            f"{a} {b}"
            for a, b in PAIR.findall(text)
            if a.casefold() in firsts
            and b.casefold() in lasts
            and f"{a} {b}".casefold() not in cat.by_full
            and f"{a} {b}".casefold() not in low
        }
    )


def cited(answer: str) -> list[str]:
    return [t.strip() for m in CITE.finditer(answer) for t in m.group(1).split(",")]


def allowed_numbers(src: Sources, question: str, refs: list[str]) -> set[str]:
    ok = numbers(question)
    for r in refs:
        if r in src.chunks:
            c = src.chunks[r]
            ok |= (
                numbers(c["header"]) | numbers(c.get("ball_labels", "")) | {str(c["over"]), str(c["inning"])}
            )
        elif r in src.stats:
            ok |= numbers(str({k: v for k, v in src.stats[r].items() if k != "match_id"}))
    return ok


def check(answer: str, question: str, src: Sources, cat: Catalog, used_tools: bool) -> list[str]:
    problems: list[str] = []
    answer = normalise(answer)
    refs = cited(answer)
    unknown = [r for r in refs if r not in src.chunks and r not in src.stats]
    if unknown:
        problems.append(f"cites sources that do not exist: {', '.join(sorted(set(unknown)))}")
    fake = sorted(set(FAKE_TAG.findall(answer)))
    if fake:
        problems.append(
            f"cites labels that are not sources: {', '.join(fake)}; cite only tags like [C1] or [S1]"
        )
    if used_tools and not refs:
        problems.append("no citations: end each factual sentence with [C#] or [S#]")
    text = CITE.sub(" ", answer)
    stray = numbers(text) - allowed_numbers(src, question, refs)
    if stray:
        problems.append(f"numbers not in the cited headers or stats results: {', '.join(sorted(stray))}")
    # Names: anywhere in the evidence shown to the writer (numbers stay bound to the cited sources above).
    named = set(link_players(text, cat).players)
    known = set(link_players(question, cat).players)
    every = list(src.chunks) + list(src.stats)
    for r in every:
        if r in src.chunks:
            c = src.chunks[r]
            known |= {p for p in c["players"].split("|") if p}
            known |= set(link_players(c.get("body", ""), cat).players)  # fielders named in the commentary
        else:
            known |= set(link_players(str(src.stats[r]), cat).players)
    shown = " ".join(
        str(src.chunks[r].get("header", "")) + " " + str(src.chunks[r].get("body", ""))
        if r in src.chunks
        else str(src.stats.get(r, ""))
        for r in every
    )
    named = {n for n in named - known if not initials_match(n, shown)}  # "Cameron White" for "CL White"
    if named - known:
        problems.append(f"names players not in the evidence: {', '.join(sorted(named - known))}")
    if made_up := invented_names(text, shown, cat):
        problems.append(
            f"names that no player has: {', '.join(made_up)}; use the names as written in the evidence"
        )
    return problems
