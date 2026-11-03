"""Canonical player identity across the three name sources.

The key is the Cricinfo player_id. ESPN commentary (`balls`, `ref_*`) already carries it; cricbot maps
Cricsheet names to it through Cricsheet's register, but players the register lacks get a stable *negative*
id; the ipl_fielding CSV has only the name written in commentary ("Rashid", "de Kock", "MSD").

Rules, in the spirit of entities.py:
- a name is only ever matched against the players of *that team in that match* (the roster), so "Sharma"
  means one person or is ambiguous, never a guess across 19 seasons;
- every resolution records a named method (exact, nickname, surname_initials, single_token, fuzzy), where
  exact means any spelling seen for that id in any source;
- two candidates at the best tier -> ambiguous, kept with no id (ask, don't guess);
- a negative Cricsheet id is merged into a positive id only when every match it appears in agrees.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz
from rapidfuzz.distance import DamerauLevenshtein

from cricrag.entities import NICKNAMES

PARTICLES = {"de", "du", "van", "der", "von", "da", "di", "le", "ul", "al", "ten"}
TIERS = ["exact", "nickname", "surname_initials", "single_token", "fuzzy"]  # best first
FUZZY_MIN = 88


def _clean(name: str | None) -> str:
    """Case kept: 'M.S. Dhoni ' -> 'M S Dhoni'; accents, punctuation and hyphens dropped."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s]", " ", s).replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def norm(name: str | None) -> str:
    """'M.S. Dhoni ' -> 'm s dhoni'."""
    return _clean(name).casefold()


@dataclass(frozen=True)
class Sig:
    """A name split into surname, full first names and initials ('MS Dhoni' -> dhoni, (), (m, s))."""

    full: str
    surname: str
    firsts: tuple[str, ...]
    initials: tuple[str, ...]

    @classmethod
    def of(cls, name: str) -> Sig:
        raw = _clean(name).split()
        toks = [t.casefold() for t in raw]
        if not toks:
            return cls("", "", (), ())
        cut = len(toks) - 1
        while cut > 0 and toks[cut - 1] in PARTICLES:  # 'de villiers', 'ul haq'
            cut -= 1
        surname, given = " ".join(toks[cut:]), toks[:cut]
        firsts: list[str] = []
        initials: list[str] = []
        for i, t in enumerate(given):
            orig = raw[i] if i < len(raw) else t
            if (orig.isupper() and len(t) <= 3) or len(t) == 1:  # Cricsheet initials: 'MS', 'AB', 'V'
                initials.extend(t)
            else:
                firsts.append(t)
                initials.append(t[0])
        return cls(" ".join(toks), surname, tuple(firsts), tuple(initials))


@dataclass
class Candidate:
    player_id: int
    names: set[str] = field(default_factory=set)  # every spelling seen for this id (all sources)

    def sigs(self) -> list[Sig]:
        return [Sig.of(n) for n in self.names]


@dataclass(frozen=True)
class Resolution:
    player_id: int | None
    method: str  # one of TIERS, or 'ambiguous' / 'unresolved' / 'unnamed'
    candidates: tuple[int, ...] = ()


def _initials_ok(m: Sig, c: Sig) -> bool:
    if not m.initials:
        return True
    if m.firsts and set(m.firsts) & set(c.firsts):
        return True
    return tuple(c.initials[: len(m.initials)]) == m.initials or m.initials[0] in c.initials


def _tier(mention: str, cand: Candidate) -> int | None:
    m = Sig.of(mention)
    if not m.full:
        return None
    sigs = cand.sigs()
    if any(s.full == m.full for s in sigs):
        return 0 if len(m.full.split()) > 1 else 3  # a one-word exact hit is still just a surname/first name
    nick = NICKNAMES.get(m.full)
    if nick and any(s.full == norm(nick) for s in sigs):
        return 1
    one = len(m.full.split()) == 1 or (m.full == m.surname and " " in m.surname)
    for s in sigs:
        if not one and s.surname == m.surname and _initials_ok(m, s):
            return 2
        if one and m.full in (s.surname, *s.firsts):
            return 3
    for s in sigs:
        if fuzz.token_set_ratio(m.full, s.full) >= FUZZY_MIN and (
            m.surname == s.surname or DamerauLevenshtein.distance(m.surname, s.surname) <= 1
        ):
            return 4
        if (
            len(m.surname) >= 5
            and DamerauLevenshtein.distance(m.surname, s.surname) == 1
            and _initials_ok(m, s)
        ):
            return 4
    return None


