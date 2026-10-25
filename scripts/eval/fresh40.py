"""Fresh 40 (frozen before any run), 2 batches x 4 sources x 5. Gold = independent SQL; routes = labelled before running.
batch 1: Outlook India (2025 final), cricket.com (Krunal POTM), RCB stat blast (CSK v RCB), CricTracker (Bumrah)
batch 2: Sportskeeda (Wankhede), Sportskeeda (death bowlers since 2016), Sportskeeda (Bumrah v Hazlewood), Scroll (IPL 2023 awards)
"""
import json, sys, textwrap, time, warnings
warnings.filterwarnings("ignore")
import duckdb

from cricrag.config import settings

EVAL = settings().data_dir / "eval"
EVAL.mkdir(parents=True, exist_ok=True)

batch = 2 if "2" in sys.argv[1:] else 1
con = duckdb.connect(str(settings().events_db), read_only=True)
con.execute(f"ATTACH '{settings().db_path}' AS a (READ_ONLY)")
con.execute("""CREATE TEMP VIEW inn AS SELECT i.match_id, i.inning, replace(i.team,'KXIP','PBKS') team, i.runs, i.wickets, i.balls,
  m.season, m.title, m.venue, m.start_date, replace(m.winner,'KXIP','PBKS') winner,
  replace(CASE WHEN m.team1=i.team THEN m.team2 ELSE m.team1 END,'KXIP','PBKS') opp
  FROM a.ref_innings i JOIN a.matches m USING(match_id) WHERE i.inning<=2 AND NOT i.is_super_over""")
