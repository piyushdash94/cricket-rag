"""Fresh set B (frozen before the run): Scroll.in, Business Standard, CricTracker, WION. Gold = independent SQL."""
import json, textwrap, warnings, sys
warnings.filterwarnings("ignore")
import duckdb

from cricrag.config import settings

EVAL = settings().data_dir / "eval"
EVAL.mkdir(parents=True, exist_ok=True)

con = duckdb.connect(str(settings().events_db), read_only=True)
con.execute(f"ATTACH '{settings().db_path}' AS a (READ_ONLY)")
BAT = "a.ref_batting r JOIN a.matches m USING(match_id) WHERE r.inning<=2"
FIN = "title = 'Final' AND winner<>''"
B = "a.balls b JOIN a.matches m USING(match_id) WHERE b.inning<=2"
INN = """(SELECT i.match_id, i.inning, replace(i.team,'KXIP','PBKS') team, i.runs, i.wickets, m.season, m.venue, m.start_date,
  replace(m.winner,'KXIP','PBKS') winner, replace(CASE WHEN m.team1=i.team THEN m.team2 ELSE m.team1 END,'KXIP','PBKS') opp
  FROM a.ref_innings i JOIN a.matches m USING(match_id) WHERE i.inning<=2 AND NOT i.is_super_over)"""
PART = """(SELECT match_id, inning, any_value(replace(batting_team,'KXIP','PBKS')) team, wb+1 wkt, sum(runs_total) runs,
  string_agg(DISTINCT batter, ' & ') pair FROM (SELECT *, sum(is_wicket::INT) OVER (PARTITION BY match_id, inning ORDER BY sequence) - is_wicket::INT wb
  FROM a.balls WHERE inning<=2) GROUP BY match_id, inning, wb)"""
