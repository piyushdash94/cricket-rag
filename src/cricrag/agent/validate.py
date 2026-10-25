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


def normalise(text: str) -> str:
    """LLMs write 【S1】, [ E1 ], [S1†L3] and narrow no-break spaces: map them to plain [S1] and spaces."""
    text = (
        text.replace("【", "[")
        .replace("】", "]")
        .replace("\u202f", " ")
        .replace("\u00a0", " ")
        .replace("\u2011", "-")
    )
    return re.sub(r"\[\s*([CSME]\d+)(?:†[^\]]*)?\s*\]", r"[\1]", text)


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
            ok |= numbers(str(src.stats[r]))
    return ok


def check(answer: str, question: str, src: Sources, cat: Catalog, used_tools: bool) -> list[str]:
    problems: list[str] = []
    answer = normalise(answer)
    refs = cited(answer)
    unknown = [r for r in refs if r not in src.chunks and r not in src.stats]
    if unknown:
        problems.append(f"cites sources that do not exist: {', '.join(sorted(set(unknown)))}")
    if used_tools and not refs:
        problems.append("no citations: end each factual sentence with [C#] or [S#]")
    text = CITE.sub(" ", answer)
    stray = numbers(text) - allowed_numbers(src, question, refs)
    if stray:
        problems.append(f"numbers not in the cited headers or stats results: {', '.join(sorted(stray))}")
    named = set(link_players(text, cat).players)
    known = set(link_players(question, cat).players)
    for r in refs:
        if r in src.chunks:
            c = src.chunks[r]
            known |= {p for p in c["players"].split("|") if p}
            known |= set(link_players(c.get("body", ""), cat).players)  # fielders named in the commentary
        elif r in src.stats:
            known |= set(link_players(str(src.stats[r]), cat).players)
    if named - known:
        problems.append(f"names players not in the cited sources: {', '.join(sorted(named - known))}")
    return problems
