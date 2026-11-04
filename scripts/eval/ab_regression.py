# Run from the repo root: uv run python scripts/eval/ab_regression.py  (needs .env with GROQ_API_KEY, CRICBOT_ROOT)
"""A/B regression: old Pandit (A) vs PanditGraph (B) on every saved eval question with gold, same archive,
same LanceDB index, same Groq models. B runs second, so a delegated question re-uses A's cached LLM calls
and must produce the identical reply. Resumable: rows append to data/eval/ab_regression.jsonl."""
import json, sys, time
from pathlib import Path
from cricrag.cli import agent as graph_agent, pandit
from cricrag.eval.v3_baseline import all_groups

E = Path("data/eval"); OUT = E / "ab_regression.jsonl"
def load(f):
    d = json.loads((E / f).read_text(encoding="utf-8")); return d["rows"] if isinstance(d, dict) else d
items = []
for name in ("fresh40_b1", "fresh40_b2", "fresh20", "fresh20b", "forum20"):
    for r in load(f"{name}.json"):
        items.append(dict(set=name, id=r["id"], q=r["q"], gold=r.get("gold"), old_saved=r.get("reply", "")))
for i, r in enumerate(load("pandit_events_20261025-1121.json")):
    items.append(dict(set="events", id=f"ev{i}", q=r["question"], gold=r.get("checks"), old_saved=r.get("reply", "")))
only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
done = {json.loads(x)["key"] for x in OUT.read_text().splitlines()} if OUT.exists() else set()
# cricbot's planner (in-process, uncached) would be called twice; memoise it so A and B see the same plan.
import os
from cricrag.config import settings
os.environ.setdefault("GROQ_API_KEY", settings().groq_api_key or "")
from cricrag.cricbot import _mods
_mods(str(settings().cricbot_root))
from backend.agent import llm as cb_llm  # type: ignore
_memo, _orig = {}, cb_llm.ChatProvider._first_available
def _first_available(self, models, body):
    k = json.dumps([models, body], sort_keys=True, default=str)
    if k not in _memo:
        _memo[k] = _orig(self, models, body)
    return _memo[k]
cb_llm.ChatProvider._first_available = _first_available
A, llm_a = pandit(); B, llm_b = graph_agent()
def groups_ok(reply, gold):
    return all_groups(reply, gold) if gold and all(isinstance(g, list) for g in gold) else None
for it in items:
    key = f"{it['set']}:{it['id']}"
    if key in done or (only and it["set"] not in only):
        continue
    t = time.time(); a = A.ask(it["q"]); ta = time.time() - t
    t = time.time(); b = B.ask(it["q"]); tb = time.time() - t
    route = next((s for s in b.trace if s.get("step") == "relational_route"), {})
    row = dict(key=key, **{k: it[k] for k in ("set", "id", "q", "gold")}, route=route.get("program"), rule=route.get("rule"),
               a_program=a.program, a_mode=a.mode, a_reply=a.text, a_ok=groups_ok(a.text, it["gold"]), a_s=round(ta, 1),
               b_program=b.program, b_mode=b.mode, b_reply=b.text, b_ok=groups_ok(b.text, it["gold"]), b_s=round(tb, 1),
               identical=a.text == b.text and a.program == b.program, a_model=a.model, b_model=b.model)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
    flag = "SAME" if row["identical"] else "DIFF"
    print(f"{flag} {key} route={row['route']} A={a.program}/{a.mode}/{row['a_ok']} B={b.program}/{b.mode}/{row['b_ok']} | {it['q'][:70]}", flush=True)
print("usage A", llm_a.usage, "B", llm_b.usage)
