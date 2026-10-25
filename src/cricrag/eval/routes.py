"""Route accuracy: did the question reach a program that can answer it?

Labels: evals/routing_labels.jsonl (Claude-drafted; rows count as gold only once `verified` is true).
  plan  intent form + route table only (one small LLM call per question; no cricbot, no writer)
  full  the whole ask(); the final program is scored, and the form is kept from the trace
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

FIELDS = ("answer", "unit", "measure", "scope")


def load(labels: Path, v3: Path | None) -> list[dict[str, Any]]:
    v3_items = {}
    if v3 and v3.is_file():
        for line in v3.read_text(encoding="utf-8").splitlines():
            if line.strip():
                x = json.loads(line)
                v3_items[x["id"]] = x
    items = []
    for line in labels.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        x = json.loads(line)
        src = v3_items.get(x["id"], {})
        x["question"] = x.get("question") or src.get("question")
        x["history"] = src.get("history") or []
        if x["question"]:
            items.append(x)
    return items


def run(pandit, items: list[dict], mode: str = "plan", pause: float = 0.0) -> list[dict[str, Any]]:
    from cricrag.agent import intent as itn
    from cricrag.frame import build_frame, find_matches

    rows = []
    for x in items:
        prev = next((t["content"] for t in reversed(x["history"]) if t.get("role") == "user"), None)
        if mode == "plan":
            frame = build_frame(x["question"], pandit.cat, x["history"] or None)
            matches = find_matches(frame) if (frame.seasons or frame.teams or frame.stage) else []
            it = _patient(lambda q=x["question"], pv=prev: itn.classify(pandit.intent_llm, q, pv))
            if it is None:  # quota gone: keep what we have, flagged
                rows.append({"id": x["id"], "set": x["set"], "aborted": True})
                break
            order = itn.routes(it, len(matches) == 1, frame)
            got, form = order[0], it.as_dict()
        else:
            a = pandit.ask(x["question"], x["history"] or None)
            order = next((t["routes"] for t in a.trace if t.get("step") == "intent"), [])
            form = next(({k: t[k] for k in FIELDS} for t in a.trace if t.get("step") == "intent"), {})
            got = a.program
        rows.append(
            {
                "id": x["id"],
                "set": x["set"],
                "question": x["question"],
                "got": got,
                "routes": order,
                "ok": got in x["routes"],
                "form": form,
                "form_match": {k: form.get(k) == x[k] for k in FIELDS} if form else {},
                "verified_label": x.get("verified", False),
            }
        )
        if pause:
            time.sleep(pause)
    return rows


def _patient(call, waits=(65, 130)):
    """Groq free tier: per-minute and per-day caps. Wait out a per-minute cap; give up on a daily one."""
    from cricrag.agent.llm import LLMUnavailable

    for wait in (0, *waits):
        time.sleep(wait)
        try:
            return call()
        except LLMUnavailable:
            continue
    return None


def report(rows: list[dict]) -> str:
    aborted = any(r.get("aborted") for r in rows)
    rows = [r for r in rows if not r.get("aborted")]
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        for key in ("ALL", f"set: {r['set']}", f"got: {r['got']}"):
            by[key][0] += r["ok"]
            by[key][1] += 1
    lines = ["| group | route ok |", "|---|---|"] + [f"| {k} | {a}/{b} |" for k, (a, b) in sorted(by.items())]
    formed = [r for r in rows if r["form_match"]]
    if formed:
        lines.append("")
        lines.append(
            "form agreement with labels: "
            + ", ".join(f"{k} {sum(r['form_match'][k] for r in formed)}/{len(formed)}" for k in FIELDS)
        )
    misses = [
        f"- {r['id']}: got {r['got']} (tried {r['routes']}) form {r['form']}" for r in rows if not r["ok"]
    ]
    head = ["ABORTED: Groq quota ran out; partial run, not a score.", ""] if aborted else []
    return "\n".join(head + lines + (["", "misses:", *misses] if misses else []))
