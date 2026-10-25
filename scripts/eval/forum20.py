"""20 public IPL stats questions (4 sources x 5). Gold = independent SQL on the archive; then Pandit answers."""
import json, textwrap, warnings
warnings.filterwarnings("ignore")
import duckdb

from cricrag.config import settings

EVAL = settings().data_dir / "eval"
EVAL.mkdir(parents=True, exist_ok=True)

ARCH = settings().db_path
con = duckdb.connect(str(settings().events_db), read_only=True)
con.execute(f"ATTACH '{ARCH}' AS a (READ_ONLY)")
def top(sql, n=3):
    try:
        return con.execute(sql).fetchall()[:n]
    except Exception as e:
        return [("ERR", str(e).splitlines()[0][:160])]

PLAYED = "(SELECT batter p, season FROM a.balls JOIN a.matches USING(match_id) UNION SELECT bowler, season FROM a.balls JOIN a.matches USING(match_id))"
GOLD = {
 "A1": ("Which team won the first IPL tournament in 2008?", "SELECT winner FROM a.matches WHERE season='2007/08' AND lower(title) LIKE '%final%'"),
 "A2": ("Who has taken the most wickets in IPL history?", "SELECT player, sum(wickets) w FROM a.cs_bowling_card WHERE inning<=2 GROUP BY 1 ORDER BY w DESC"),
 "A3": ("Who has the most hat-tricks in IPL history?", "SELECT player, count(*) n FROM events WHERE type='hattrick' GROUP BY 1 ORDER BY n DESC"),
 "A4": ("Who has the best bowling figures in an IPL innings?", "SELECT player, wickets, conceded FROM a.cs_bowling_card WHERE inning<=2 ORDER BY wickets DESC, conceded"),
 "A5": ("Which team has the lowest team total in IPL history?", "SELECT team, runs, wickets FROM a.cs_innings_card WHERE inning<=2 AND (wickets=10 OR balls>=120) ORDER BY runs"),
 "B1": ("Who has scored the most runs in IPL history?", "SELECT player, sum(runs) r FROM a.cs_batting_card WHERE inning<=2 GROUP BY 1 ORDER BY r DESC"),
 "B2": ("Who has hit the most sixes in IPL history?", "SELECT player, sum(sixes) s FROM a.cs_batting_card WHERE inning<=2 GROUP BY 1 ORDER BY s DESC"),
 "B3": ("Who has taken the most wickets for Gujarat Titans?", "SELECT b.bowler, count(*) w FROM a.balls b JOIN a.matches m USING(match_id) WHERE b.bowler_wicket AND b.batting_team<>'GT' AND 'GT' IN (m.team1,m.team2) GROUP BY 1 ORDER BY w DESC"),
 "B4": ("Which players have played in every IPL season?", f"SELECT p, count(DISTINCT season) n FROM {PLAYED} GROUP BY 1 HAVING n=(SELECT count(DISTINCT season) FROM a.matches) ORDER BY 1"),
 "B5": ("How many runs did Brendon McCullum score in the first IPL match?", "SELECT player, runs, balls FROM a.cs_batting_card WHERE match_id=(SELECT match_id FROM a.matches ORDER BY start_date LIMIT 1) ORDER BY runs DESC"),
 "C1": ("Who has hit the most sixes off the first ball they faced in the IPL?", "SELECT batter, count(*) n FROM (SELECT batter, is_six, row_number() OVER (PARTITION BY match_id, inning, batter ORDER BY sequence) k FROM a.balls WHERE NOT is_wide) WHERE k=1 AND is_six GROUP BY 1 ORDER BY n DESC"),
 "C2": ("Who has the most scores between 30 and 49 in the IPL?", "SELECT player, count(*) n FROM a.cs_batting_card WHERE runs BETWEEN 30 AND 49 AND inning<=2 GROUP BY 1 ORDER BY n DESC"),
 "C3": ("Which captain has won the most consecutive matches in a single IPL season?", "SELECT 'captaincy is not recorded in the archive'"),
 "C4": ("How many IPL matches have been won by a team that lost three or more wickets in the powerplay?", "SELECT count(*) FROM (SELECT match_id, batting_team, sum(is_wicket::INT) w FROM a.balls WHERE over_number<=6 AND inning<=2 GROUP BY 1,2) x JOIN a.matches m USING(match_id) WHERE w>=3 AND replace(m.winner,'KXIP','PBKS')=x.batting_team"),
 "C5": ("How many times has Rohit Sharma been dismissed for a single-digit score in the IPL?", "SELECT count(*) FILTER (WHERE value<10), count(*) FILTER (WHERE value=0) FROM events WHERE type='wicket' AND player='Rohit Sharma'"),
 "D1": ("Who has the most ducks in the IPL?", "SELECT player, count(*) n FROM events WHERE type='wicket' AND value=0 GROUP BY 1 ORDER BY n DESC"),
 "D2": ("Which bowler has bowled the most dot balls in IPL history?", "SELECT bowler, count(*) n FROM a.balls WHERE is_legal AND runs_bat+wide_runs+noball_runs=0 AND inning<=2 GROUP BY 1 ORDER BY n DESC"),
 "D3": ("Who has bowled the most maidens in IPL history?", "SELECT player, count(*) n FROM events WHERE type='maiden' GROUP BY 1 ORDER BY n DESC"),
 "D4": ("Who has the most centuries in IPL history?", "SELECT player, count(*) n FROM events WHERE type='hundred' GROUP BY 1 ORDER BY n DESC"),
 "D5": ("Which batter faced the most dot balls in a single IPL season?", "SELECT batter, m.season, count(*) n FROM a.balls b JOIN a.matches m USING(match_id) WHERE NOT is_wide AND runs_bat=0 AND inning<=2 GROUP BY 1,2 ORDER BY n DESC"),
}
gold = {k: (q, top(sql)) for k, (q, sql) in GOLD.items()}
for k, (q, g) in gold.items(): print(k, g)
con.close()
import sys
if "--gold" in sys.argv: sys.exit()

from cricrag.cli import pandit
p, llm = pandit()
out = []
for k, (q, g) in gold.items():
    b = dict(llm.usage); a = p.ask(q)
    tok = sum(llm.usage[x] - b[x] for x in ("prompt_tokens", "completion_tokens"))
    first = g[0] if g else ()
    name = str(first[0]) if first else ""
    hit = bool(name) and name.split()[-1].lower() in a.text.lower()
    out.append({"id": k, "q": q, "gold": [list(map(str, r)) for r in g], "program": a.program, "mode": a.mode,
                "tokens": tok, "name_hit": hit, "reply": a.text, "trace": a.trace})
    print(f"\n{k} {q}\n  GOLD {g}\n  [{a.program}/{a.mode} {tok}tok] {textwrap.shorten(a.text.replace(chr(10), ' '), 260)}")
    sq = next((t.get("sql") or t.get("reason") for t in a.trace if t.get("step") == "finder"), None)
    if sq: print("  SQL", " ".join(str(sq).split())[:300])
json.dump(out, open(EVAL / "forum20.json", "w", encoding="utf-8"), indent=1, default=str)
print("\nTOKENS", llm.usage)