con.execute("CREATE TEMP VIEW bat AS SELECT r.*, i.team, i.opp, i.season, i.title, i.venue, i.start_date FROM a.ref_batting r JOIN inn i USING(match_id, inning)")
con.execute("CREATE TEMP VIEW bowl AS SELECT r.*, i.opp AS team, i.team AS opp, i.season, i.title, i.venue, i.start_date FROM a.ref_bowling r JOIN inn i USING(match_id, inning)")
con.execute("""CREATE TEMP VIEW bl AS SELECT b.*, m.season, year(m.start_date) yr, m.title, m.venue FROM a.balls b JOIN a.matches m USING(match_id) WHERE b.inning<=2""")
F25 = "(SELECT match_id FROM a.matches WHERE season='2025' AND title='Final')"
F17 = "(SELECT match_id FROM a.matches WHERE season='2017' AND title='Final')"
DEATH = "FROM bl WHERE over_number>=16 AND yr>=2016"
def first36(p): return f"SELECT count(*) m, sum(wickets) w FROM (SELECT * FROM bowl WHERE player='{p}' ORDER BY start_date LIMIT 36)"
SE, SF, M, EV = ["stats","finder"], ["finder","stats"], ["match","stats","finder"], ["event","finder","stats"]
B1 = {
 "O1": ("What was the margin of victory in the IPL 2025 final?", f"SELECT winner, status_text FROM a.matches WHERE match_id={F25}", M),
 "O2": ("Who top-scored for Punjab Kings in the IPL 2025 final?", f"SELECT player, runs, balls FROM bat WHERE match_id={F25} AND team='PBKS' ORDER BY runs DESC", M),
 "O3": ("How many runs did RCB score in the powerplay of the IPL 2025 final?", f"SELECT sum(runs_total), sum(is_wicket::INT) FROM bl WHERE match_id={F25} AND batting_team='RCB' AND over_number<=6", ["team_phase","finder","match"]),
 "O4": ("How was Josh Inglis dismissed in the IPL 2025 final?", f"SELECT bowler, dismissal_type, short_text FROM bl WHERE match_id={F25} AND player_out='Josh Inglis'", ["event"]),
 "O5": ("How many wickets did Arshdeep Singh take in the final over of RCB's innings in the 2025 final?", f"SELECT bowler, sum(bowler_wicket::INT), sum(runs_total) FROM bl WHERE match_id={F25} AND inning=1 AND over_number=20 GROUP BY 1", ["event","finder"]),
 "K1": ("Who has won the most player of the match awards in IPL finals?", "SELECT player, count(*) n FROM a.cs_awards JOIN a.matches USING(match_id) WHERE title='Final' GROUP BY 1 ORDER BY n DESC", SF),
 "K2": ("What were Krunal Pandya's bowling figures in the IPL 2025 final?", f"SELECT wickets, conceded, balls FROM bowl WHERE match_id={F25} AND player='Krunal Pandya'", M+["event"]),
 "K3": ("How many runs did Krunal Pandya score in the IPL 2017 final?", f"SELECT runs, balls FROM bat WHERE match_id={F17} AND player='Krunal Pandya'", M),
 "K4": ("Which batters did Krunal Pandya dismiss in the IPL 2025 final?", f"SELECT player_out FROM bl WHERE match_id={F25} AND bowler='Krunal Pandya' AND bowler_wicket", ["event","match","finder"]),
 "K5": ("Where was the IPL 2017 final played?", f"SELECT venue FROM a.matches WHERE match_id={F17}", ["match","stats","finder"]),
 "R1": ("What is the head-to-head record between CSK and RCB in the IPL?", "SELECT winner, count(*) FROM a.matches WHERE team1 IN ('CSK','RCB') AND team2 IN ('CSK','RCB') GROUP BY 1 ORDER BY 2 DESC", SE),
 "R2": ("Who has scored the most runs for RCB against CSK?", "SELECT player, sum(runs) r FROM bat WHERE team='RCB' AND opp='CSK' GROUP BY 1 ORDER BY r DESC", SE),
 "R3": ("Who has taken the most wickets for RCB against CSK?", "SELECT player, sum(wickets) w FROM bowl WHERE team='RCB' AND opp='CSK' GROUP BY 1 ORDER BY w DESC", SE),
 "R4": ("What is the highest individual score by an RCB batter against CSK?", "SELECT player, runs, balls, season FROM bat WHERE team='RCB' AND opp='CSK' ORDER BY runs DESC", SE),
 "R5": ("By how many runs did CSK beat RCB when they scored 226 in IPL 2023?", "SELECT i.runs, m.status_text FROM inn i JOIN a.matches m USING(match_id) WHERE i.team='CSK' AND i.opp='RCB' AND i.season='2023' AND i.runs=226", ["match","finder","stats"]),
 "J1": ("How many wickets did Jasprit Bumrah take in IPL 2020?", "SELECT sum(wickets) FROM bowl WHERE player='Jasprit Bumrah' AND season='2020/21'", SE),
 "J2": ("What are Jasprit Bumrah's best bowling figures in the IPL?", "SELECT wickets, conceded, season FROM bowl WHERE player='Jasprit Bumrah' ORDER BY wickets DESC, conceded", SE),
 "J3": ("What was Jasprit Bumrah's economy rate in IPL 2019?", "SELECT round(6.0*sum(conceded)/sum(balls),2) FROM bowl WHERE player='Jasprit Bumrah' AND season='2019'", SE),
 "J4": ("What were Jasprit Bumrah's figures in the IPL 2020 Qualifier against Delhi Capitals?", "SELECT wickets, conceded, title FROM bowl WHERE player='Jasprit Bumrah' AND season='2020/21' AND opp='DC' AND lower(title) LIKE '%qualifier%'", ["match","event","stats","finder"]),
 "J5": ("How many maidens has Jasprit Bumrah bowled in the IPL?", "SELECT count(*) FROM events WHERE type='maiden' AND player='Jasprit Bumrah'", ["leaderboard","stats","finder"]),
}
B2 = {
 "W1": ("How many IPL matches at the Wankhede have been won by the team batting second?", "SELECT count(*) FILTER (WHERE winner=team), count(*) FROM inn WHERE inning=2 AND venue ILIKE '%Wankhede%' AND winner<>''", SE),
 "W2": ("What is the highest team total at the Wankhede in the IPL?", "SELECT team, runs, wickets, season FROM inn WHERE venue ILIKE '%Wankhede%' ORDER BY runs DESC", SE),
 "W3": ("What is the lowest team total at the Wankhede in the IPL?", "SELECT team, runs, wickets, season FROM inn WHERE venue ILIKE '%Wankhede%' AND (wickets=10 OR balls>=120) ORDER BY runs", SE),
 "W4": ("What is the highest successful run chase at the Wankhede?", "SELECT team, runs, opp, season FROM inn WHERE venue ILIKE '%Wankhede%' AND inning=2 AND team=winner ORDER BY runs DESC", SE),
 "W5": ("What is the highest individual score at the Wankhede in the IPL?", "SELECT player, runs, season FROM bat WHERE venue ILIKE '%Wankhede%' ORDER BY runs DESC", SE),
 "D1": ("Who has taken the most wickets in the death overs in the IPL since 2016?", f"SELECT bowler, count(*) FILTER (WHERE bowler_wicket) w {DEATH} GROUP BY 1 ORDER BY w DESC", SE),
 "D2": ("Which bowler has the best economy rate in the death overs since 2016, minimum 10 innings?", f"SELECT bowler, round(6.0*sum(runs_bat+wide_runs+noball_runs)/sum(is_legal::INT),2) e, count(DISTINCT (match_id, inning)) n {DEATH} GROUP BY 1 HAVING n>=10 ORDER BY e", SE),
 "D3": ("Which bowler has the best strike rate in the death overs since 2016, minimum 10 innings?", f"SELECT bowler, round(sum(is_legal::INT)/nullif(count(*) FILTER (WHERE bowler_wicket),0),2) sr, count(DISTINCT (match_id, inning)) n {DEATH} GROUP BY 1 HAVING n>=10 ORDER BY sr", SE),
 "D4": ("How many wickets has Jasprit Bumrah taken in the death overs in the IPL?", "SELECT count(*) FROM bl WHERE bowler='Jasprit Bumrah' AND over_number>=16 AND bowler_wicket", SE),
 "D5": ("What is Rashid Khan's economy rate in the death overs in the IPL?", "SELECT round(6.0*sum(runs_bat+wide_runs+noball_runs)/sum(is_legal::INT),2) FROM bl WHERE bowler='Rashid Khan' AND over_number>=16", SE),
 "C1": ("How many wickets did Jasprit Bumrah take in his first 36 IPL matches?", first36("Jasprit Bumrah"), SF),
 "C2": ("How many wickets did Josh Hazlewood take in his first 36 IPL matches?", first36("Josh Hazlewood"), SF),
 "C3": ("Who has the better IPL economy rate, Jasprit Bumrah or Josh Hazlewood?", "SELECT player, round(6.0*sum(conceded)/sum(balls),2) FROM bowl WHERE player IN ('Jasprit Bumrah','Josh Hazlewood') GROUP BY 1", SE),
 "C4": ("What are Josh Hazlewood's best bowling figures in the IPL?", "SELECT wickets, conceded, season FROM bowl WHERE player='Josh Hazlewood' ORDER BY wickets DESC, conceded", SE),
 "C5": ("Compare Jasprit Bumrah and Josh Hazlewood's IPL bowling averages.", "SELECT player, round(sum(conceded)/sum(wickets),2), sum(wickets) FROM bowl WHERE player IN ('Jasprit Bumrah','Josh Hazlewood') GROUP BY 1", SE),
 "A1": ("Who won the Orange Cap in IPL 2023?", "SELECT player, sum(runs) r FROM bat WHERE season='2023' GROUP BY 1 ORDER BY r DESC", SE),
 "A2": ("How many fours did Shubman Gill hit in IPL 2023?", "SELECT sum(fours) FROM bat WHERE player='Shubman Gill' AND season='2023'", SE),
 "A3": ("Who has hit the most fours in a single IPL season?", "SELECT player, season, sum(fours) f FROM bat GROUP BY 1,2 ORDER BY f DESC", SE),
 "A4": ("Who took the most wickets in IPL 2023?", "SELECT player, sum(wickets) w FROM bowl WHERE season='2023' GROUP BY 1 ORDER BY w DESC", SE),
 "A5": ("What was Yashasvi Jaiswal's highest score in IPL 2023?", "SELECT runs, balls, opp FROM bat WHERE player='Yashasvi Jaiswal' AND season='2023' ORDER BY runs DESC", SE),
}
QS = B1 if batch == 1 else B2
start = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--from=")), None)
if start:  # resume after a quota stop: skip items already run
    keys = list(QS)
    QS = {k: QS[k] for k in keys[keys.index(start):]}
