"""Build data/graph.duckdb: canonical people plus player-to-player edge tables, derived from the archive.

The archive is attached read-only and stays the source of truth; this file is rebuilt from it
(`cricrag build-graph`). Edges are aggregated per season so tools can filter without touching balls:

  bowled_to           bowler -> batter    balls, runs, dots, fours, sixes, dismissals (by season, phase, teams)
  dismissed           bowler -> batter    wickets credited to the bowler, by mode
  fielding_dismissal  fielder -> batter   caught / stumped / run out (Cricsheet scorecard), with the bowler
  dropped             fielder -> batter   audited drops / misfields (ipl_fielding), runs added afterwards
  played_for          player  -> team     matches per season
  wicket_detail       bowler  -> batter   each bowler-credited wicket: mode, bowling type, the batter's position

`graph_checks` re-counts each edge table straight from `balls` / `cs_fielding`; every check must be equal
before anything reads the graph (Rule 1: numbers come from SQL, and the graph must agree with it).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from cricrag.graph.identity import (
    INNINGS_SQL,
    build_people,
    has_table,
    identity_report,
    load_xi,
    merge_negative_ids,
    resolve_fielding_csv,
    roster_sql,
)
from cricrag.styles import spin_type_sql

# canonical id: Cricinfo id, or the merged one for an unregistered Cricsheet id
CANON = "coalesce((SELECT to_id FROM id_merge WHERE from_id = {0}), {0})"

BALLS_VIEW = """
CREATE OR REPLACE TEMP VIEW gb AS
SELECT b.*, i.season, i.bat, i.bowl,
  CASE WHEN b.over_number <= 6 THEN 'powerplay' WHEN b.over_number <= 15 THEN 'middle' ELSE 'death' END AS phase
