"""Command line: build the index, search, ask the agent, run the evaluations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cricrag.config import settings

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _retriever(rerank: bool = True):
    from cricrag.retrieval.hybrid import Retriever, open_table
    from cricrag.retrieval.models import default_embedder, default_reranker

    return Retriever(
        open_table(settings().lance_dir), default_embedder(), default_reranker() if rerank else None
    )


@app.command("build-index")
def build_index(
    limit_matches: int = typer.Option(None, help="only the N most recent matches (quick trial)"),
    fielding: bool = typer.Option(True, help="join ipl_fielding events when FIELDING_EVENTS is set"),
):
    """Chunk every over, embed (cached), write LanceDB with FTS and scalar indexes."""
    from cricrag.archive import connect
    from cricrag.index.commentary import build
    from cricrag.retrieval.models import default_embedder

    s = settings()
    t = time.time()
    with connect() as con:
        _, n = build(
            con,
            default_embedder(),
            s.lance_dir,
            s.cache_dir,
            s.fielding_events if fielding else None,
            limit_matches,
            log=console.print,
        )
    console.print(f"[green]indexed {n} overs in {time.time() - t:.0f}s -> {s.lance_dir}")


@app.command()
def frame(question: str):
    """Show the deterministic frame for a question."""
    from cricrag.frame import build_frame, find_matches, load_catalog

    f = build_frame(question, load_catalog())
    console.print_json(json.dumps(f.as_dict()))
    console.print(find_matches(f))


@app.command()
def search(query: str, mode: str = "hybrid_rerank", k: int = 5, filters: bool = True):
    """Retrieve overs for a query (frame filters on by default)."""
    from cricrag.frame import build_frame, load_catalog
    from cricrag.retrieval.hybrid import Filters

    f = Filters.from_frame(build_frame(query, load_catalog())) if filters else Filters()
    res = _retriever(mode == "hybrid_rerank").search(query, f, k=k, mode=mode)  # type: ignore[arg-type]
    console.print(f"[dim]where: {res.where}  relaxed: {res.relaxed}  ms: {res.ms}")
    for h in res.hits:
        console.print(
            f"[bold]{h.id}[/] score={h.score:.4f} ranks={h.ranks}\n  {h.header}\n  {h.body[:300]}\n"
        )


@app.command("build-events")
def build_events():
    """Build data/events.duckdb: wickets, milestones, partnerships, hauls, maidens, drops, finishes, shifts."""
    from cricrag.events import build

    s = settings()
    t = time.time()
    n = build(s.db_path, s.events_db, s.fielding_events, log=console.print)
    console.print(f"[green]{n} events in {time.time() - t:.0f}s")


@app.command("fill-styles")
def fill_styles():
    """Give every player a batting hand and a bowling type (pace, spin, or none) in the archive's player_styles."""
    from cricrag.styles import fill

    report = fill(settings().db_path)
    console.print(report)
    if report["no_batting_hand"] or report["no_bowling_type"]:
        raise typer.Exit(1)


@app.command("build-graph")
def build_graph():
    """Build data/graph.duckdb: canonical player ids, then player-to-player edge tables, with parity checks."""
    from cricrag.graph.build import build

    s = settings()
    t = time.time()
    report = build(s.db_path, s.graph_db, s.fielding_events, s.xi_csv, s.register_csv, log=console.print)
    console.print("[bold]identity[/]")
    for k, v in report["identity"].items():
        console.print(f"  {k}: {v}")
    console.print("[bold]parity (edge table vs archive)[/]")
    bad = 0
    for k, (a, b, ok) in report["parity"].items():
        bad += not ok
        console.print(f"  {'[green]ok[/]' if ok else '[red]MISMATCH[/]'} {k}: {a} vs {b}")
    console.print(f"[{'green' if not bad else 'red'}]graph built in {time.time() - t:.0f}s -> {s.graph_db}")
    if bad:
        raise typer.Exit(1)


@app.command("export-xi")
def export_xi(
    json_dir: Annotated[
        Path | None, typer.Option(help="Cricsheet IPL JSON folder (default: cricbot's data/cricsheet/ipl)")
    ] = None,
):
    """Write data/cricsheet_xi.csv (every playing XI) so build-graph knows fielders who never batted or bowled."""
    from cricrag.graph.identity import export_xi as export

    s = settings()
    src = json_dir or s.db_path.parent / "cricsheet" / "ipl"
    s.xi_csv.parent.mkdir(parents=True, exist_ok=True)
    n = export(src, s.xi_csv)
    console.print(f"[green]{n} XI rows from {src} -> {s.xi_csv}")


