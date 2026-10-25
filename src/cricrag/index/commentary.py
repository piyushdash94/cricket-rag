"""Build the commentary index: one chunk per over, with a header computed by SQL.

Why one over: a single ball is ~110 characters, too short for useful embeddings or BM25 length
normalisation; an over (~6 balls, ~700 characters) is a natural unit people ask about.

The header carries only SQL-derived facts (teams, stage, over runs, the score after the over). The
agent may quote numbers from headers but never from commentary text, which contains mid-innings
scores and speeds that would pass a naive number check.
"""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa

from cricrag.archive import STAGE_SQL
from cricrag.entities import canon
from cricrag.retrieval.models import Embedder
from cricrag.text import bold_spans, strip_html

TABLE = "commentary"

BALLS_SQL = f"""
WITH c AS (
  SELECT b.*,
         sum(b.runs_total) OVER w AS score,
         sum(CASE WHEN b.is_wicket THEN 1 ELSE 0 END) OVER w AS wkts
  FROM balls b
  WINDOW w AS (PARTITION BY b.match_id, b.inning ORDER BY b.sequence
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
SELECT c.match_id, m.season, year(m.start_date) AS year, m.title, CAST(m.start_date AS VARCHAR) AS date,
       m.venue, m.team1, m.team2, {STAGE_SQL} AS stage, c.inning, c.over_number, c.over_label,
       c.batting_team, c.batter, c.non_striker, c.bowler, c.runs_total, c.is_wicket, c.is_four, c.is_six,
       c.player_out, c.dismissal_type, c.short_text, c.commentary, c.score, c.wkts
FROM c JOIN matches m USING (match_id)
WHERE NOT coalesce(m.is_cancelled, false)
ORDER BY c.match_id, c.inning, c.sequence
"""
COLS = [
    "match_id",
    "season",
    "year",
    "title",
    "date",
    "venue",
    "team1",
    "team2",
    "stage",
    "inning",
    "over",
    "over_label",
    "batting_team",
    "batter",
    "non_striker",
    "bowler",
    "runs",
    "is_wicket",
    "is_four",
    "is_six",
    "player_out",
    "dismissal",
    "short_text",
    "commentary",
    "score",
    "wkts",
]


def phase_of(over: int) -> str:
    return "powerplay" if over <= 6 else "middle" if over <= 15 else "death"


@dataclass
class FieldingEvent:
    label: str  # drop | misfield | overthrow
    fielder: str
    confidence: str


def load_fielding(path: Path | None) -> dict[tuple[int, int, str], list[FieldingEvent]]:
    """Events from the ipl_fielding package, keyed by (match, inning, over_label). Optional."""
    out: dict[tuple[int, int, str], list[FieldingEvent]] = defaultdict(list)
    if not path or not Path(path).is_file():
        return out
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                key = (int(r["match_id"]), int(r["inning"]), str(r["over_label"]))
            except (KeyError, ValueError):
                continue
            out[key].append(
                FieldingEvent(r.get("label", ""), r.get("fielder", "") or "", r.get("confidence", "") or "")
            )
    return out


def build_chunks(rows: Iterable[tuple], fielding: dict | None = None) -> list[dict[str, Any]]:
    fielding = fielding or {}
    overs: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        d = dict(zip(COLS, row, strict=True))
        overs[(d["match_id"], d["inning"], d["over"])].append(d)
    return [_chunk(balls, fielding) for balls in overs.values()]


