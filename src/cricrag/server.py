"""HTTP API for the UI (frontend/ui), one process: `uv run cricrag serve`.

    /api/agent/*                Pandit (this repo): status, pipeline graph, chat streamed as NDJSON
    /api/ipl/*, /api/data/*     cricbot's archive and match pages, loaded in-process from CRICBOT_ROOT
    /api/stats/*                cricbot's stats router, in-process
    /api/docs/catalog           what this API serves and the rules behind it

The chat stream sends one {"event": "trace"} line per pipeline step as Pandit takes it, then one
{"event": "result"} line. Numbers in replies still come only from SQL: this layer adds no facts, it only
reshapes the answer (stats tables, clarify options, which match to open) for the UI.
"""

from __future__ import annotations

import contextlib
import json
import queue
import re
import threading
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cricrag.config import settings

# ---------------------------------------------------------------- pipeline graph (the trace pane draws it)

NODES = [
    (
        "frame",
        "Question frame",
        "entity",
        "Link players, teams, seasons, stage, phase and over in code. No LLM.",
    ),
    (
        "checks",
        "Fast checks",
        "routing",
        "Ambiguous name -> ask. Other competitions and missing data -> say so.",
    ),
    (
        "intent",
        "Intent form",
        "model",
        "One small LLM call fills a fixed-choice form: answer, unit, measure, scope.",
    ),
    ("route", "Route table", "routing", "The form picks an ordered list of programs to try."),
    (
        "program",
        "Run program",
        "tool",
        "story, event, stats, match, counts, team phase, fielding, search or finder.",
    ),
    ("shape", "Shape check", "validation", "A result keyed by the wrong unit moves on to the next program."),
    ("write", "Writer", "model", "One LLM call writes up to five cited sentences from the evidence card."),
    (
        "validate",
        "Validator",
        "validation",
        "Reject uncited facts, numbers not in the evidence, unknown names.",
    ),
    ("finalize", "Finalize", "output", "Package the reply, tables, clarify options and UI actions."),
]
EDGES = [
    ("frame", "checks", None),
    ("checks", "finalize", "clarify / refuse / gap"),
    ("checks", "intent", "intent router"),
    ("checks", "route", "rules router"),
    ("intent", "route", None),
    ("route", "program", None),
    ("program", "shape", None),
    ("shape", "program", "wrong shape: next program"),
    ("shape", "write", "fits"),
    ("write", "validate", None),
    ("validate", "write", "problems: one retry"),
    ("validate", "finalize", None),
]
LABELS = {node: label for node, label, *_ in NODES}
KINDS = {node: kind for node, _, kind, _ in NODES}
PROGRAMS = {"story", "event", "search", "stats", "match", "leaderboard", "fielding", "team_phase", "finder"}


def graph() -> dict[str, Any]:
    return {
        "name": "Pandit",
        "version": "3",
        "framework": "cricket-rag programs",
        "nodes": [{"id": i, "label": label, "kind": k, "description": d} for i, label, k, d in NODES],
        "edges": [{"from": a, "to": b, **({"condition": c} if c else {})} for a, b, c in EDGES],
    }


def node_of(step: dict[str, Any]) -> str:
    """Which graph node a Pandit trace step belongs to."""
    name = step.get("step", "")
    if name in ("frame", "ui_context"):
        return "frame"
    if name == "route":
        return "checks" if step.get("rule") else "route"
    if name in ("intent", "intent_unavailable"):
        return "intent"
    if name == "try" and step.get("mismatch"):
        return "shape"
    if name == "validate":
        return "validate"
    if name == "llm_unavailable":
        return "write"
    return "program"  # try, stats, fallback, finder, and the program names themselves


def summary_of(step: dict[str, Any]) -> str:
    name = step.get("step", "")
    if name == "frame":
        parts = [
            f"{k}: {v}" for k, v in step.items() if k in ("players", "teams", "seasons", "stage", "phase")
        ]
        return "Linked " + "; ".join(parts) if parts else "No players, teams or seasons named."
    if name == "route":
        if step.get("rule"):
            return f"Rule {step['rule']}" + (f" ({step['term']})" if step.get("term") else "")
        return (
            f"{len(step.get('matches') or [])} candidate matches; events {step.get('event_types') or 'none'}"
        )
    if name == "intent":
        return (
            f"Form: {step.get('answer')} / {step.get('unit')} / {step.get('measure')} -> {step.get('routes')}"
        )
    if name == "try":
        return f"Try {step.get('program')}" + (
            f": rejected ({step['mismatch']})" if step.get("mismatch") else ""
        )
    if name == "validate":
        return "Passed" if not step.get("problems") else "Problems: " + "; ".join(step["problems"])
    if name == "ui_context":
        return f"Question refers to the open match: added {step.get('added')}"
    if name in PROGRAMS:
        return f"{name}: {step.get('evidence_lines', 0)} evidence lines"
    return ", ".join(f"{k}: {v}" for k, v in step.items() if k != "step")[:240]