@app.command("graph-explore")
def graph_explore(
    batter: str = typer.Option(None, help="show this batter's nemesis list"),
    season: Annotated[list[str] | None, typer.Option(help="limit to archive seasons, e.g. 2024")] = None,
    top: int = 10,
):
    """NetworkX views over the graph: bowler/batter PageRank, fielder-bowler pairs, teammate communities."""
    import duckdb

    from cricrag.graph import explore as gx

    with duckdb.connect(str(settings().graph_db), read_only=True) as con:
        if batter:
            hits = gx.find_player(con, batter)
            if not hits:
                raise typer.BadParameter(f"no player matches {batter!r}")
            pid, name, _ = hits[0]
            console.print(f"[bold]nemesis: {name}[/] (dismissals, balls, runs)")
            for row in gx.nemesis(con, pid, seasons=season or None)[:top]:
                console.print(f"  {row}")
            return
        for title, rows in (
            ("bowler PageRank (x1000, wickets)", gx.bowler_rank(con, season or None, top=top)),
            ("batter PageRank (x1000, runs)", gx.batter_rank(con, season or None, top=top)),
            ("fielder-bowler pairs (c/st X b Y)", gx.fielder_bowler(con, season or None, top=top)),
        ):
            console.print(f"[bold]{title}[/]")
            for r in rows:
                console.print(f"  {r}")
        console.print("[bold]teammate communities (top 8 names each)[/]")
        for c in gx.communities(con, season or None)[:6]:
            console.print(f"  {len(c):3} players: {', '.join(c[:8])}")


def pandit():
    import duckdb

    from cricrag.agent.llm import GroqClient
    from cricrag.agent.pandit import Pandit
    from cricrag.frame import load_catalog

    s = settings()
    llm = GroqClient(
        s.groq_api_key, s.groq_models, s.cache_dir / "llm" if s.llm_cache else None, max_wait=s.groq_max_wait
    )
    edb = duckdb.connect(str(s.events_db), read_only=True) if s.events_db.is_file() else None
    small = GroqClient(
        s.groq_api_key,
        s.groq_intent_models,
        s.cache_dir / "llm" if s.llm_cache else None,
        max_wait=s.groq_max_wait,
    )
    root = str(s.cricbot_root) if s.cricbot_root else None
    return Pandit(
        _retriever(rerank=False), load_catalog(), llm, edb, root, intent_llm=small, router=s.router
    ), llm


def agent():
    """The configured agent: PanditGraph (relational branch + Pandit, the default) or Pandit alone."""
    s = settings()
    if s.agent != "graph":
        return pandit()
    from cricrag.agent.graph_agent import PanditGraph
    from cricrag.agent.llm import GroqClient
    from cricrag.frame import load_catalog

    llm = GroqClient(
        s.groq_api_key, s.groq_models, s.cache_dir / "llm" if s.llm_cache else None, max_wait=s.groq_max_wait
    )
    return PanditGraph(load_catalog(), s.graph_db, llm, pandit_factory=lambda: pandit()[0]), llm


@app.command()
def ask(question: str, show_trace: bool = typer.Option(False, "--trace")):
    """Ask the agent: relational questions go to the graph; story / event / stats / search go to Pandit."""
    agent_, llm = agent()
    ans = agent_.ask(question)
    ans = agent.ask(question)
    console.print(f"\n{ans.text}\n")
    console.print(
        f"[dim]program={ans.program} mode={ans.mode} model={ans.model} {ans.seconds}s tokens={llm.usage}"
    )
    if ans.problems:
        console.print(f"[yellow]validator: {ans.problems}")
    if ans.extra.get("graph"):
        g = ans.extra["graph"]
        console.print(f"[dim]graph: {g['tool']} - {len(g['nodes'])} nodes, {len(g['edges'])} edges")
    if show_trace:
        console.print_json(json.dumps(ans.trace, default=str))


@app.command()
def story(question: str):
    """Facts and momentum shifts for the match a question names (SQL only, no LLM)."""
    from cricrag.archive import connect
    from cricrag.frame import build_frame, find_matches, load_catalog
    from cricrag.story import load_drops, match_story

    matches = find_matches(build_frame(question, load_catalog()))
    if len(matches) != 1:
        console.print(f"[yellow]{len(matches)} candidate matches; name season, teams or stage more precisely")
        console.print([(m["match_id"], m["title"], m["date"]) for m in matches])
        return
    with connect() as con:
        st = match_story(con, matches[0]["match_id"], load_drops(settings().fielding_events) or None)
    console.print("\n".join(st["facts"]))
    for sh in st["shifts"]:
        console.print(f"{'* ' if sh['decisive'] else '- '}{sh['line']}  overs={sh['over_ids']}")


