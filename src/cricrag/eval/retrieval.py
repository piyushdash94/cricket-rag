"""Retrieval evaluation: BM25 vs dense vs hybrid (equal / weighted) vs hybrid + rerank, with and without
frame filters, across five question families whose gold overs come from SQL or audited fielding events:

  wicket     one ball named by batter and bowler          drop       one ball named by fielder and batter
  spell      every over a bowler bowled in one match      chase      the last two overs of a chase
  described  no names at all: any death-overs drop in a chase that season

Choose settings on dev, report test. Story questions ("where did the final turn") are answered by
match_story from SQL, so they are scored end to end, not here.
"""

from __future__ import annotations

import csv
import json
import math
import random
import statistics
import time
from pathlib import Path
from typing import Any

from cricrag.entities import Catalog, canon
from cricrag.frame import build_frame
from cricrag.retrieval.hybrid import Filters, Retriever

WICKET_SQL = """
WITH w AS (
  SELECT b.match_id, b.inning, b.over_number AS over, b.player_out, b.bowler, lower(b.dismissal_type) AS how,
         b.batting_team, m.season, year(m.start_date) AS year, m.team1, m.team2
  FROM balls b JOIN matches m USING (match_id)
  WHERE b.is_wicket AND lower(b.dismissal_type) IN ('caught', 'bowled', 'lbw', 'stumped')
    AND b.player_out <> '' AND b.bowler <> ''
)
SELECT * FROM w
QUALIFY count(*) OVER (PARTITION BY player_out, bowler, season, team1, team2) = 1
"""
# one bowler, one match, at least 3 overs: gold = every over he bowled
SPELL_SQL = """
SELECT * FROM (
  SELECT b.match_id, b.inning, b.bowler, b.batting_team, m.season, m.team1, m.team2, year(m.start_date) AS year,
         list(DISTINCT b.over_number) AS overs
  FROM balls b JOIN matches m USING (match_id)
  WHERE b.inning <= 2 GROUP BY ALL HAVING count(DISTINCT b.over_number) >= 3
) QUALIFY count(*) OVER (PARTITION BY bowler, season, batting_team) = 1
"""
# how a chase ended: gold = the last two overs of the second innings
CHASE_SQL = """
SELECT * FROM (
  SELECT b.match_id, b.batting_team, m.season, m.team1, m.team2, year(m.start_date) AS year, max(b.over_number) AS last
  FROM balls b JOIN matches m USING (match_id) WHERE b.inning = 2 GROUP BY ALL
) QUALIFY count(*) OVER (PARTITION BY season, least(team1, team2), greatest(team1, team2)) = 1
"""
TEMPLATES = {
    "wicket": [
        "How was {out} dismissed by {bowler} when {bat} played {bowl} in IPL {year}?",
        "Describe how {bowler_s} got {out_s} out in the {year} game between {bat} and {bowl}.",
        "What happened when {out_s} fell to {bowler_s} ({how}) against {bowl} in {year}?",
    ],
    "spell": [
        "Describe {bowler}'s spell against {bat} in IPL {year}.",
        "How did {bowler_s} bowl to {bat} in {year}?",
    ],
    "chase": [
        "How did {bat}'s chase against {bowl} end in IPL {year}?",
        "Describe the finish of the {year} game where {bat} chased against {bowl}.",
    ],
    "drop": [
        "When did {fielder} drop {batter} in IPL {year}?",
        "Which over did {fielder_s} put down {batter_s} in {year}?",
        "Describe the chance {fielder_s} missed off {bowler_s}'s bowling to {batter_s} in {year}.",
    ],
    "described": ["A catch was dropped in the death overs of a chase in IPL {year}. Describe it."],
}


def _surname(name: str) -> str:
    return name.split()[-1] if name else name


def _teams(r: dict) -> tuple[str, str]:
    t1, t2, bat = canon(r["team1"]), canon(r["team2"]), canon(r["batting_team"])
    return bat, (t2 if bat == t1 else t1)


def _rows(con, sql: str) -> list[dict]:
    rel = con.sql(sql)
    return [dict(zip(rel.columns, r, strict=True)) for r in rel.fetchall()]