class TraceStream:
    """Turns Pandit's trace steps into the UI's trace events and keeps the running snapshot."""

    def __init__(self) -> None:
        self.sequence = 0
        self.route = "-"
        self.tools: list[str] = []
        self.validation = "pending"
        self.model_used = False

    def event(self, step: dict[str, Any], node: str | None = None) -> dict[str, Any]:
        node = node or node_of(step)
        name = step.get("step", node)
        if name == "try" and step.get("program"):
            self.route = step["program"]
            self.tools.append(step["program"])
        elif name in PROGRAMS:
            self.route = name
            if name not in self.tools:
                self.tools.append(name)
        elif name == "route" and step.get("rule"):
            self.route = step["rule"]
        if name == "validate":
            self.validation = "failed" if step.get("problems") else "passed"
            self.model_used = True
        self.sequence += 1
        label = LABELS[node] + (f": {step['program']}" if name == "try" and step.get("program") else "")
        return {
            "sequence": self.sequence,
            "node": node,
            "label": label,
            "kind": KINDS[node],
            "summary": summary_of(step),
            "patch": step,
            "snapshot": {
                "phase": LABELS[node],
                "route": self.route,
                "tools": list(self.tools),
                "result_count": 0,
                "action_count": 0,
                "model_used": self.model_used,
                "validation": self.validation,
            },
        }


# ---------------------------------------------------------------- answer -> UI result

MATCH_REF = re.compile(r"\bmatch (\d+)\b")
THIS_MATCH = re.compile(
    r"\b(this|that) (match|game|chase|innings|final|contest|run chase)\b|\bthe (chase|last over|first over)\b"
    r"|\bhere\b",
    re.I,
)


def match_slugs(match_id: int, root: str | None) -> dict[str, str] | None:
    """The archive's slugs for a match id (cricbot reads the id off the end of the match slug)."""
    from cricrag.archive import connect

    try:
        with connect() as con:
            cur = con.execute("SELECT * FROM matches WHERE match_id = ?", [match_id])
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
    except Exception:
        return None
    if not row:
        return None
    m = dict(zip(cols, row, strict=True))
    season, series, path = m.get("season"), m.get("series_path"), m.get("match_path")
    if not series and root:
        try:
            series = archive(root)[0].series_slug_for(int(str(season)))
        except Exception:
            series = ""
    slug = re.sub(r"[^a-z0-9]+", "-", f"{m.get('team1')}-vs-{m.get('team2')}".lower()).strip("-")
    return {"series_slug": series or f"ipl-{season}", "match_slug": path or f"{slug}-{match_id}"}


def clarify_card(question: str, frame: dict[str, Any]) -> dict[str, Any] | None:
    amb = frame.get("ambiguous") or {}
    if not amb:
        return None
    token, cands = next(iter(amb.items()))
    pattern = re.compile(rf"\b{re.escape(token)}\b", re.I)
    return {
        "type": "clarify",
        "title": f"Which {token.title()}?",
        "name": token,
        "options": [
            {"label": c, "question": pattern.sub(c, question, count=1) if pattern.search(question) else c}
            for c in cands
        ],
    }


def stats_card(question: str, tables: list[dict[str, Any]], calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "stats",
        "title": question,
        "question": question,
        "blocks": tables,
        "calls": [
            {"name": c.get("name", ""), "args": c.get("args") or {}, "command": "", "error": None}
            for c in calls
        ],
    }