@app.command("eval-pandit")
def eval_pandit(suite: str = "v3"):
    """End to end: --suite v3 (held-out benchmark) or events (SQL-gold moments + finals for review)."""
    from cricrag.eval import pandit_eval as pe

    agent, llm = pandit()
    out = settings().data_dir / "eval"
    out.mkdir(parents=True, exist_ok=True)
    if suite == "v3":
        rows = pe.run_v3(
            agent, Path(settings().cricbot_root) / "benchmark" / "v3_new_questions" / "v3_new_questions.jsonl"
        )
        table = pe.report(rows, ("behaviour", "capability", "program", "mode"))
    else:
        rows = pe.run_events(agent, pe.build_events(agent.edb))
        table = pe.report(rows, ("kind", "program", "mode"))
    stamp = time.strftime("%Y%m%d-%H%M")
    (out / f"pandit_{suite}_{stamp}.json").write_text(
        json.dumps({"summary": table, "usage": llm.usage, "rows": rows}, indent=1, default=str),
        encoding="utf-8",
    )
    console.print(table)
    console.print(f"[dim]tokens={llm.usage} -> {out / f'pandit_{suite}_{stamp}.json'}")


@app.command("eval-routes")
def eval_routes(mode: str = "plan", pause: float = 6.0, only: str = ""):
    """Route accuracy on data/eval/routing_labels.jsonl: --mode plan (form + route table) or full (ask)."""
    from cricrag.eval import routes as rt

    agent, _ = pandit()
    s = settings()
    items = rt.load(Path(__file__).resolve().parents[2] / "evals" / "routing_labels.jsonl",
                    Path(s.cricbot_root) / "benchmark" / "v3_new_questions" / "v3_new_questions.jsonl")  # fmt: skip
    if only:
        items = [x for x in items if x["set"] in only.split(",")]
    rows = rt.run(agent, items, mode, pause)
    table = rt.report(rows)
    stamp = time.strftime("%Y%m%d-%H%M")
    path = s.data_dir / "eval" / f"routes_{mode}_{stamp}.json"
    path.write_text(
        json.dumps({"summary": table, "usage": agent.intent_llm.usage, "rows": rows}, indent=1), "utf-8"
    )
    console.print(table)
    console.print(f"[dim]intent tokens={agent.intent_llm.usage} -> {path}")


@app.command("eval-retrieval")
def eval_retrieval(
    n: int = 120,
    split: str = "test",
    modes: str = "bm25,dense,hybrid,hybrid_w,hybrid_rerank",
    rebuild_set: bool = False,
):
    """Compare retrieval modes on archive-generated questions; writes data/eval/retrieval_*.{jsonl,md}."""
    from cricrag.archive import connect
    from cricrag.eval import retrieval as ev
    from cricrag.frame import load_catalog

    s = settings()
    set_path = s.data_dir / "eval" / "retrieval_set_v2.jsonl"
    if rebuild_set or not set_path.is_file():
        with connect() as con:
            ev.save(set_path, ev.build_set(con, s.fielding_events, n))
    items = ev.load(set_path)
    results = ev.evaluate(items, _retriever(), load_catalog(), modes.split(","), [False, True])
    stamp = time.strftime("%Y%m%d-%H%M")
    out = s.data_dir / "eval"
    (out / f"retrieval_results_{stamp}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in results), encoding="utf-8"
    )
    md = "\n\n".join(
        f"### split: {sp}\n\n" + ev.to_markdown(ev.summarise(results, sp)) for sp in ("dev", "test")
    )
    (out / f"retrieval_{stamp}.md").write_text(md, encoding="utf-8")
    console.print(ev.to_markdown(ev.summarise(results, split)))
    console.print(f"[green]saved {out / f'retrieval_{stamp}.md'}")


@app.command()
def news(live_only: bool = typer.Option(False, "--live", help="only the live ESPN feed")):
    """IPL reading: live stories from ESPN's feed, plus the hand-checked ESPNcricinfo reading list."""
    from cricrag.news import articles

    got = articles(settings().data_dir / "cache" / "news.json")
    rows = got["live"] + ([] if live_only else got["reading"])
    for a in rows:
        console.print(f"[bold]{a['kind']:8}[/bold] {a['title']}\n         {a['url']}")
    if not got["live"]:
        console.print("[dim]No IPL stories in ESPN's live feed right now.[/dim]")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """The UI's API (frontend/ui): Pandit plus cricbot's archive, match pages and stats, in one process."""
    import uvicorn

    uvicorn.run("cricrag.server:create_app", factory=True, host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
