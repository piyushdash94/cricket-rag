"""Fresh 20 (frozen before the run): ETV Bharat FAQ, Gulf News, Howstat, Wikipedia IPL 2017. Gold = independent SQL."""
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
GOLD = {
 "E1": ("Who has scored the most runs in the powerplay in IPL history?", f"SELECT batter, sum(runs_bat) r FROM {B} AND over_number<=6 GROUP BY 1 ORDER BY r DESC"),
 "E2": ("Which bowler has taken the most powerplay wickets in the IPL?", f"SELECT bowler, count(*) w FROM {B} AND over_number<=6 AND bowler_wicket GROUP BY 1 ORDER BY w DESC"),
 "E3": ("Who has the most five-wicket hauls in the IPL?", "SELECT player, count(*) n FROM a.ref_bowling WHERE wickets>=5 AND inning<=2 GROUP BY 1 ORDER BY n DESC, 1"),
 "E4": ("Who has won the most player of the match awards in the IPL?", "SELECT player, count(*) n FROM a.cs_awards GROUP BY 1 ORDER BY n DESC"),
 "E5": ("Which team has finished runner-up the most times in the IPL?", f"SELECT CASE WHEN winner=team1 THEN team2 ELSE team1 END loser, count(*) n FROM a.matches WHERE {FIN} GROUP BY 1 ORDER BY n DESC"),
 "G1": ("Who has scored the fastest century in the IPL?", f"SELECT batter, min(bb) FROM (SELECT b.batter, sum(runs_bat) OVER w br, sum((NOT is_wide)::INT) OVER w bb FROM {B} WINDOW w AS (PARTITION BY match_id, b.inning, batter ORDER BY sequence)) WHERE br>=100 GROUP BY 1 ORDER BY 2"),
 "G2": ("Which player has the most 600-run seasons in the IPL?", f"SELECT player, count(*) n FROM (SELECT player, season, sum(runs) s FROM {BAT} GROUP BY 1,2 HAVING s>=600) GROUP BY 1 ORDER BY n DESC"),
 "G3": ("How many IPL titles have Chennai Super Kings won?", f"SELECT winner, count(*) FROM a.matches WHERE {FIN} GROUP BY 1 ORDER BY 2 DESC"),
 "G4": ("How many runs has Virat Kohli scored for RCB in the IPL?", f"SELECT sum(runs), count(*) FROM {BAT} AND player='Virat Kohli'"),
 "G5": ("How many sixes has MS Dhoni hit in the IPL?", f"SELECT sum(sixes) FROM {BAT} AND player='MS Dhoni'"),
 "H1": ("What is the highest individual score in IPL history?", f"SELECT player, runs, balls, season FROM {BAT} ORDER BY runs DESC"),
 "H2": ("Who has hit the most sixes in a single IPL innings?", f"SELECT player, sixes, season FROM {BAT} ORDER BY sixes DESC"),
 "H3": ("How many centuries did Virat Kohli score in IPL 2016?", f"SELECT count(*) FROM {BAT} AND player='Virat Kohli' AND season='2016' AND runs>=100"),
 "H4": ("What is the highest team total in IPL history?", "SELECT team, runs, wickets, m.season FROM a.ref_innings i JOIN a.matches m USING(match_id) WHERE inning<=2 ORDER BY runs DESC"),
 "H5": ("What is Lasith Malinga's career economy rate in the IPL?", "SELECT round(6.0*sum(conceded)/sum(balls),2), sum(wickets) FROM a.ref_bowling WHERE player='Lasith Malinga' AND inning<=2"),
 "W1": ("Who scored the most runs in IPL 2017?", f"SELECT player, sum(runs) s FROM {BAT} AND season='2017' GROUP BY 1 ORDER BY s DESC"),
 "W2": ("Who took the most wickets in IPL 2017?", "SELECT r.player, sum(wickets) w FROM a.ref_bowling r JOIN a.matches m USING(match_id) WHERE inning<=2 AND season='2017' GROUP BY 1 ORDER BY w DESC"),
 "W3": ("By how many runs did Mumbai Indians win the 2017 IPL final?", f"SELECT winner, status_text FROM a.matches WHERE season='2017' AND {FIN}"),
 "W4": ("Who took hat-tricks in IPL 2017?", "SELECT player, title FROM events WHERE type='hattrick' AND season='2017'"),
 "W5": ("Who hit the most sixes in IPL 2017?", f"SELECT player, sum(sixes) s FROM {BAT} AND season='2017' GROUP BY 1 ORDER BY s DESC"),
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
json.dump(out, open(EVAL / "fresh20.json", "w", encoding="utf-8"), indent=1, default=str)
print("\nTOKENS", llm.usage)
