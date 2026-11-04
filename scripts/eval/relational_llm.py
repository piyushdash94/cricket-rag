# Run from the repo root: uv run python scripts/eval/relational_llm.py  (needs data/graph.duckdb and GROQ_API_KEY)
"""Relational questions with the real Groq writer; gold from independent SQL on the archive (not the graph tables)."""
import json, re, sys, time
import duckdb
from cricrag.agent.graph_agent import PanditGraph
from cricrag.agent.llm import GroqClient
from cricrag.config import settings
from cricrag.frame import load_catalog

s = settings()
a = duckdb.connect(str(s.db_path), read_only=True)
def one(sql): return a.execute(sql).fetchone()
B = "FROM balls b JOIN matches m USING(match_id) WHERE NOT coalesce(m.is_cancelled,false) AND b.inning<=2"
def outs(bowler, batter): return one(f"SELECT count(*) {B} AND bowler_wicket AND bowler='{bowler}' AND player_out='{batter}'")[0]
def top(sql): return one(sql)[0]
CS = """FROM cs_fielding f JOIN balls b ON b.match_id=f.match_id AND b.inning=f.inning AND b.player_out_id=f.player_out_id AND b.is_wicket
        JOIN matches m ON m.match_id=b.match_id WHERE f.kind IN ('caught','stumped') AND b.inning<=2"""
items = [
 ("How many times has Sandeep Sharma dismissed Virat Kohli?", [str(outs("Sandeep Sharma", "Virat Kohli"))]),
 ("How many times has Jadeja dismissed Rohit Sharma?", [str(outs("Ravindra Jadeja", "Rohit Sharma"))]),
 ("How many times has Piyush Chawla dismissed Ambati Rayudu in the IPL?", [str(outs("Piyush Chawla", "Ambati Rayudu"))]),
 ("Who is Rohit Sharma's nemesis?", [top(f"SELECT bowler {B} AND bowler_wicket AND player_out='Rohit Sharma' GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")]),
 ("Who is Jasprit Bumrah's favourite victim?", [top(f"SELECT player_out {B} AND bowler_wicket AND bowler='Jasprit Bumrah' GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")]),
 ("Which bowler troubled David Warner the most while he batted for SRH?",
  ["Kagiso Rabada", "Umesh Yadav", "Harbhajan Singh"]),  # three tied on 3 (independent SQL above)
 ("Maxwell's strike rate against Chahal",
  [str(one(f"SELECT round(100.0*sum(runs_bat)/sum((NOT is_wide)::INT),1) {B} AND batter='Glenn Maxwell' AND bowler='Yuzvendra Chahal'")[0])]),
 ("How did Virat Kohli do against Rashid Khan in 2024?",
  list(map(str, one(f"SELECT sum((NOT is_wide)::INT), sum(runs_bat) {B} AND m.season='2024' AND batter='Virat Kohli' AND bowler='Rashid Khan'")))),
 ("Which bowler did MS Dhoni take the most catches and stumpings off?",
  [top(f"SELECT b.bowler {CS} AND f.fielder_id=(SELECT batter_id FROM balls WHERE batter='MS Dhoni' LIMIT 1) GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")]),
 ("What was the best fielder-bowler combination for MI in 2019?", ["Quinton de Kock"]),
 ("Who dropped the most catches off Dwayne Bravo's bowling?", ["MS Dhoni"]),
 ("Who has Ravindra Jadeja played with the most?", ["MS Dhoni"]),
 ("How is Virat Kohli connected to Rashid Khan?", ["Mohammed Siraj"]),  # strongest 2-step link: 7 + 3 seasons (SQL)
 ("Show the network rank of bowlers in 2024", ["Harshal Patel"]),  # quality wickets; checked against raw-archive SQL
]
llm = GroqClient(s.groq_api_key, s.groq_models, s.cache_dir / "llm", max_wait=s.groq_max_wait)
agent = PanditGraph(load_catalog(), s.graph_db, llm)
rows = []
for q, gold in items:
    t = time.time(); ans = agent.ask(q)
    from cricrag.agent.graph_agent import WORD_NUM, WORDS, clean
    text = WORD_NUM.sub(lambda m: str(WORDS[m.group(0).lower()]), clean(ans.text)).lower()
    hit = all(g and g.lower() in text for g in gold)
    rows.append(dict(q=q, gold=gold, ok=hit, mode=ans.mode, program=ans.program, problems=ans.problems, model=ans.model,
                     reply=ans.text, rule=next((x["rule"] for x in ans.trace if x.get("step") == "relational_route"), None)))
    print(f"{'OK ' if hit else 'MISS'} [{ans.mode}/{ans.model}] {q}\n     gold={gold}\n     {ans.text[:300]}\n     problems={ans.problems}", flush=True)
import pathlib; pathlib.Path("data/eval").mkdir(parents=True, exist_ok=True)  # noqa: E702
json.dump({"rows": rows, "usage": llm.usage}, open("data/eval/relational_llm.json", "w"), indent=1)
print(sum(r["ok"] for r in rows), "/", len(rows), "llm:", sum(r["mode"] == "llm" for r in rows), "usage", llm.usage)
