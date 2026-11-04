"""Every IPL player gets a batting hand and a bowling type: pace, spin, or none (does not bowl).

Bowling types are two classes only. Pace is every speed (fast, fast-medium, medium-fast, medium);
spin is every kind of slow bowling. The arm (left/right) is kept beside it.

cricbot's player_styles comes from ESPN scorecards and leaves two gaps, both filled here:
  - players ESPN never listed (they appear only in a Cricsheet XI) or listed without a bowling style
    although they bowled: styles_manual.csv, one sourced row each;
  - players ESPN lists with no bowling style who never bowled a ball in the archive: 'none'.

    cricrag fill-styles          # writes player_styles in the archive; re-running is a no-op
"""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb

MANUAL = Path(__file__).with_name("styles_manual.csv")
NO_BOWLING = "does not bowl"
SPIN_WORDS = (
    "offbreak",
    "off break",
    "off-break",
    "legbreak",
    "leg break",
    "googly",
    "orthodox",
    "wrist",
    "chinaman",
    "slow",
)
PACE_WORDS = ("fast", "medium", "seam")

# Spin type from ESPN's style: the archive has exactly five spin styles, each mapping to one type.
# Finger spin = off-spin and left-arm orthodox; wrist spin = leg-spin and left-arm wrist-spin.
SPIN_TYPES = ("off-spin", "leg-spin", "left-arm orthodox", "left-arm wrist-spin")


def spin_type_sql(s: str = "s") -> str:
    """SQL for the spin type of a player_styles row aliased `s` (NULL for pace and non-bowlers)."""
    st = f"lower({s}.bowl_style)"
    return f"""CASE WHEN {s}.bowl_kind <> 'spin' THEN NULL
        WHEN {st} LIKE '%wrist%' OR {st} LIKE '%chinaman%' THEN 'left-arm wrist-spin'
        WHEN {st} LIKE '%orthodox%' THEN 'left-arm orthodox'
        WHEN {st} LIKE '%leg%' OR {st} LIKE '%googly%' THEN 'leg-spin'
        WHEN {st} LIKE '%off%' THEN 'off-spin' END"""


# Anyone with a delivery, a scorecard line or a catch is a player; officials in player_styles are not.
PLAYERS = """SELECT batter_id AS id FROM balls UNION SELECT bowler_id FROM balls
    UNION SELECT player_id FROM cs_batting_card UNION SELECT player_id FROM cs_bowling_card"""


def classify(style: str) -> tuple[str, str, str]:
    """(bowl_style, bowl_kind, bowl_arm) for an ESPN-style description; '' means it does not bowl."""
    s = style.strip().lower()
    if not s or s == NO_BOWLING:
        return NO_BOWLING, "none", ""
    kind = "spin" if any(w in s for w in SPIN_WORDS) else "pace" if any(w in s for w in PACE_WORDS) else ""
    arm = "left" if "left" in s else "right" if "right" in s else ""
    return s, kind, arm


def manual_rows(path: Path = MANUAL) -> list[tuple]:
    with path.open(encoding="utf-8", newline="") as f:
        return [
            (
                int(r["player_id"]),
                r["name"],
                r["bat_hand"].strip().lower(),
                *classify(r["bowl_style"]),
                r["source"],
            )
            for r in csv.DictReader(f)
        ]


def fill(db: Path | str, manual: Path = MANUAL) -> dict[str, int | list[str]]:
    """Apply the manual rows and mark non-bowlers. Returns what changed and anything still missing."""
    rows = manual_rows(manual)
    con = duckdb.connect(str(db))
    try:
        con.execute("BEGIN")
        con.executemany("DELETE FROM player_styles WHERE player_id = ?", [[r[0]] for r in rows])
        con.executemany("INSERT INTO player_styles VALUES (?, ?, ?, ?, ?, ?, ?)", [list(r) for r in rows])
        (none,) = con.execute(
            f"""UPDATE player_styles SET bowl_style = ?, bowl_kind = 'none', bowl_arm = ''
            WHERE coalesce(bowl_kind, '') = '' AND player_id IN ({PLAYERS})
              AND player_id NOT IN (SELECT bowler_id FROM balls WHERE bowler_id IS NOT NULL)""",
            [NO_BOWLING],
        ).fetchone() or (0,)
        con.execute("COMMIT")
        return {"manual": len(rows), "marked_does_not_bowl": int(none or 0), **missing(con)}
    finally:
        con.close()


def missing(con) -> dict[str, list[str]]:
    """Players (as PLAYERS defines them) still without a batting hand or a bowling type."""
    q = f"""SELECT coalesce(s.name, p.id::VARCHAR) FROM ({PLAYERS}) p LEFT JOIN player_styles s ON s.player_id = p.id
        WHERE p.id IS NOT NULL AND {{}} ORDER BY 1"""
    return {
        "no_batting_hand": [r[0] for r in con.execute(q.format("coalesce(s.bat_hand, '') = ''")).fetchall()],
        "no_bowling_type": [
            r[0]
            for r in con.execute(
                q.format("coalesce(s.bowl_kind, '') NOT IN ('pace', 'spin', 'none')")
            ).fetchall()
        ],
    }