def resolve(mention: str | None, roster: list[Candidate]) -> Resolution:
    """Match one name against one team-in-a-match. Best tier wins; two players at that tier -> ambiguous."""
    if not mention or not norm(mention):
        return Resolution(None, "unnamed")
    best: dict[int, list[int]] = defaultdict(list)
    for c in roster:
        t = _tier(mention, c)
        if t is not None:
            best[t].append(c.player_id)
    if not best:
        return Resolution(None, "unresolved")
    t = min(best)
    ids: tuple[int, ...] = tuple(sorted(set(best[t])))
    if len(ids) > 1:
        return Resolution(None, "ambiguous", ids)
    return Resolution(next(iter(ids)), TIERS[t], ids)


# ---------------------------------------------------------------- archive side (DuckDB, read-only)


def has_table(con, schema: str, table: str) -> bool:
    q = "select count(*) from duckdb_tables() where database_name = ? and table_name = ?"
    return bool(con.execute(q, [schema, table]).fetchone()[0])


_TEAM = "replace({}, 'KXIP', 'PBKS')"
INNINGS_SQL = f"""
CREATE OR REPLACE TEMP TABLE inn AS
SELECT b.match_id, b.inning, any_value(m.season) AS season, any_value({_TEAM.format("b.batting_team")}) AS bat,
  any_value(CASE WHEN {_TEAM.format("m.team1")} = {_TEAM.format("b.batting_team")} THEN {_TEAM.format("m.team2")}
                 ELSE {_TEAM.format("m.team1")} END) AS bowl
FROM cb.balls b JOIN cb.matches m USING (match_id)
WHERE NOT coalesce(m.is_cancelled, false) AND b.inning <= 2
GROUP BY 1, 2
"""


def team_code(full: str) -> str | None:
    """'Kings XI Punjab' -> 'PBKS' (Cricsheet writes full franchise names)."""
    from cricrag.entities import TEAM_NAMES, canon

    code = TEAM_NAMES.get(full.casefold().strip())
    if code is None and full.casefold().rstrip("s") + "s" in TEAM_NAMES:  # 'Rising Pune Supergiant'
        code = TEAM_NAMES[full.casefold().rstrip("s") + "s"]
    return canon(code) if code else None


def export_xi(json_dir: Path, out_csv: Path) -> int:
    """Every Cricsheet playing XI (info.players + registry) -> one CSV. Run where the JSON lives."""
    rows = 0
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["match_id", "team", "cricsheet_id", "name"])
        for path in sorted(Path(json_dir).glob("*.json")):
            info = json.loads(path.read_text(encoding="utf-8")).get("info", {})
            people = (info.get("registry") or {}).get("people") or {}
            for team, names in (info.get("players") or {}).items():
                for name in names:
                    w.writerow([path.stem, team, people.get(name, ""), name])
                    rows += 1
    return rows