def result_of(ans, question: str, root: str | None) -> dict[str, Any]:
    cards: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    if ans.program == "stats" and ans.sources.tables:
        calls = next((s.get("calls") or [] for s in reversed(ans.trace) if s.get("calls")), [])
        cards.append(stats_card(question, ans.sources.tables, calls))
    if ans.mode == "clarify" and (card := clarify_card(question, ans.frame)):
        cards.append(card)
    # A story or single-match answer opens that match beside the chat.
    m1 = ans.sources.stats.get("M1", {})
    mid = None
    if ans.program in ("story", "match") and (found := MATCH_REF.search(m1.get("question", ""))):
        mid = int(found.group(1))
    elif ans.program == "event":  # every cited event is in one match: open it, so "this match" follows
        cited = {ans.sources.stats.get(t, {}).get("match_id") for t in re.findall(r"\[(E\d+)\]", ans.text)}
        mid = cited.pop() if len(cited) == 1 else None
    if mid and (slugs := match_slugs(int(mid), root)):
        actions.append({"type": "open_match", **slugs})
    return {
        "reply": ans.text,
        "cards": cards,
        "ui_actions": actions,
        "provider": "groq" if ans.model else None,
        "model": ans.model,
        "model_used": ans.mode == "llm",
        "program": ans.program,
        "mode": ans.mode,
        "problems": ans.problems,
        "seconds": ans.seconds,
    }


def with_match_context(question: str, ui: dict[str, Any]) -> tuple[str, str | None]:
    """ "Where did this match turn?" with a match open: name that match so the frame can find it."""
    teams = [t for t in ui.get("teams") or [] if t]
    if not (teams and ui.get("match_season") and THIS_MATCH.search(question)):
        return question, None
    label = ui.get("match_label") or ""
    added = f"{' vs '.join(teams)}, IPL {ui['match_season']}" + (
        f", {label}" if label and label != "Match" else ""
    )
    return f"{question} ({added})", added


# ---------------------------------------------------------------- app


class Turn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list)
    ui_context: dict[str, Any] = Field(default_factory=dict)


def enrich_match(detail: dict[str, Any], mid: int) -> dict[str, Any]:
    """Local commentary, winner-aware shifts and dropped catches on top of cricbot's match page."""
    import duckdb

    from cricrag.archive import connect
    from cricrag.matchpage import enrich

    s = settings()
    ev = duckdb.connect(str(s.events_db), read_only=True) if s.events_db.is_file() else None
    try:
        with connect() as con:
            return enrich(detail, con, ev, mid)
    finally:
        if ev is not None:
            ev.close()


def archive(root: str):
    from cricrag import cricbot

    return cricbot.archive_api(root)


