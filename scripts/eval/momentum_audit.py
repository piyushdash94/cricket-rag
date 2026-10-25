"""Does the decisive momentum shift favour the team that won? Offline: events.duckdb + archive, no LLM."""
import sys, json
import duckdb

from cricrag.config import settings

EVAL = settings().data_dir / "eval"
EVAL.mkdir(parents=True, exist_ok=True)
tag = sys.argv[1] if len(sys.argv) > 1 else "run"
con = duckdb.connect(str(settings().events_db), read_only=True)
con.execute(f"ATTACH '{settings().db_path}' AS a (READ_ONLY)")
rows = con.execute("""
WITH m AS (SELECT match_id, season, title, replace(winner,'KXIP','PBKS') winner, status_text FROM a.matches
           WHERE winner <> '' AND NOT coalesce(is_cancelled,false)),
s AS (SELECT match_id, inning, label, detail AS kind, value AS mag, side, close FROM events WHERE type='shift')
SELECT m.match_id, m.season, m.title, m.winner, m.status_text,
  count(s.match_id) n,
  any_value(s.side) FILTER (WHERE s.close) dec_side, any_value(s.kind) FILTER (WHERE s.close) dec_kind,
  any_value(s.label) FILTER (WHERE s.close) dec_at, any_value(s.inning) FILTER (WHERE s.close) dec_inn,
  any_value(s.mag) FILTER (WHERE s.close) dec_mag,
  count(*) FILTER (WHERE s.side = m.winner) n_win,
  list(DISTINCT s.kind) kinds
FROM m LEFT JOIN s USING (match_id) GROUP BY ALL ORDER BY m.match_id""").fetchall()
cols = ["match_id","season","title","winner","result","n","dec_side","dec_kind","dec_at","dec_inn","dec_mag","n_win","kinds"]
R = [dict(zip(cols, r)) for r in rows]
have = [r for r in R if r["n"]]
ok = [r for r in have if r["dec_side"] == r["winner"]]
print(f"[{tag}] matches with a result: {len(R)}; with shifts: {len(have)}; decisive favours winner: {len(ok)}/{len(have)} = {100*len(ok)/max(len(have),1):.1f}%")
print(f"  matches with no winner-favouring shift at all: {sum(1 for r in have if r['n_win']==0)}")
by = {}
for r in have: by.setdefault(r["season"], [0, 0]); by[r["season"]][0] += r["dec_side"] == r["winner"]; by[r["season"]][1] += 1
print("  by season:", ", ".join(f"{s} {a}/{b}" for s, (a, b) in sorted(by.items())))
wrong = [r for r in have if r["dec_side"] != r["winner"]]
kinds = {}
for r in wrong: kinds[r["dec_kind"]] = kinds.get(r["dec_kind"], 0) + 1
print("  wrong decisive by kind:", kinds)
close = [r for r in wrong if any(w in (r["result"] or "").lower() for w in ("1 run", "2 runs", "3 runs", "4 runs", "5 runs", "with 1 ball", "with 2 balls", "with 0 balls", "super over", "tie"))]
print(f"  of the wrong ones, close finishes: {len(close)}")
# stratified sample: 3 per season
sample = []
for s in sorted(by):
    pool = [r for r in have if r["season"] == s]
    step = max(len(pool) // 3, 1)
    sample += pool[::step][:3]
print(f"\nSAMPLE {len(sample)} matches (3 per season):")
for r in sample:
    flag = "OK " if r["dec_side"] == r["winner"] else "BAD"
    print(f"  {flag} {r['season']:7} {r['title'][:18]:18} won {r['winner']:4} | decisive {r['dec_kind']} {r['dec_side']} inn{r['dec_inn']} {r['dec_at']} ({r['dec_mag']}) | shifts {r['n']} ({r['n_win']} for winner) {sorted(r['kinds'])}")
json.dump({"summary": [len(R), len(have), len(ok)], "sample": sample, "wrong": wrong}, open(EVAL / f"momentum_audit_{tag}.json", "w"), default=str, indent=1)
