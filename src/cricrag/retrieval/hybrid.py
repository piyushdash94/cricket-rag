"""Hybrid retrieval over the commentary table.

    frame filters (SQL prefilter)  ->  BM25 (LanceDB native FTS)  ||  dense (bge-small, cosine, exact)
                                   ->  reciprocal rank fusion      ->  optional cross-encoder rerank

Dense search is exact (no ANN index): after prefiltering there are at most ~50k 384-d vectors, which a
flat scan handles in milliseconds. Every stage is switchable so the eval can compare them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from cricrag.entities import codes
from cricrag.frame import Frame
from cricrag.retrieval.models import Embedder, Reranker
from cricrag.text import fts_query

Mode = Literal[
    "bm25", "dense", "hybrid", "hybrid_w", "hybrid_rerank"
]  # hybrid_w: dense weighted by dense_weight
RRF_K = 60


@dataclass
class Filters:
    seasons: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)
    match_ids: list[int] = field(default_factory=list)
    stage: str | None = None
    phase: str | None = None
    inning: int | None = None
    over: int | None = None
    players: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    role: str | None = None  # "batting" / "bowling": which side the frame's team is on

    @classmethod
    def from_frame(cls, f: Frame, match_ids: list[int] | None = None) -> Filters:
        return cls(
            seasons=f.seasons,
            teams=f.teams,
            match_ids=match_ids or [],
            stage=f.stage,
            phase=f.phase,
            inning=f.inning,
            over=f.over,
            players=f.players,
        )

    def where(self, with_players: bool = True) -> str | None:
        q = _quote
        parts: list[str] = []
        if self.match_ids:
            parts.append(f"match_id IN ({','.join(str(int(m)) for m in self.match_ids)})")
        else:
            if self.seasons:
                parts.append(f"season IN ({','.join(q(s) for s in self.seasons)})")
            if self.stage:
                parts.append(f"stage = {q(self.stage)}")
            for t in self.teams[:2]:
                alias = ",".join(q(c) for c in codes(t))
                if self.role in ("batting", "bowling"):
                    parts.append(f"{self.role}_team IN ({alias})")
                else:
                    parts.append(f"(team1 IN ({alias}) OR team2 IN ({alias}))")
        if self.phase:
            parts.append(f"phase = {q(self.phase)}")
        if self.inning:
            parts.append(f"inning = {int(self.inning)}")
        if self.over:
            parts.append(f"over = {int(self.over)}")
        if with_players and self.players:
            parts.append("(" + " OR ".join(f"players LIKE {q('%|' + p + '|%')}" for p in self.players) + ")")
        for e in self.events:
            parts.append(f"events LIKE {q('%|' + e + '|%')}")
        return " AND ".join(parts) or None


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class Hit:
    id: str
    row: dict[str, Any]
    score: float
    ranks: dict[str, float] = field(default_factory=dict)

    @property
    def header(self) -> str:
        return self.row["header"]

    @property
    def body(self) -> str:
        return self.row["body"]


@dataclass
class Result:
    hits: list[Hit]
    mode: str
    where: str | None
    relaxed: bool = False
    ms: dict[str, float] = field(default_factory=dict)


COLUMNS = [
    "id",
    "match_id",
    "season",
    "title",
    "date",
    "stage",
    "venue",
    "team1",
    "team2",
    "batting_team",
    "bowling_team",
    "inning",
    "over",
    "phase",
    "players",
    "events",
    "runs",
    "wickets",
    "score",
    "ball_labels",
    "header",
    "body",
    "bold",
]


class Retriever:
    def __init__(
        self, table, embedder: Embedder, reranker: Reranker | None = None, dense_weight: float = 0.5
    ):
        self.tbl, self.emb, self.rr, self.dense_weight = table, embedder, reranker, dense_weight

    def by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        """Fetch overs by id, no similarity search: used when SQL already chose them."""
        if not ids:
            return []
        where = f"id IN ({','.join(_quote(i) for i in ids)})"
        rows = self.tbl.search().where(where).select(COLUMNS).limit(len(ids)).to_list()
        return sorted(rows, key=lambda r: ids.index(r["id"]))

    def search(
        self,
        query: str,
        filters: Filters | None = None,
        k: int = 6,
        mode: Mode = "hybrid_rerank",
        pool: int = 50,
        rerank_top: int = 15,
        min_hits: int | None = None,
    ) -> Result:
        """Search; if the player filter leaves fewer than `min_hits` rows, retry without it (soft filter)."""
        filters = filters or Filters()
        res = self._search(query, filters, k, mode, pool, rerank_top, with_players=True)
        need = k if min_hits is None else min_hits
        if filters.players and len(res.hits) < need:
            relaxed = self._search(
                query + " " + " ".join(filters.players),
                filters,
                k,
                mode,
                pool,
                rerank_top,
                with_players=False,
            )
            seen = {h.id for h in res.hits}
            res.hits += [h for h in relaxed.hits if h.id not in seen][: k - len(res.hits)]
            res.relaxed = True
        return res

    def _search(
        self, query: str, f: Filters, k: int, mode: Mode, pool: int, rerank_top: int, with_players: bool
    ) -> Result:
        where = f.where(with_players=with_players)
        ms: dict[str, float] = {}
        lists: dict[str, list[dict[str, Any]]] = {}
        if mode != "dense":
            t = time.perf_counter()
            text = fts_query(query)
            lists["bm25"] = (
                self._run(self.tbl.search(text, query_type="fts"), where, pool, "_score") if text else []
            )
            ms["bm25"] = _ms(t)
        if mode != "bm25":
            t = time.perf_counter()
            vec = self.emb.query(query)
            lists["dense"] = self._run(
                self.tbl.search(vec.tolist(), vector_column_name="vector").distance_type("cosine"),
                where,
                pool,
                "_distance",
            )
            ms["dense"] = _ms(t)
        hits = rrf(lists, weights={"dense": 1.0 if mode == "hybrid" else self.dense_weight})
        if mode == "hybrid_rerank" and self.rr and hits:
            t = time.perf_counter()
            top = hits[:rerank_top]
            scores = self.rr.scores(
                query, [f"{h.row['batting_team']} innings, over {h.row['over']}: {h.body[:600]}" for h in top]
            )
            for h, s in zip(top, scores, strict=True):
                h.ranks["rerank_score"] = s
                h.score = s
            hits = sorted(top, key=lambda h: -h.score) + hits[rerank_top:]
            ms["rerank"] = _ms(t)
        return Result(hits=hits[:k], mode=mode, where=where, ms=ms)

    @staticmethod
    def _run(q, where: str | None, limit: int, score_col: str) -> list[dict[str, Any]]:
        if where:
            q = q.where(where, prefilter=True)
        return q.select([*COLUMNS, "text", score_col]).limit(limit).to_list()


def rrf(
    lists: dict[str, list[dict[str, Any]]], k: int = RRF_K, weights: dict[str, float] | None = None
) -> list[Hit]:
    """Weighted reciprocal rank fusion: rank-based, so BM25 and cosine scales never need comparing."""
    by_id: dict[str, Hit] = {}
    for name, rows in lists.items():
        w = (weights or {}).get(name, 1.0)
        for rank, row in enumerate(rows, 1):
            hit = by_id.setdefault(row["id"], Hit(row["id"], row, 0.0))
            hit.score += w / (k + rank)
            hit.ranks[name] = rank
    return sorted(by_id.values(), key=lambda h: -h.score)


def _ms(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 1)


def open_table(db_dir):
    import lancedb

    from cricrag.index.commentary import TABLE

    return lancedb.connect(str(db_dir)).open_table(TABLE)
