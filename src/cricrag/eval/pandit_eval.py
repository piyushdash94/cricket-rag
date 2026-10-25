"""End-to-end evaluation of Pandit.

v3      benchmark/v3_new_questions (50, held out), scored with the same rules as the cricbot baseline.
        route_match rows cannot open a UI here, so they are scored on content only and reported apart.
events  questions whose gold comes from SQL by construction (wicket, drop, fastest fifty, top partnership),
        plus finals' turning points written out for a person to judge (no automatic gold).
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from cricrag.eval.v3_baseline import all_groups, score

NO_QUERY = {"refuse", "clarify", "gap"}


def run_v3(pandit, dataset: Path) -> list[dict[str, Any]]:
    rows = []
    for item in (json.loads(x) for x in dataset.read_text(encoding="utf-8").splitlines() if x.strip()):
        a = pandit.ask(item["question"], item.get("history") or None)
        calls = [] if a.program in NO_QUERY else [{"name": a.program}]
        it = {**item, "behaviour": "answer"} if item["behaviour"] == "route_match" else item
        ok, note = score(it, a.text, calls, [])
        rows.append(
            {
                "id": item["id"],
                "behaviour": item["behaviour"],
                "capability": item["capability"],
                "question": item["question"],
                "ok": ok,
                "note": note,
                "program": a.program,
                "mode": a.mode,
                "problems": a.problems,
                "reply": a.text,
                "seconds": a.seconds,
            }
        )
    return rows


def build_events(edb, n: int = 5, seed: int = 11) -> list[dict[str, Any]]:
    rng = random.Random(seed)

    def q(sql: str) -> list[dict]:
        cur = edb.execute(sql)
        return [dict(zip([d[0] for d in cur.description], r, strict=True)) for r in cur.fetchall()]

    items = []
    wk = q("""SELECT * FROM events WHERE type='wicket' AND player2 IS NOT NULL AND stage IN ('final','playoff')
              QUALIFY count(*) OVER (PARTITION BY player, season, stage) = 1""")
    for r in rng.sample(wk, n):
        items.append(
            {
                "kind": "wicket",
                "question": f"How was {r['player']} out in the IPL {r['year']} {r['title']}?",
                "checks": [[r["player2"].split()[-1]], [r["detail"].split()[0]]],
            }
        )
    dr = q("""SELECT * FROM events WHERE type='drop' AND player IS NOT NULL AND detail IN ('very_high','high')
              QUALIFY count(*) OVER (PARTITION BY player2, season) = 1""")
    for r in rng.sample(dr, n):
        items.append(
            {
                "kind": "drop",
                "question": f"Who dropped {r['player2']} in IPL {r['year']}?",
                "checks": [[r["player"].split()[-1]]],
            }
        )
    for kind, sql, fmt, chk in (
        (
            "fastest_fifty",
            "SELECT * FROM events WHERE type='fifty' QUALIFY row_number() OVER (PARTITION BY year ORDER BY value, date) = 1",
            "What was the fastest fifty in IPL {year}?",
            lambda r: [[r["player"].split()[-1]], [str(int(r["value"]))]],
        ),
        (
            "partnership",
            "SELECT * FROM events WHERE type='partnership' QUALIFY row_number() OVER (PARTITION BY year, batting_team ORDER BY value DESC) = 1",
            "Highest partnership for {batting_team} in IPL {year}?",
            lambda r: [[str(int(r["value"]))]],
        ),
    ):
        for r in rng.sample(q(sql), n):
            items.append({"kind": kind, "question": fmt.format(**r), "checks": chk(r)})
    for r in rng.sample(q("SELECT DISTINCT year FROM events WHERE stage='final'"), n):
        items.append(
            {"kind": "story_review", "question": f"Where did the IPL {r['year']} final turn?", "checks": []}
        )
    return items


def run_events(pandit, items: list[dict]) -> list[dict[str, Any]]:
    rows = []
    for it in items:
        a = pandit.ask(it["question"])
        ok = all_groups(a.text, it["checks"]) if it["checks"] else None
        rows.append(
            {
                **it,
                "ok": ok,
                "program": a.program,
                "mode": a.mode,
                "problems": a.problems,
                "reply": a.text,
                "seconds": a.seconds,
            }
        )
    return rows


def report(rows: list[dict], keys: tuple[str, ...]) -> str:
    out = ["| group | passed |", "|---|---|"]
    graded = [r for r in rows if r["ok"] is not None]
    out.append(f"| ALL | {sum(r['ok'] for r in graded)}/{len(graded)} |")
    for k in keys:
        g: dict[str, list] = defaultdict(list)
        for r in graded:
            g[r[k]].append(r)
        out += [f"| {k}: {v} | {sum(x['ok'] for x in rs)}/{len(rs)} |" for v, rs in sorted(g.items())]
    return "\n".join(out)