def docs_catalog() -> dict[str, Any]:
    g = graph()
    return {
        "title": "cricket-rag API",
        "version": "1",
        "principles": [
            "Numbers come from SQL only. Retrieved commentary never supplies a number, a winner or a ranking.",
            "The LLM chooses; it does not extract. Entities, years and filters come from the code-built question frame.",
            "Answer the asked question, or name the gap. Never substitute a nearby question.",
            "Out-of-scope requests and unknown people get a clear reply and run no IPL query.",
            "A shared surname with no dominant player gets a clarifying question with options.",
            "Every reply passes the validator: citations exist, numbers are in the evidence, names are in the sources.",
            "ESPN commentary stays local: only short, cited snippets are shown.",
        ],
        "sources": [
            {
                "name": "cricbot.duckdb",
                "use": "Matches, deliveries, scorecards, verified stats queries",
                "mode": "read-only",
            },
            {
                "name": "events.duckdb",
                "use": "34k notable moments and momentum shifts (cricrag build-events)",
                "mode": "read-only",
            },
            {
                "name": "LanceDB",
                "use": "One chunk per over of commentary, BM25 with frame filters",
                "mode": "local index",
            },
            {
                "name": "Cricsheet",
                "use": "Ball-by-ball match pages via cricbot's archive modules",
                "mode": "local files",
            },
            {
                "name": "Groq",
                "use": "Intent form and writer; answers fall back to the evidence without it",
                "mode": "free tier",
            },
        ],
        "apis": [
            {
                "method": "POST",
                "path": "/api/agent/chat/stream",
                "use": "Ask Pandit; trace lines then a result line",
                "request": "{ message, history, ui_context }",
                "response": "NDJSON { event: trace | result, data }",
            },
            {
                "method": "GET",
                "path": "/api/agent/status",
                "use": "Which LLM is configured",
                "response": "{ status, provider, model, providers }",
            },
            {
                "method": "GET",
                "path": "/api/agent/graph",
                "use": "Pandit's pipeline as nodes and edges",
                "response": "{ nodes, edges }",
            },
            {
                "method": "GET",
                "path": "/api/ipl/matches",
                "use": "Archive list for a season, searchable",
                "response": "{ matches, pagination, source }",
            },
            {
                "method": "GET",
                "path": "/api/ipl/matches/{series_slug}/{match_slug}",
                "use": "Match page: scorecard, deliveries, partnerships, shifts",
                "response": "{ match, innings, balls, partnerships, analytics, ... }",
            },
            {
                "method": "GET",
                "path": "/api/stats/*",
                "use": "cricbot's stats workspace queries",
                "response": "{ blocks }",
            },
        ],
        "tools": [
            {
                "name": "story",
                "owner": "program",
                "use": "A match's facts sheet, momentum shifts and key events",
            },
            {
                "name": "event",
                "owner": "program",
                "use": "SQL over the events table: wickets, partnerships, drops, hat-tricks",
            },
            {"name": "stats", "owner": "program", "use": "cricbot's verified queries, run in-process"},
            {
                "name": "search",
                "owner": "program",
                "use": "BM25 over one-over commentary chunks with the frame's filters",
            },
            {
                "name": "finder",
                "owner": "program",
                "use": "One guarded read-only SELECT written by the LLM over documented views",
            },
        ],
        "states": [
            {
                "name": "frame",
                "meaning": "Players, teams, seasons, stage, phase, innings and over linked by code",
            },
            {"name": "intent", "meaning": "The fixed-choice form: answer, unit, measure, scope"},
            {
                "name": "sources",
                "meaning": "Tagged evidence: [E#] events, [S#] stats, [M#] match facts, [C#] commentary",
            },
            {
                "name": "problems",
                "meaning": "What the validator rejected; a failed reply falls back to the evidence",
            },
        ],
        "nodes": g["nodes"],
        "transitions": g["edges"],
        "coverage": [
            {"level": "cricsheet", "meaning": "Every delivery from the local Cricsheet archive"},
            {"level": "full", "meaning": "ESPN play-by-play returned every delivery with commentary text"},
            {
                "level": "commentary",
                "meaning": "Deliveries reconstructed from historical commentary; may be partial",
            },
            {"level": "overs-only", "meaning": "Only over aggregates are available"},
            {"level": "unavailable", "meaning": "No delivery-level data; the scorecard remains usable"},
        ],
    }