def build_set(
    con, fielding_csv: Path | None, n: int = 120, seed: int = 7, allow_no_fielding: bool = False
) -> list[dict]:
    """Five families, gold (match:inning:over, possibly several) from SQL or audited fielding events."""
    if not (fielding_csv and Path(fielding_csv).is_file()) and not allow_no_fielding:
        raise FileNotFoundError(
            "FIELDING_EVENTS is not set or missing: drop questions need it (or pass allow_no_fielding)"
        )
    rng = random.Random(seed)
    items: list[dict[str, Any]] = []

    def add(kind: str, i: int, gold: list[str], **kw) -> None:
        tpl = TEMPLATES[kind][i % len(TEMPLATES[kind])]
        items.append(
            {
                "id": f"{kind}-{i}",
                "kind": kind,
                "template": i % len(TEMPLATES[kind]),
                "question": tpl.format(**kw),
                "gold": gold,
            }
        )

    wickets = [w for w in _rows(con, WICKET_SQL) if _surname(w["player_out"]) != _surname(w["bowler"])]
    for i, w in enumerate(rng.sample(wickets, min(n, len(wickets)))):
        bat, bowl = _teams(w)
        add(
            "wicket",
            i,
            [f"{w['match_id']}:{w['inning']}:{w['over']}"],
            out=w["player_out"],
            bowler=w["bowler"],
            out_s=_surname(w["player_out"]),
            bowler_s=_surname(w["bowler"]),
            bat=bat,
            bowl=bowl,
            year=w["year"],
            how=w["how"],
        )
    spells = _rows(con, SPELL_SQL)
    for i, sp in enumerate(rng.sample(spells, min(n // 2, len(spells)))):
        bat, _ = _teams(sp)
        add(
            "spell",
            i,
            [f"{sp['match_id']}:{sp['inning']}:{o}" for o in sorted(sp["overs"])],
            bowler=sp["bowler"],
            bowler_s=_surname(sp["bowler"]),
            bat=bat,
            year=sp["year"],
        )
    chases = _rows(con, CHASE_SQL)
    for i, ch in enumerate(rng.sample(chases, min(n // 2, len(chases)))):
        bat, bowl = _teams(ch)
        add(
            "chase",
            i,
            [f"{ch['match_id']}:2:{o}" for o in (ch["last"] - 1, ch["last"])],
            bat=bat,
            bowl=bowl,
            year=ch["year"],
        )
    if fielding_csv and Path(fielding_csv).is_file():
        years = dict(con.sql("select season, min(year(start_date)) from matches group by 1").fetchall())
        with open(fielding_csv, encoding="utf-8", newline="") as fh:
            drops = [r for r in csv.DictReader(fh) if r["label"] == "drop"]
        gold = lambda r: f"{r['match_id']}:{r['inning']}:{int(str(r['over_label']).split('.')[0]) + 1}"  # noqa: E731
        named, seen = [], set()
        for r in drops:
            key = (r["fielder"], r["batter"], r["season"])
            if r["fielder"] and r["confidence"] in ("very_high", "high") and key not in seen:
                seen.add(key)
                named.append(r)
        for i, r in enumerate(rng.sample(named, min(n // 2, len(named)))):
            add(
                "drop",
                i,
                [gold(r)],
                fielder=r["fielder"],
                batter=r["batter"],
                fielder_s=_surname(r["fielder"]),
                batter_s=_surname(r["batter"]),
                bowler_s=_surname(r["bowler"]),
                year=years.get(r["season"], r["season"]),
            )
        by_year: dict[Any, list[str]] = {}
        for r in drops:  # no names in the question: any death-overs drop in a chase that season is correct
            if (
                r["inning"] == "2"
                and int(str(r["over_label"]).split(".")[0]) >= 15
                and r["confidence"] != "very_low"
            ):
                by_year.setdefault(years.get(r["season"], r["season"]), []).append(gold(r))
        for i, (year, g) in enumerate(sorted(by_year.items(), key=lambda kv: str(kv[0]))):
            add("described", i, sorted(set(g)), year=year)
    rng.shuffle(items)
    for j, it in enumerate(items):
        it["split"] = "test" if j % 10 < 3 else "dev"
    return items


def evaluate(
    items: list[dict],
    retriever: Retriever,
    cat: Catalog,
    modes: list[str],
    filtered: list[bool],
    k: int = 10,
    pool: int = 50,
) -> list[dict]:
    results = []
    for use_frame in filtered:
        for mode in modes:
            for it in items:
                f = Filters.from_frame(build_frame(it["question"], cat)) if use_frame else Filters()
                f.players = []  # players act through the query text here; the soft filter is tested separately
                t = time.perf_counter()
                res = retriever.search(
                    it["question"],
                    f,
                    k=k,
                    mode=mode,  # type: ignore[arg-type]
                    pool=pool,
                )
                ms = (time.perf_counter() - t) * 1000
                ids = [h.id for h in res.hits]
                gold = it["gold"] if isinstance(it["gold"], list) else [it["gold"]]
                ranks = [ids.index(g) + 1 for g in gold if g in ids]
                rank = min(ranks) if ranks else None
                recall = sum(1 for r in ranks if r <= k) / len(gold)
                results.append(
                    {
                        "id": it["id"],
                        "kind": it["kind"],
                        "split": it["split"],
                        "mode": mode,
                        "filtered": use_frame,
                        "rank": rank,
                        "recall@10": round(recall, 3),
                        "ms": round(ms, 1),
                    }
                )
    return results


def summarise(results: list[dict], split: str | None = None) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in results:
        if split and r["split"] != split:
            continue
        groups.setdefault((r["filtered"], r["mode"], r["kind"]), []).append(r)
        groups.setdefault((r["filtered"], r["mode"], "all"), []).append(r)
    out = []
    for (filt, mode, kind), rs in sorted(groups.items()):
        ranks = [r["rank"] for r in rs]
        out.append(
            {
                "filters": "frame" if filt else "none",
                "mode": mode,
                "kind": kind,
                "n": len(rs),
                "hit@1": _rate(ranks, 1),
                "hit@5": _rate(ranks, 5),
                "hit@10": _rate(ranks, 10),
                "MRR@10": round(statistics.fmean([1 / r if r and r <= 10 else 0 for r in ranks]), 3),
                "nDCG@5": round(
                    statistics.fmean([1 / math.log2(r + 1) if r and r <= 5 else 0 for r in ranks]), 3
                ),
                "recall@10": round(statistics.fmean([r.get("recall@10", 0) for r in rs]), 3),
                "ms_p50": round(statistics.median([r["ms"] for r in rs]), 1),
            }
        )
    return out


def _rate(ranks: list[int | None], k: int) -> float:
    return round(sum(1 for r in ranks if r and r <= k) / max(len(ranks), 1), 3)


def to_markdown(rows: list[dict]) -> str:
    cols = [
        "filters",
        "mode",
        "kind",
        "n",
        "hit@1",
        "hit@5",
        "hit@10",
        "recall@10",
        "MRR@10",
        "nDCG@5",
        "ms_p50",
    ]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(lines)


def save(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(i) for i in items) + "\n", encoding="utf-8")


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