FROM cb.balls b JOIN inn i USING (match_id, inning)
"""

EDGES = {
    "played_for": """SELECT player_id, team, i.season, count(DISTINCT r.match_id) AS matches
        FROM (SELECT DISTINCT match_id, team, player_id FROM roster) r
        JOIN (SELECT DISTINCT match_id, season FROM inn) i USING (match_id) GROUP BY ALL""",
    "bowled_to": f"""SELECT {CANON.format("bowler_id")} AS bowler_id, {CANON.format("batter_id")} AS batter_id,
            season, phase, bat AS bat_team, bowl AS bowl_team,
            sum((NOT is_wide)::INT) AS balls, sum(runs_bat) AS runs,
            sum((NOT is_wide AND runs_bat = 0)::INT) AS dots, sum(is_four::INT) AS fours, sum(is_six::INT) AS sixes,
            sum((bowler_wicket AND player_out_id = batter_id)::INT) AS dismissals
        FROM gb WHERE bowler_id IS NOT NULL AND batter_id IS NOT NULL GROUP BY ALL""",
    "dismissed": f"""SELECT {CANON.format("bowler_id")} AS bowler_id, {CANON.format("player_out_id")} AS batter_id,
            season, lower(dismissal_type) AS mode, count(*) AS n
        FROM gb WHERE bowler_wicket AND bowler_id IS NOT NULL AND player_out_id IS NOT NULL GROUP BY ALL""",
    "fielding_dismissal": f"""SELECT {CANON.format("fielder_id")} AS fielder_id, {CANON.format("player_out_id")} AS batter_id,
            {CANON.format("bowler_id")} AS bowler_id, season, team, kind, bool_or(substitute) AS substitute,
            count(*) AS n
        FROM (SELECT f.fielder_id, b.player_out_id, b.bowler_id, b.season, b.bowl AS team, lower(f.kind) AS kind,
                     coalesce(f.substitute, false) AS substitute
              FROM cb.cs_fielding f
              JOIN gb b ON b.match_id = f.match_id AND b.inning = f.inning AND b.player_out_id = f.player_out_id
                       AND b.is_wicket
              WHERE f.fielder_id IS NOT NULL
              QUALIFY row_number() OVER (PARTITION BY f.match_id, f.inning, f.player_out_id, f.fielder_id
                                         ORDER BY b.sequence) = 1)
        GROUP BY ALL""",
    "dropped": f"""SELECT fielder_id, {CANON.format("batter_id")} AS batter_id, {CANON.format("bowler_id")} AS bowler_id,
            season, team, label, count(*) AS n, sum(runs_after_drop) AS runs_after
        FROM (SELECT m.player_id AS fielder_id, b.batter_id, b.bowler_id, b.season, b.bowl AS team, m.label, m.runs_after_drop
              FROM fielding_mentions m
              JOIN gb b ON b.match_id = m.match_id AND b.inning = m.inning AND b.over_label = m.over_label
              WHERE m.player_id IS NOT NULL
              QUALIFY row_number() OVER (PARTITION BY m.match_id, m.inning, m.over_label, m.label
                                         ORDER BY coalesce(b.is_legal, true) DESC, b.sequence) = 1)
        GROUP BY ALL""",
}


# Each pair: (edge-table total, the same count taken straight from the archive). They must be equal.
# Each wicket the bowler is credited with, by mode, bowling type (pace, or the spin type) and the batter's position
# (1-3 top order, 4-7 middle, 8+ tail; 12 and 13 are impact substitutes). Run outs are the fielder's, not here.
def wicket_detail_sql(styles: bool, positions: bool) -> str:
    kind = f"CASE WHEN s.bowl_kind = 'pace' THEN 'pace' ELSE {spin_type_sql('s')} END" if styles else "NULL"
    return f"""CREATE OR REPLACE TABLE wicket_detail AS
    SELECT {CANON.format("b.bowler_id")} AS bowler_id, {CANON.format("b.player_out_id")} AS batter_id, b.season,
           b.bowl AS team, CASE lower(b.dismissal_type) WHEN 'leg before wicket' THEN 'lbw'
                                ELSE lower(b.dismissal_type) END AS mode,
           coalesce({kind}, 'unknown') AS bowl_type,
           {"r.position" if positions else "NULL::INT"} AS position,
           CASE WHEN position <= 3 THEN 'top order' WHEN position <= 7 THEN 'middle order'
                WHEN position IS NOT NULL THEN 'tail' END AS order_band,
           count(*) AS n
    FROM gb b
    {"LEFT JOIN cb.player_styles s ON s.player_id = b.bowler_id" if styles else ""}
    {"LEFT JOIN cb.ref_batting r ON r.match_id = b.match_id AND r.inning = b.inning AND r.player_id = b.player_out_id" if positions else ""}
    WHERE b.bowler_wicket AND b.bowler_id IS NOT NULL AND b.player_out_id IS NOT NULL
    GROUP BY ALL"""


CHECKS = {
    "bowled_to.balls == legal-to-batter balls": (
        "SELECT sum(balls) FROM bowled_to",
        "SELECT sum((NOT is_wide)::INT) FROM gb WHERE bowler_id IS NOT NULL AND batter_id IS NOT NULL",
    ),
    "bowled_to.runs == batter runs": (
        "SELECT sum(runs) FROM bowled_to",
        "SELECT sum(runs_bat) FROM gb WHERE bowler_id IS NOT NULL AND batter_id IS NOT NULL",
    ),
    "dismissed.n == bowler wickets": (
        "SELECT sum(n) FROM dismissed",
        "SELECT count(*) FROM gb WHERE bowler_wicket AND bowler_id IS NOT NULL AND player_out_id IS NOT NULL",
    ),
    "bowled_to.dismissals == striker bowler wickets": (
        "SELECT sum(dismissals) FROM bowled_to",
        "SELECT count(*) FROM gb WHERE bowler_wicket AND player_out_id = batter_id AND bowler_id IS NOT NULL",
    ),
    "fielding_dismissal.n == cs_fielding rows on a wicket ball": (
        "SELECT coalesce(sum(n), 0) FROM fielding_dismissal",
        """SELECT count(*) FROM (SELECT DISTINCT f.match_id, f.inning, f.player_out_id, f.fielder_id
            FROM cb.cs_fielding f JOIN gb b ON b.match_id = f.match_id AND b.inning = f.inning
            AND b.player_out_id = f.player_out_id AND b.is_wicket WHERE f.fielder_id IS NOT NULL)""",
    ),
}


def build(
    archive: Path,
    out: Path,
    fielding_csv: Path | None = None,
    xi_csv: Path | None = None,
    register_csv: Path | None = None,
    log=print,
) -> dict:
    """Rebuild data/graph.duckdb from the archive. Returns the identity report and the parity checks."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    con = duckdb.connect(str(out))
    try:
        con.execute(f"ATTACH '{archive}' AS cb (READ_ONLY)")
        con.execute(INNINGS_SQL)
        load_xi(con, xi_csv, register_csv, log)
        con.execute(roster_sql(con))
        merge_negative_ids(con, log)
        build_people(con)
        resolve_fielding_csv(
            con, fielding_csv if fielding_csv and Path(fielding_csv).is_file() else None, log
        )
        con.execute(BALLS_VIEW)
        con.execute("CREATE OR REPLACE TABLE inn_meta AS SELECT * FROM inn")
        con.execute("""CREATE OR REPLACE TABLE season_years AS  -- archive season ('2007/08') -> IPL year (2008)
            SELECT season, mode(year(start_date)) AS year FROM cb.matches
            WHERE match_id IN (SELECT match_id FROM inn) GROUP BY 1""")
        for name, sql in EDGES.items():
            if name == "fielding_dismissal" and not has_table(con, "cb", "cs_fielding"):
                con.execute(
                    "CREATE TABLE fielding_dismissal (fielder_id BIGINT, batter_id BIGINT, bowler_id BIGINT, "
                    "season VARCHAR, team VARCHAR, kind VARCHAR, substitute BOOLEAN, n BIGINT)"
                )
                continue
            con.execute(f"CREATE OR REPLACE TABLE {name} AS {sql}")
            (n,) = con.execute(f"SELECT count(*) FROM {name}").fetchone() or (0,)
            log(f"  {name}: {n} edges")
        con.execute(
            wicket_detail_sql(has_table(con, "cb", "player_styles"), has_table(con, "cb", "ref_batting"))
        )
        # who kept wicket: a player with a stumping for that team in that season (Cricsheet has no keeper flag)
        con.execute("""CREATE OR REPLACE TABLE keepers AS
            SELECT DISTINCT fielder_id AS player_id, team, season FROM fielding_dismissal
            WHERE kind = 'stumped' AND NOT coalesce(substitute, false)""")
        report = {"identity": identity_report(con), "parity": graph_checks(con)}
        con.execute(
            "CREATE OR REPLACE TABLE build_report (section VARCHAR, check_name VARCHAR, value VARCHAR)"
        )
        con.executemany(
            "INSERT INTO build_report VALUES (?, ?, ?)",
            [("identity", k, str(v)) for k, v in report["identity"].items()]
            + [
                ("parity", k, f"{a} vs {b} {'ok' if ok else 'MISMATCH'}")
                for k, (a, b, ok) in report["parity"].items()
            ],
        )
    finally:
        con.close()
    return report