def top(sql, n=3):
    try: return con.execute(sql).fetchall()[:n]
    except Exception as e: return [("ERR", str(e).splitlines()[0][:160])]
gold = {k: (q, top(sql), routes) for k, (q, sql, routes) in QS.items()}
for k, (q, g, r) in gold.items(): print(k, g)
con.close()
if "--gold" in sys.argv: sys.exit()

from cricrag.cli import pandit
p, llm = pandit()
small = p.intent_llm
out = []
for k, (q, g, routes) in gold.items():
    time.sleep(20)  # Groq free tier: 8k tokens/minute per model
    b, bs = dict(llm.usage), dict(small.usage)
    a = p.ask(q)
    tok = sum(llm.usage[x] - b[x] + small.usage[x] - bs[x] for x in ("prompt_tokens", "completion_tokens"))
    form = next(({x: t[x] for x in ("answer", "unit", "measure", "scope")} for t in a.trace if t.get("step") == "intent"), None)
    tried = [t["program"] for t in a.trace if t.get("step") == "try"]
    why = [t.get("mismatch") for t in a.trace if t.get("step") == "try" and t.get("mismatch")]
    out.append({"id": k, "q": q, "gold": [list(map(str, r)) for r in g], "program": a.program, "mode": a.mode,
                "route_ok": a.program in routes, "routes_ok": routes, "form": form, "tried": tried, "rejected": why,
                "tokens": tok, "reply": a.text, "trace": a.trace})
    print(f"\n{k} {q}\n  GOLD {g}\n  [{a.program}/{a.mode} {tok}tok route_ok={a.program in routes}] form={form} tried={tried} {why or ''}\n  {textwrap.shorten(a.text.replace(chr(10), ' '), 300)}")
    sq = next((t.get("sql") or t.get("reason") for t in a.trace if t.get("step") == "finder"), None)
    if sq: print("  SQL", " ".join(str(sq).split())[:300])
    if any(t.get("step") in ("llm_unavailable", "intent_unavailable") or t.get("reason") == "llm unavailable" for t in a.trace):
        print("\nABORT: Groq quota exhausted; partial results are not a valid score."); out[-1]["aborted"] = True; break
json.dump(out, open(EVAL / f"fresh40_b{batch}{'_from_' + start if start else ''}.json", "w", encoding="utf-8"), indent=1, default=str)
print("\nTOKENS", llm.usage, small.usage)