def load_xi(con, xi_csv: Path | None, register_csv: Path | None, log=print) -> int:
    """TEMP TABLE xi(match_id, team, player_id, name): the full XI, ids mapped like cricbot does
    (Cricinfo id from the register, else -int(cricsheet_id, 16)). Covers fielders who never batted or bowled."""
    con.execute(
        "CREATE OR REPLACE TEMP TABLE xi (match_id BIGINT, team VARCHAR, player_id BIGINT, name VARCHAR)"
    )
    if not xi_csv or not Path(xi_csv).is_file():
        return 0
    register: dict[str, int] = {}
    if register_csv and Path(register_csv).is_file():
        with open(register_csv, encoding="utf-8", newline="") as fh:
            register = {
                r["identifier"]: int(r["key_cricinfo"])
                for r in csv.DictReader(fh)
                if (r.get("key_cricinfo") or "").isdigit()
            }
    rows, skipped = [], Counter()
    with open(xi_csv, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            code, cid = team_code(r["team"]), r["cricsheet_id"]
            if not code or not cid or not r["match_id"].isdigit():
                skipped[r["team"] if not code else "no id"] += 1
                continue
            rows.append((int(r["match_id"]), code, register.get(cid) or -int(cid, 16), r["name"]))
    if rows:
        con.executemany("INSERT INTO xi VALUES (?, ?, ?, ?)", rows)
    log(
        f"  xi: {len(rows)} players from Cricsheet line-ups"
        + (f", skipped {dict(skipped)}" if skipped else "")
    )
    return len(rows)


def roster_sql(con) -> str:
    """Every (match, team, player_id, name, source) the archive can vouch for. Optional tables are skipped."""
    parts = [
        "SELECT match_id, team, player_id, name, 'cricsheet' FROM xi",
        "SELECT b.match_id, i.bat, b.batter_id, b.batter, 'espn' FROM cb.balls b JOIN inn i USING (match_id, inning)",
        "SELECT b.match_id, i.bowl, b.bowler_id, b.bowler, 'espn' FROM cb.balls b JOIN inn i USING (match_id, inning)",
        "SELECT b.match_id, i.bat, b.player_out_id, b.player_out, 'espn' FROM cb.balls b JOIN inn i USING (match_id, inning) WHERE b.is_wicket",
    ]
    if has_table(con, "cb", "cs_fielding"):
        parts.append(
            "SELECT f.match_id, i.bowl, f.fielder_id, f.fielder, 'cricsheet' FROM cb.cs_fielding f JOIN inn i USING (match_id, inning)"
        )
    if has_table(con, "cb", "cricsheet_balls"):
        for who, side in (("batter", "bat"), ("bowler", "bowl")):
            parts.append(
                f"SELECT c.match_id, i.{side}, c.{who}_id, c.{who}, 'cricsheet' FROM cb.cricsheet_balls c JOIN inn i USING (match_id, inning)"
            )
    if has_table(con, "cb", "cs_subs"):
        parts.append(
            f"SELECT match_id, {_TEAM.format('team')}, player_in_id, player_in, 'cricsheet' FROM cb.cs_subs"
        )
    for tbl, side in (("ref_batting", "bat"), ("ref_bowling", "bowl")):
        if has_table(con, "cb", tbl):
            parts.append(
                f"SELECT r.match_id, i.{side}, r.player_id, r.player, 'scorecard' FROM cb.{tbl} r JOIN inn i USING (match_id, inning)"
            )
    body = "\nUNION ALL\n".join(parts)
    return f"""CREATE OR REPLACE TABLE roster AS
SELECT match_id, team, player_id, name, source, count(*) AS n FROM ({body}) t(match_id, team, player_id, name, source)
WHERE player_id IS NOT NULL AND coalesce(name, '') <> '' AND match_id IN (SELECT match_id FROM inn)
GROUP BY ALL"""


def _rosters(con, positive_only: bool) -> dict[tuple[int, str], list[Candidate]]:
    names: dict[int, set[str]] = defaultdict(set)
    for pid, name in con.execute("SELECT DISTINCT player_id, name FROM roster").fetchall():
        names[pid].add(name)
    where = "WHERE player_id > 0" if positive_only else ""
    out: dict[tuple[int, str], list[Candidate]] = defaultdict(list)
    for mid, team, pid in con.execute(
        f"SELECT DISTINCT match_id, team, player_id FROM roster {where}"
    ).fetchall():
        out[(mid, team)].append(Candidate(pid, names[pid]))
    return out


def merge_negative_ids(con, log=print) -> int:
    """Map Cricsheet's unregistered (negative) ids onto Cricinfo ids, only when every match agrees."""
    rosters = _rosters(con, positive_only=True)
    votes: dict[int, Counter] = defaultdict(Counter)
    methods: dict[int, Counter] = defaultdict(Counter)
    rows = con.execute(
        "SELECT player_id, match_id, team, any_value(name) FROM roster WHERE player_id < 0 GROUP BY 1, 2, 3"
    ).fetchall()
    for neg, mid, team, name in rows:
        r = resolve(name, rosters.get((mid, team), []))
        votes[neg][r.player_id] += 1
        methods[neg][r.method] += 1
    merges = []
    for neg, v in votes.items():
        (to, n), total = v.most_common(1)[0], sum(v.values())
        if to is not None and n == total:
            merges.append((neg, to, methods[neg].most_common(1)[0][0], total))
    con.execute(
        "CREATE OR REPLACE TABLE id_merge (from_id BIGINT PRIMARY KEY, to_id BIGINT, method VARCHAR, matches INT)"
    )
    if merges:
        con.executemany("INSERT INTO id_merge VALUES (?, ?, ?, ?)", merges)
    con.execute("UPDATE roster SET player_id = m.to_id FROM id_merge m WHERE roster.player_id = m.from_id")
    log(f"  id_merge: {len(merges)} of {len(votes)} unregistered Cricsheet ids mapped to Cricinfo ids")
    return len(merges)


def build_people(con) -> None:
    """people: one row per canonical id, display name = most-used ESPN spelling (else any source)."""
    con.execute("""CREATE OR REPLACE TABLE player_alias AS
        SELECT player_id, name, source, sum(n) AS n FROM roster GROUP BY ALL""")
    con.execute("""CREATE OR REPLACE TABLE people AS
        SELECT player_id,
               arg_max(name, (source = 'espn')::INT * 1e9 + n) AS name,
               list(DISTINCT name ORDER BY name) AS aliases,
               count(DISTINCT match_id) AS matches,
               list(DISTINCT team ORDER BY team) AS teams
        FROM roster GROUP BY player_id""")


def resolve_fielding_csv(con, csv_path, log=print) -> None:
    """Attach a player_id to every audited fielder named in the ipl_fielding CSV (fielding side only)."""
    con.execute("""CREATE OR REPLACE TABLE fielding_mentions (match_id BIGINT, inning INT, over_label VARCHAR,
        label VARCHAR, confidence VARCHAR, fielder VARCHAR, runs_after_drop DOUBLE, team VARCHAR,
        player_id BIGINT, method VARCHAR, candidates BIGINT[])""")
    if not csv_path:
        return
    path = str(csv_path).replace("'", "''")
    rows = con.execute(f"""SELECT f.match_id::BIGINT, f.inning::INT, f.over_label, f.label, f.confidence,
            nullif(trim(f.fielder), ''), try_cast(f.runs_after_drop AS DOUBLE), i.bowl
        FROM read_csv('{path}', all_varchar = true, header = true) f
        JOIN inn i ON i.match_id = f.match_id::BIGINT AND i.inning = f.inning::INT
        WHERE f.label IN ('drop', 'misfield', 'overthrow') AND f.confidence NOT IN ('low', 'very_low')""").fetchall()
    rosters = _rosters(con, positive_only=True)
    out = []
    for mid, inn, ov, label, conf, fielder, after, team in rows:
        r = resolve(fielder, rosters.get((mid, team), []))
        out.append(
            (mid, inn, ov, label, conf, fielder, after, team, r.player_id, r.method, list(r.candidates))
        )
    if out:
        con.executemany("INSERT INTO fielding_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?)", out)
    by = Counter(r[9] for r in out)
    log(f"  fielding_mentions: {len(out)} rows, " + ", ".join(f"{k} {v}" for k, v in by.most_common()))


IDENTITY_CHECKS = {
    "espn_ball_ids_present": """SELECT round(100.0 * avg((batter_id IS NOT NULL AND bowler_id IS NOT NULL)::INT), 2)
        FROM cb.balls WHERE match_id IN (SELECT match_id FROM inn) AND inning <= 2""",
    "unregistered_ids_left": "SELECT count(DISTINCT player_id) FROM roster WHERE player_id < 0",
    "ids_with_conflicting_surnames": """SELECT count(*) FROM (SELECT player_id FROM player_alias
        WHERE source = 'espn' GROUP BY 1 HAVING count(DISTINCT lower(split_part(name, ' ', -1))) > 1)""",
    "same_surname_same_xi": """SELECT count(*) FROM (SELECT match_id, team, lower(split_part(p.name, ' ', -1)) s
        FROM (SELECT DISTINCT match_id, team, player_id FROM roster) r JOIN people p USING (player_id)
        GROUP BY ALL HAVING count(DISTINCT player_id) > 1)""",
    "fielding_mentions_resolved_pct": """SELECT round(100.0 * avg((player_id IS NOT NULL)::INT), 2)
        FROM fielding_mentions WHERE method <> 'unnamed'""",
    "fielding_mentions_ambiguous": "SELECT count(*) FROM fielding_mentions WHERE method = 'ambiguous'",
    "fielding_mentions_unresolved": "SELECT count(*) FROM fielding_mentions WHERE method = 'unresolved'",
}


def identity_report(con) -> dict[str, float | int | None]:
    return {k: con.execute(q).fetchone()[0] for k, q in IDENTITY_CHECKS.items()}