def _chunk(balls: list[dict[str, Any]], fielding: dict) -> dict[str, Any]:
    first, last = balls[0], balls[-1]
    over, inning = int(first["over"]), int(first["inning"])
    batting, t1, t2 = canon(first["batting_team"]), canon(first["team1"]), canon(first["team2"])
    bowling = t2 if batting == t1 else t1
    bowlers = list(dict.fromkeys(b["bowler"] for b in balls if b["bowler"]))
    batters = list(dict.fromkeys(n for b in balls for n in (b["batter"], b["non_striker"]) if n))
    runs = sum(int(b["runs"] or 0) for b in balls)
    wickets = sum(1 for b in balls if b["is_wicket"])
    events: set[str] = set()
    fielders: list[str] = []
    lines, bold = [], []
    for b in balls:
        if b["is_wicket"]:
            events.add("wicket")
        if b["is_six"]:
            events.add("six")
        if b["is_four"]:
            events.add("four")
        spans = bold_spans(b["commentary"])
        if spans:
            events.add("bold")
            bold += spans
        note = ""
        for ev in fielding.get((int(b["match_id"]), inning, str(b["over_label"])), []):
            events.add(ev.label)
            if ev.fielder:
                fielders.append(ev.fielder)
            note += f" [fielding: {ev.label}{' by ' + ev.fielder if ev.fielder else ''}]"
        lines.append(
            f"{b['over_label']} {strip_html(b['short_text'])}. {strip_html(b['commentary'])}{note}".strip()
        )
    stage = first["stage"]
    title = first["title"] or ""
    header = (
        f"IPL {first['season']} {title} ({stage}): {first['team1']} v {first['team2']} at {first['venue']}, "
        f"{first['date']}. {batting} innings {inning}, over {over} ({phase_of(over)}), "
        f"bowling {', '.join(bowlers)}. This over: {runs} runs, {wickets} wicket{'s' if wickets != 1 else ''}. "
        f"Score after the over: {batting} {last['score']}/{last['wkts']}."
    )
    body = "\n".join(lines)
    players = list(dict.fromkeys(batters + bowlers + fielders))
    return {
        "id": f"{first['match_id']}:{inning}:{over}",
        "match_id": int(first["match_id"]),
        "season": str(first["season"]),
        "year": int(first["year"] or 0),
        "title": title,
        "date": str(first["date"]),
        "stage": stage,
        "venue": first["venue"] or "",
        "team1": t1,
        "team2": t2,
        "batting_team": batting,
        "bowling_team": bowling,
        "inning": inning,
        "over": over,
        "phase": phase_of(over),
        "bowlers": "|" + "|".join(bowlers) + "|",
        "players": "|" + "|".join(players) + "|",
        "events": "|" + "|".join(sorted(events)) + "|",
        "runs": runs,
        "wickets": wickets,
        "score": f"{last['score']}/{last['wkts']}",
        "ball_labels": " ".join(str(b["over_label"]) for b in balls),
        "header": header,
        "body": body,
        "bold": " | ".join(bold),
        "text": header + "\n" + body,
    }


def embed_with_cache(chunks: list[dict[str, Any]], emb: Embedder, cache_dir: Path, log=print) -> np.ndarray:
    """Vectors cached by text hash, so a rebuild only embeds chunks whose text changed."""
    import pyarrow.parquet as pq

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"vectors_{emb.name.replace('/', '_')}.parquet"
    cache: dict[str, np.ndarray] = {}
    if path.is_file():
        t = pq.read_table(path)
        cache = dict(
            zip(t["key"].to_pylist(), np.asarray(t["vector"].to_pylist(), dtype=np.float32), strict=True)
        )
    keys = [hashlib.sha1(c["text"].encode()).hexdigest() for c in chunks]
    todo = [i for i, k in enumerate(keys) if k not in cache]
    log(f"vectors: {len(chunks) - len(todo)} cached, {len(todo)} to embed")
    step = 2048
    for s in range(0, len(todo), step):
        idx = todo[s : s + step]
        vecs = emb.passages([chunks[i]["text"] for i in idx])
        for i, v in zip(idx, vecs, strict=True):
            cache[keys[i]] = v
        log(f"  embedded {min(s + step, len(todo))}/{len(todo)}")
        if (s // step) % 8 == 7 or s + step >= len(todo):  # checkpoint so an interrupted build resumes
            pq.write_table(
                pa.table({"key": list(cache), "vector": [v.tolist() for v in cache.values()]}), path
            )
    return np.stack([cache[k] for k in keys])


def write_table(db_dir: Path, chunks: list[dict[str, Any]], vectors: np.ndarray):
    import lancedb
    from lancedb.index import FTS, Bitmap, BTree

    db = lancedb.connect(str(db_dir))
    data = pa.table(
        {
            **{k: [c[k] for c in chunks] for k in chunks[0]},
            "vector": pa.array(vectors.tolist(), type=pa.list_(pa.float32(), vectors.shape[1])),
        }
    )
    tbl = db.create_table(TABLE, data, mode="overwrite")
    tbl.create_index("text", config=FTS(with_position=False, stem=True, remove_stop_words=True), replace=True)
    # Scalar indexes make prefilters cheap: BTree for high-cardinality ids, Bitmap for small enums.
    for col, cfg in (
        ("match_id", BTree()),
        ("season", Bitmap()),
        ("stage", Bitmap()),
        ("phase", Bitmap()),
        ("batting_team", Bitmap()),
        ("bowling_team", Bitmap()),
        ("inning", Bitmap()),
    ):
        tbl.create_index(col, config=cfg, replace=True)
    return tbl


def build(
    con: duckdb.DuckDBPyConnection,
    emb: Embedder,
    db_dir: Path,
    cache_dir: Path,
    fielding_csv: Path | None = None,
    limit_matches: int | None = None,
    log=print,
):
    sql = BALLS_SQL
    if limit_matches:
        sql = sql.replace(
            "WHERE NOT",
            f"WHERE c.match_id IN (SELECT match_id FROM matches ORDER BY start_date DESC LIMIT {int(limit_matches)}) AND NOT",
        )
    rows = con.sql(sql).fetchall()
    log(f"balls: {len(rows)}")
    chunks = build_chunks(rows, load_fielding(fielding_csv))
    log(f"chunks: {len(chunks)}")
    vectors = embed_with_cache(chunks, emb, cache_dir, log=log)
    return write_table(db_dir, chunks, vectors), len(chunks)