CM = "(SELECT * FROM a.matches WHERE replace(team1,'KXIP','PBKS') IN ('CSK','MI') AND team2 IN ('CSK','MI') AND winner<>'')"
GOLD = {
 "S1": ("What is the highest match aggregate in IPL history?", f"SELECT match_id, sum(runs) agg, string_agg(team || ' ' || runs, ' v ') FROM {INN} GROUP BY 1 ORDER BY agg DESC"),
 "S2": ("Which IPL season had the most centuries?", "SELECT m.season, count(*) n FROM a.ref_batting r JOIN a.matches m USING(match_id) WHERE r.inning<=2 AND r.runs>=100 GROUP BY 1 ORDER BY n DESC"),
 "S3": ("Who hit the most sixes in IPL 2024?", "SELECT player, sum(sixes) s FROM a.ref_batting r JOIN a.matches m USING(match_id) WHERE r.inning<=2 AND m.season='2024' GROUP BY 1 ORDER BY s DESC"),
 "S4": ("How many sixes were hit in total in IPL 2024?", "SELECT sum(sixes) FROM a.ref_batting r JOIN a.matches m USING(match_id) WHERE r.inning<=2 AND m.season='2024'"),
 "S5": ("How many 200-plus team totals were there in IPL 2024?", f"SELECT count(*) FROM {INN} WHERE season='2024' AND runs>=200"),
 "B1": ("What is the highest successful run chase in IPL history?", f"SELECT team, runs, opp, season FROM {INN} WHERE inning=2 AND team=winner ORDER BY runs DESC"),
 "B2": ("What is Rajasthan Royals' highest successful run chase in the IPL?", f"SELECT team, runs, opp, season FROM {INN} WHERE inning=2 AND team=winner AND team='RR' ORDER BY runs DESC"),
 "B3": ("What is Mumbai Indians' highest successful chase against CSK?", f"SELECT team, runs, opp, season FROM {INN} WHERE inning=2 AND team=winner AND team='MI' AND opp='CSK' ORDER BY runs DESC"),
 "B4": ("What is Delhi Capitals' highest successful run chase in the IPL?", f"SELECT team, runs, opp, season FROM {INN} WHERE inning=2 AND team=winner AND team='DC' ORDER BY runs DESC"),
 "B5": ("Which team chased down 215 against Deccan Chargers in 2008?", f"SELECT team, runs, opp, season FROM {INN} WHERE inning=2 AND team=winner AND season='2007/08' AND opp IN ('DCH','DC') ORDER BY runs DESC"),
 "C1": ("What is the head-to-head record between CSK and MI in the IPL?", f"SELECT winner, count(*) FROM {CM} GROUP BY 1 ORDER BY 2 DESC"),
 "C2": ("What is CSK's lowest total against MI in the IPL?", f"SELECT team, runs, wickets, season FROM {INN} WHERE team='CSK' AND opp='MI' ORDER BY runs"),
 "C3": ("What is Mumbai Indians' highest total against CSK?", f"SELECT team, runs, wickets, season FROM {INN} WHERE team='MI' AND opp='CSK' ORDER BY runs DESC"),
 "C4": ("How many times has Piyush Chawla dismissed Ambati Rayudu in the IPL?", "SELECT count(*) FROM a.balls WHERE bowler='Piyush Chawla' AND player_out='Ambati Rayudu' AND bowler_wicket"),
 "C5": ("How many IPL matches at Chepauk have been won by the team batting first?", f"SELECT count(*) FILTER (WHERE winner=team), count(*) FROM {INN} WHERE inning=1 AND venue ILIKE '%Chidambaram%' AND winner<>''"),
 "W1": ("What is the highest partnership in IPL history?", f"SELECT pair, runs, wkt, team, match_id FROM {PART} ORDER BY runs DESC"),
 "W2": ("What is the highest first-wicket partnership in the IPL?", f"SELECT pair, runs, team, match_id FROM {PART} WHERE wkt=1 ORDER BY runs DESC"),
 "W3": ("What partnership did KL Rahul and Nitish Rana put on for Delhi Capitals against Punjab Kings in 2026?", f"SELECT p.pair, p.runs, p.wkt FROM {PART} p JOIN a.matches m USING(match_id) WHERE m.season='2026' AND pair LIKE '%KL Rahul%' AND pair LIKE '%Nitish Rana%'"),
 "W4": ("How many 200-run partnerships have Virat Kohli and AB de Villiers shared in the IPL?", f"SELECT count(*), list(runs) FROM {PART} WHERE runs>=200 AND pair LIKE '%Virat Kohli%' AND pair LIKE '%AB de Villiers%'"),
 "W5": ("What is the highest partnership for Gujarat Titans in the IPL?", f"SELECT pair, runs, wkt, match_id FROM {PART} WHERE team='GT' ORDER BY runs DESC"),
}
def top(sql, n=3):
    try: return con.execute(sql).fetchall()[:n]
    except Exception as e: return [("ERR", str(e).splitlines()[0][:160])]
gold = {k: (q, top(sql)) for k, (q, sql) in GOLD.items()}
for k, (q, g) in gold.items(): print(k, g)
con.close()
if "--gold" in sys.argv: sys.exit()

from cricrag.cli import pandit
p, llm = pandit()
out = []
import time
for k, (q, g) in gold.items():
    time.sleep(20)  # Groq free tier: 8k tokens/minute
    b = dict(llm.usage); a = p.ask(q)
    tok = sum(llm.usage[x] - b[x] for x in ("prompt_tokens", "completion_tokens"))
    out.append({"id": k, "q": q, "gold": [list(map(str, r)) for r in g], "program": a.program, "mode": a.mode, "tokens": tok, "reply": a.text, "trace": a.trace})
    print(f"\n{k} {q}\n  GOLD {g}\n  [{a.program}/{a.mode} {tok}tok] {textwrap.shorten(a.text.replace(chr(10), ' '), 300)}")
    sq = next((t.get("sql") or t.get("reason") for t in a.trace if t.get("step") == "finder"), None)
    if sq: print("  SQL", " ".join(str(sq).split())[:300])
    if any(t.get("step") == "llm_unavailable" or t.get("reason") == "llm unavailable" for t in a.trace):
        print("\nABORT: Groq quota exhausted; partial results are not a valid score."); out[-1]["aborted"] = True; break
json.dump(out, open(EVAL / "fresh20b.json", "w", encoding="utf-8"), indent=1, default=str)
print("\nTOKENS", llm.usage)