def graph_checks(con) -> dict[str, tuple[int, int, bool]]:
    out = {}
    for name, (edge_sql, base_sql) in CHECKS.items():
        if "cs_fielding" in base_sql and not has_table(con, "cb", "cs_fielding"):
            continue
        a = int(con.execute(edge_sql).fetchone()[0] or 0)
        b = int(con.execute(base_sql).fetchone()[0] or 0)
        out[name] = (a, b, a == b)
    if con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'wicket_detail' AND database_name = current_database()"
    ).fetchone()[0]:
        a = int(con.execute("SELECT sum(n) FROM wicket_detail").fetchone()[0] or 0)
        b = int(con.execute("SELECT sum(n) FROM dismissed").fetchone()[0] or 0)
        out["wicket_detail.n == dismissed.n"] = (a, b, a == b)
    if has_table(con, "cb", "player_styles"):  # every player: a batting hand and pace / spin / none
        (bad,) = con.execute(STYLES_CHECK).fetchone() or (0,)
        out["players without a batting hand or bowling type"] = (int(bad or 0), 0, not bad)
    if has_table(con, "cb", "ref_bowling"):  # an independent source: Cricinfo's scorecard
        (bad,) = con.execute(SCORECARD_CHECK).fetchone() or (0,)
        out["dismissed per bowler-match vs scorecard: mismatches"] = (int(bad or 0), 0, not bad)
    return out


STYLES_CHECK = """SELECT count(*) FROM people p LEFT JOIN cb.player_styles s USING (player_id)
    WHERE coalesce(s.bat_hand, '') = '' OR coalesce(s.bowl_kind, '') NOT IN ('pace', 'spin', 'none')"""

SCORECARD_CHECK = """WITH g AS (SELECT match_id, bowler_id, sum(n) w FROM (
        SELECT b.match_id, b.bowler_id, count(*) n FROM gb b WHERE b.bowler_wicket GROUP BY ALL) GROUP BY ALL),
    r AS (SELECT match_id, player_id, sum(wickets) w FROM cb.ref_bowling WHERE inning <= 2 GROUP BY ALL)
    SELECT count(*) FROM g JOIN r ON r.match_id = g.match_id AND r.player_id = g.bowler_id WHERE g.w <> r.w"""
