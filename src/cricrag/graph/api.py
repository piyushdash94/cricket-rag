"""/api/graph/*: the Network tab's data. Read-only over data/graph.duckdb; every number comes from SQL."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
from fastapi import APIRouter, HTTPException, Query

from cricrag.graph import explore as gx
from cricrag.graph import tools as gt
from cricrag.styles import SPIN_TYPES


def router(db_path: Callable[[], Path]) -> APIRouter:
    r = APIRouter(prefix="/api/graph", tags=["graph"])
    lock = threading.Lock()
    state: dict[str, Any] = {}

    def con() -> duckdb.DuckDBPyConnection:
        path = db_path()
        if not Path(path).is_file():
            raise HTTPException(503, "The relational graph isn't built. Run `uv run cricrag build-graph`.")
        if state.get("path") != str(path):
            state["con"], state["path"] = duckdb.connect(str(path), read_only=True), str(path)
        return state["con"]

    def seasons(season: str | None, year: int | None = None) -> list[str] | None:
        """An archive season ('2007/08'), or the IPL year the front page uses (2008)."""
        if year is not None and not season:
            row = con().execute("SELECT season FROM season_years WHERE year = ?", [year]).fetchone()
            if not row:
                raise HTTPException(404, f"no IPL season {year} in the graph")
            return [row[0]]
        return [season] if season else None

    @r.get("/status")
    def status():
        with lock:
            c = con()
            report = c.execute("SELECT section, check_name, value FROM build_report").fetchall()
            tables = ("people", "bowled_to", "dismissed", "fielding_dismissal", "dropped", "played_for")
            counts = {t: (c.execute(f"SELECT count(*) FROM {t}").fetchone() or (0,))[0] for t in tables}
            return {"report": [dict(section=a, check=b, value=v) for a, b, v in report], "counts": counts}

    @r.get("/options")
    def options():
        with lock:
            c = con()
            return {
                "seasons": [
                    x for (x,) in c.execute("SELECT DISTINCT season FROM played_for ORDER BY 1").fetchall()
                ],
                "teams": [
                    x for (x,) in c.execute("SELECT DISTINCT team FROM played_for ORDER BY 1").fetchall()
                ],
            }

    @r.get("/search")
    def search(q: str = Query(min_length=2)):
        with lock:
            return [dict(id=i, name=n, matches=m) for i, n, m in gx.find_player(con(), q)]

    @r.get("/player/{pid}")
    def player(pid: int, season: str | None = None):
        """Ego network: who troubles them, whom they trouble, their catch partners, their teammates."""
        with lock:
            c = con()
            name = gt.name_of(c, pid)
            faced, bowled = gt.role_balls(c, pid)
            parts = [gt.nemesis(c, pid, seasons(season), top=8, min_dismissals=2)]
            if bowled >= 60:
                parts.append(gt.bunnies(c, pid, seasons(season), top=8, min_dismissals=2))
            parts.append(gt.catch_pairs(c, fielder=pid, seasons=seasons(season), top=5))
            if bowled >= 60:
                parts.append(gt.catch_pairs(c, bowler=pid, seasons=seasons(season), top=5))
            parts.append(gt.teammates(c, pid, seasons(season), top=6))
            return {"id": pid, "name": name, "faced": faced, "bowled": bowled,
                    "sections": [p.as_payload() | {"gap": p.gap, "lines": p.lines} for p in parts]}  # fmt: skip

    @r.get("/head-to-head")
    def head_to_head(a: int, b: int, season: str | None = None):
        with lock:
            ev = gt.head_to_head(con(), a, b, seasons(season))
            return ev.as_payload() | {"gap": ev.gap, "lines": ev.lines}

    @r.get("/fielding")
    def fielding(team: str, season: str | None = None):
        with lock:
            c = con()
            pairs = gt.catch_pairs(c, team=team, seasons=seasons(season), top=25)
            drops = gt.drops(c, team=team, seasons=seasons(season), top=25)
            return {"team": team, "season": season, "catches": pairs.as_payload() | {"gap": pairs.gap},
                    "drops": drops.as_payload() | {"gap": drops.gap}}  # fmt: skip

    @r.get("/rankings")
    def rankings(kind: str = "bowler", season: str | None = None, year: int | None = None, top: int = 15):
        if kind not in ("bowler", "batter"):
            raise HTTPException(400, "kind is bowler or batter")
        with lock:
            picked = seasons(season, year)
            rows = (gx.bowler_rank if kind == "bowler" else gx.batter_rank)(con(), picked, top=min(top, 50))
            least = gx.qualify(picked, None)
            note = (
                "Quality wickets: each wicket weighted by the batter's average in the same seasons, so a wicket "
                "of a batter averaging 40 counts twice one averaging 20"
                if kind == "bowler"
                else "Quality runs: each run weighted by how hard that bowler was to score off per wicket, "
                "against the league (runs off a miserly bowler count for more)"
            )
            return {"kind": kind, "season": picked[0] if picked else None, "min_balls": least,
                    "note": f"{note}. Min {least} balls.", "rows": rows}  # fmt: skip

    @r.get("/dismissals/{pid}")
    def dismissals(pid: int, season: str | None = None):
        """How a player gets out (by bowling type) and whom he dismisses (by batting order), each split by mode."""
        with lock:
            c = con()
            name = gt.name_of(c, pid)
            prof = gx.dismissal_profile(c, pid, seasons(season))

            def side(rows: list[tuple[str, str, int]], order: tuple[str, ...]) -> dict:
                groups: dict[str, dict] = {}
                for g, mode, n in rows:
                    e = groups.setdefault(g, {"group": g, "n": 0, "modes": {}})
                    e["n"] += n
                    e["modes"][mode] = e["modes"].get(mode, 0) + n
                ranked = sorted(
                    groups.values(), key=lambda e: order.index(e["group"]) if e["group"] in order else 99
                )
                return {"total": sum(e["n"] for e in ranked), "groups": ranked}

            return {"id": pid, "name": name, "season": season,
                    "batter": side(prof["batter"], ("pace", *SPIN_TYPES, "unknown")),
                    "bowler": side(prof["bowler"], gx.BANDS)}  # fmt: skip

    @r.get("/pairs")
    def pairs(season: str | None = None, year: int | None = None, top: int = 6):
        """The league's caught / stumped fielder-bowler pairs (the front page's catching web)."""
        with lock:
            picked = seasons(season, year)
            ev = gt.catch_pairs(con(), seasons=picked, top=min(top, 25))
            return ev.as_payload() | {
                "gap": ev.gap,
                "lines": ev.lines,
                "season": picked[0] if picked else None,
            }

    return r
