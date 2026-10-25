"""Score cricbot (the oracle) on benchmark/v3_new_questions, using the rules in that set's README.

Standard library only, so it runs inside cricbot's own venv:

    <cricbot>\\.venv\\Scripts\\python.exe src\\cricrag\\eval\\v3_baseline.py --cricbot <cricbot> --planner rules
    <cricbot>\\.venv\\Scripts\\python.exe src\\cricrag\\eval\\v3_baseline.py --cricbot <cricbot> --planner hybrid --answer

cricbot's own runner only knows v1/v2 behaviours; v3 adds define_metric, correct_premise, say_unknown
and route_match. This is the first run on v3, so its numbers are the held-out baseline. Do not tune on it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ASK = re.compile(r"\bwhich\b|\bdo you mean\b|\bcould be\b|\bclarify\b", re.I)


def contains(reply: str, wanted: str) -> bool:
    """Same rule as cricbot's run_qna: numbers match as whole numbers, text case-insensitively."""
    if re.fullmatch(r"[\d.]+", wanted):
        return re.search(rf"(?<![\d.]){re.escape(wanted)}(?!\d|\.\d)", reply.replace(",", "")) is not None
    return wanted.casefold() in reply.casefold()


def all_groups(reply: str, groups: list[list[str]]) -> bool:
    return bool(groups) and all(any(contains(reply, o) for o in g) for g in groups)


def asked(reply: str) -> bool:
    return bool(ASK.search(reply)) and "?" in reply


def score(item: dict[str, Any], reply: str, calls: list[dict], ui_actions: list[dict]) -> tuple[bool, str]:
    """(passed, note). The note flags what a person should still check by hand."""
    b, checks = item["behaviour"], item.get("checks") or []
    if b == "answer":
        return all_groups(reply, checks) and not asked(reply), ""
    if b == "route_match":
        opened = any("open_match" in json.dumps(a) for a in ui_actions or [])
        return opened and (not checks or all_groups(reply, checks)), "" if opened else "match view not opened"
    if b == "clarify":
        named = sum(1 for n in checks[0] if n.casefold() in reply.casefold())
        return asked(reply) and named >= 2, f"named {named} options"
    if b in ("refuse", "say_unknown"):
        phrase = any(p in reply.casefold() for p in checks[0])
        if phrase and not calls:
            return True, ""
        if b == "say_unknown":
            gold = item.get("gold_answer", "").split(" (")[0].split(";")[0].strip()
            if gold and len(gold) > 3 and gold.casefold() in reply.casefold():
                return True, "gave the published answer"
        return False, "ran IPL queries" if calls else "no refusal phrase"
    if b == "correct_premise":
        return all_groups(reply, checks), "check by hand: no number for the false premise"
    if b == "define_metric":
        hit = [r["metric"] for r in item.get("rubric") or [] if all_groups(reply, r["must"])]
        return bool(hit), f"metric: {hit[0]}" if hit else "no rubric option matched"
    return False, f"unknown behaviour {b}"


def report(rows: list[dict[str, Any]]) -> str:
    def line(name: str, rs: list[dict]) -> str:
        ok = sum(r["ok"] for r in rs)
        return f"| {name} | {ok}/{len(rs)} ({100 * ok // max(len(rs), 1)}%) |"

    out = ["| Group | Passed |", "|---|---|", line("ALL", rows)]
    for key in ("behaviour", "capability", "category"):
        groups: dict[str, list] = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r)
        out += [line(f"{key}: {k}", v) for k, v in sorted(groups.items())]
    return "\n".join(out)


def run(cricbot: Path, dataset: Path, planner: str, answer: bool, out: Path, sleep: float) -> list[dict]:
    os.chdir(cricbot)
    sys.path.insert(0, str(cricbot))
    from backend.agent.llm import LLMRouter, load_env_file  # type: ignore[import-not-found]
    from backend.agent.service import CricbotAgent  # type: ignore[import-not-found]
    from backend.eval.run_qna import PatientRouter, PlanOnlyRouter  # type: ignore[import-not-found]

    load_env_file()
    os.environ.setdefault("LLM_PROVIDERS", "groq")  # wait out Groq limits; don't silently switch provider
    os.environ["PANDIT_PLANNER"] = planner
    router: Any = (
        LLMRouter([])
        if planner == "rules"
        else (PatientRouter() if answer else PlanOnlyRouter(PatientRouter()))
    )
    agent = CricbotAgent(llm=router)
    items = [json.loads(x) for x in dataset.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows: list[dict] = []
    for n, item in enumerate(items, 1):
        t = time.time()
        try:
            res = agent.answer(item["question"], history=item.get("history") or None)
        except Exception as exc:
            res = {"reply": f"<error: {exc!r}>", "trace": [], "ui_actions": []}
        step = next((e for e in res.get("trace", []) if e.get("node") == "plan_question"), {}).get(
            "patch", {}
        )
        calls = step.get("calls") or []
        reply = (res.get("reply") or "").replace(" ", " ")
        ok, note = score(item, reply, calls, res.get("ui_actions") or [])
        rows.append(
            {
                "id": item["id"],
                "behaviour": item["behaviour"],
                "capability": item["capability"],
                "category": item["category"],
                "question": item["question"],
                "ok": ok,
                "note": note,
                "planner": step.get("planner") or "",
                "calls": calls,
                "reply": reply,
                "validation": res.get("validation", ""),
                "provider": res.get("provider") or "",
                "model": res.get("model") or "",
                "seconds": round(time.time() - t, 2),
            }
        )
        print(f"[{n}/{len(items)}] {'PASS' if ok else 'FAIL'} {item['id']} {note}", flush=True)
        out.write_text(
            json.dumps(
                {"planner": planner, "answer": answer, "partial": n < len(items), "rows": rows}, indent=1
            ),
            encoding="utf-8",
        )
        if sleep:
            time.sleep(sleep)
    return rows


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cricbot", required=True, type=Path)
    ap.add_argument(
        "--dataset", type=Path, help="default: <cricbot>/benchmark/v3_new_questions/v3_new_questions.jsonl"
    )
    ap.add_argument("--planner", choices=["rules", "hybrid", "llm"], default="rules")
    ap.add_argument("--answer", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    cricbot = a.cricbot.resolve()
    dataset = (a.dataset or cricbot / "benchmark" / "v3_new_questions" / "v3_new_questions.jsonl").resolve()
    out = (
        a.out or Path.cwd() / "data" / "baselines" / f"v3_{a.planner}{'_answer' if a.answer else ''}.json"
    ).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = run(cricbot, dataset, a.planner, a.answer, out, a.sleep)
    table = report(rows)
    print("\n" + table)
    out.write_text(
        json.dumps({"planner": a.planner, "answer": a.answer, "summary": table, "rows": rows}, indent=1),
        encoding="utf-8",
    )
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
