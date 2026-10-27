"""cricbot's verified stats path, run in-process (no server): planner -> verified SQL -> facts.

cricbot's backend/agent/__init__.py imports its LangGraph service. We register that package without
running __init__, so only the stats modules (duckdb + stdlib) load. Nothing in cricbot is modified.
"""

from __future__ import annotations

import os
import sys
import types
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _mods(root: str):
    os.environ.setdefault("CRICBOT_DB", str(Path(root) / "data" / "cricbot.duckdb"))
    if root not in sys.path:
        sys.path.insert(0, root)
    if "backend.agent" not in sys.modules:
        import backend  # noqa: F401  (plain package)

        pkg = types.ModuleType("backend.agent")
        pkg.__path__ = [str(Path(root) / "backend" / "agent")]
        sys.modules["backend.agent"] = pkg
    from backend.agent import entities, stats  # type: ignore[import-not-found]

    return stats, entities


def _router(stats_mod, planner: str):
    if planner == "rules":
        return None
    try:
        from backend.agent.llm import LLMRouter, load_env_file  # type: ignore[import-not-found]

        load_env_file()
        os.environ.setdefault("LLM_PROVIDERS", "groq")
        return LLMRouter()
    except Exception:
        return None


def ask(
    root: str, question: str, history: list[dict] | None = None, planner: str = "hybrid"
) -> dict[str, Any]:
    """{'kind': refuse|clarify|stats|gap|none, ...}. Numbers in 'facts' come from cricbot's verified queries."""
    stats, ent = _mods(root)
    if not stats.available():
        return {"kind": "none", "reason": "cricbot archive not found"}
    q = stats.contextualize(question, history) if history else question
    if topic := stats.out_of_scope(q):
        return {"kind": "refuse", "reply": stats.scope_reply(topic)}
    router = _router(stats, planner)
    index = stats.player_index()  # the rules planner needs it (cricbot's service passes it too)
    entities = ent.resolve_entities(q, history=history or [])
    calls, used, _ = stats.plan(q, entities, router, history, index, mode=planner if router else "rules")
    gaps = stats.capability_gaps(q, calls)
    if not calls:
        return {
            "kind": "gap" if gaps else "none",
            "gaps": gaps,
            "reply": stats.gap_reply(gaps, False) if gaps else "",
        }
    results = stats.run_calls(calls)
    if card := stats.clarify_card(results, q):
        return {
            "kind": "clarify",
            "reply": f"{card['title']} " + ", ".join(o["label"] for o in card["options"]) + "?",
        }
    facts = stats.facts_text(results) + "".join(totals(b) for r in results for b in r["blocks"])
    return {
        "kind": "stats",
        "planner": used,
        "calls": [{k: c.get(k) for k in ("name", "args")} for c in calls],
        "facts": facts,
        "summary": stats.summary(results, q),
        "gaps": gaps,
        "blocks": [b for r in results for b in r["blocks"]],  # shown as tables under the reply (UI only)
        "tables": [  # result shape, for the intent router's fit check
            {
                "title": b.get("title", ""),
                "columns": list(b.get("columns") or []),
                "rows": len(b.get("rows") or []),
            }
            for r in results
            for b in r["blocks"]
            if b.get("type") == "table"
        ],
    }


ADDITIVE = (
    "balls",
    "runs",
    "outs",
    "wickets",
    "inns",
    "innings",
    "4s",
    "6s",
    "fifties",
    "hundreds",
    "50s",
    "100s",
)


def totals(block: dict[str, Any]) -> str:
    """A per-year table gets an all-years line computed in code, so the LLM never adds rows up itself."""
    rows, cols = block.get("rows") or [], block.get("columns") or []
    if block.get("type") != "table" or "year" not in cols or len(rows) < 2:
        return ""
    idx = {c: i for i, c in enumerate(cols)}
    if any(str(r[idx["year"]]).lower() in ("all", "total", "in all") for r in rows):
        return ""
    sums = {c: sum(float(r[idx[c]] or 0) for r in rows) for c in ADDITIVE if c in idx}
    parts = [f"{c} {int(v)}" for c, v in sums.items()]
    if sums.get("balls") and "runs" in sums:
        parts.append(f"strike rate {round(100 * sums['runs'] / sums['balls'], 2)}")
    return f"\nAll years combined (computed): {', '.join(parts)}." if parts else ""


@lru_cache(maxsize=1)
def archive_api(root: str):
    """cricbot's archive, match-page and stats modules for `cricrag serve`: (ipl_data, cricsheet, stats router).

    Loaded the same way as the stats path: no LangGraph service, no cricbot server process.
    """
    _mods(root)
    from backend.apis.stats import router  # type: ignore[import-not-found]
    from backend.src import cricsheet, ipl_data  # type: ignore[import-not-found]

    return ipl_data, cricsheet, router