def create_app(make_agent: Callable[[], Any] | None = None) -> FastAPI:
    """`make_agent` builds the Pandit to use (tests pass a fake); the default is the CLI's, built on first use."""
    s = settings()
    root = str(s.cricbot_root) if s.cricbot_root else None
    app = FastAPI(title="cricket-rag API", version="1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    state: dict[str, Any] = {}
    lock = threading.Lock()  # Pandit holds DuckDB connections; one question at a time

    def agent():
        with lock:
            if "agent" not in state:
                if make_agent:
                    state["agent"] = make_agent()
                else:
                    from cricrag.cli import pandit

                    state["agent"] = pandit()[0]
            return state["agent"]

    def cricbot_archive():
        if not root:
            raise HTTPException(503, "Set CRICBOT_ROOT to your cricbot checkout (see .env.example).")
        try:
            return archive(root)
        except Exception as exc:  # missing checkout or a cricbot import error
            raise HTTPException(503, f"cricbot archive unavailable: {exc}") from exc

    if root:
        with contextlib.suppress(Exception):  # the archive routes report the error
            app.include_router(archive(root)[2])  # /api/stats/*

    @app.get("/api/health")
    def health():
        return {"status": "ok", "cricbot_root": bool(root)}

    @app.get("/api/ipl/seasons")
    def seasons():
        ipl, cs, _ = cricbot_archive()
        archived = set(cs.seasons())
        return {
            "seasons": [
                {
                    "year": y,
                    "name": f"IPL {y}",
                    "slug": ipl.series_slug_for(y),
                    "source": "Cricsheet" if y in archived else "ESPNcricinfo",
                }
                for y in ipl.all_seasons()
            ]
        }

    @app.get("/api/data/status")
    def data_status():
        _, cs, _ = cricbot_archive()
        entries = cs.entries()
        return {
            "cricsheet": {
                "matches": len(entries),
                "seasons": cs.seasons(),
                "synced": bool(entries),
                "sync_command": "python -m backend.src.cricsheet sync  (in the cricbot checkout)",
            },
            "canonical_source": "Cricsheet" if entries else "ESPNcricinfo (Cricsheet not synced)",
        }

    @app.get("/api/ipl/matches")
    def matches(season: int = 2025, q: str = "", limit: int = 24, offset: int = 0):
        ipl, _, _ = cricbot_archive()
        try:
            return ipl.list_ipl_matches(season=season, query=q, limit=limit, offset=offset)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/ipl/matches/{series_slug}/{match_slug}")
    def match_detail(series_slug: str, match_slug: str):
        detail = cricbot_archive()[0].get_match_detail(series_slug, match_slug)
        if found := re.search(r"(\d+)$", match_slug):
            with contextlib.suppress(Exception):  # cricbot's page still shows without the extras
                enrich_match(detail, int(found.group(1)))
        return detail

    @app.get("/api/home")
    def home(season: int = 2025):
        """Front page: one season's final, caps, totals and closest finishes, plus every season's scoring."""
        key = f"home-{season}"
        if key not in state:
            from cricrag.archive import connect
            from cricrag.matchpage import eras, season_home

            try:
                with connect() as con:
                    page = season_home(con, season)
                    if "eras" not in state:
                        state["eras"] = eras(con)
            except Exception as exc:
                raise HTTPException(503, f"archive unavailable: {exc}") from exc
            for item in [page.get("final"), page.get("highest"), *page.get("thrillers", [])]:
                if item and (slugs := match_slugs(int(item["match_id"]), root)):
                    item.update(slugs)
            state[key] = page
        return {**state[key], "eras": state["eras"]}

    @app.get("/api/news")
    def news():
        from cricrag.news import articles

        return articles(s.data_dir / "cache" / "news.json")

    @app.get("/api/hall-of-fame")
    def hall():
        if "hall" not in state:
            from cricrag.archive import connect
            from cricrag.matchpage import hall_of_fame

            try:
                with connect() as con:
                    state["hall"] = hall_of_fame(con)
            except Exception as exc:
                raise HTTPException(503, f"archive unavailable: {exc}") from exc
        return state["hall"]

    @app.get("/api/docs/catalog")
    def catalog():
        return docs_catalog()

    @app.get("/api/agent/graph")
    def agent_graph():
        return graph()

    @app.get("/api/agent/status")
    def agent_status():
        models = s.groq_models
        ready = bool(s.groq_api_key)
        return {
            "status": "ready" if ready else "offline",
            "provider": "groq" if ready else None,
            "model": models[0] if ready and models else None,
            "providers": [{"name": "groq", "model": ",".join(models), "configured": ready, "ready": ready}],
        }

    @app.post("/api/agent/chat/stream")
    def chat_stream(request: ChatRequest):
        steps: queue.Queue = queue.Queue()
        tracer = TraceStream()
        history = [t.model_dump() for t in request.history]
        question, added = with_match_context(request.message.strip(), request.ui_context)

        def trace(step: dict[str, Any], node: str | None = None) -> None:
            steps.put(("trace", tracer.event(step, node)))

        def reply(text: str) -> None:
            steps.put(("result", {"reply": text, "cards": [], "ui_actions": [], "provider": None,
                                  "model": None, "model_used": False}))  # fmt: skip

        def work():
            try:
                pandit = agent()
            except Exception as exc:
                reply(f"Pandit isn't ready: {str(exc)[:200]}. Check CRICBOT_ROOT in .env, then run "
                      "`uv run cricrag build-index` and `uv run cricrag build-events`.")  # fmt: skip
                steps.put(None)
                return
            try:
                if added:
                    trace({"step": "ui_context", "rule": "deictic_match", "added": added})
                with lock:
                    ans = pandit.ask(question, history, on_step=trace)
                result = result_of(ans, request.message.strip(), root)
                tracer.model_used = bool(ans.model)
                trace({"step": "finalize", "program": ans.program, "mode": ans.mode}, "finalize")
                steps.put(("result", result))
            except Exception as exc:  # report it in the chat instead of dropping the stream
                reply(f"Pandit hit an error: {str(exc)[:200]}")
            steps.put(None)

        threading.Thread(target=work, daemon=True).start()

        def lines() -> Iterator[str]:
            while (item := steps.get()) is not None:
                yield json.dumps({"event": item[0], "data": item[1]}, ensure_ascii=False, default=str) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    return app
