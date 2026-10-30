"""Command line: build the index, search, ask the agent, run the evaluations."""

from __future__ import annotations

import json
import time
from pathlib import Path

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


@app.command()
def ask(question: str, show_trace: bool = typer.Option(False, "--trace")):
    """Ask Pandit: routes to story / event / stats / search, then one LLM call writes the answer."""
    agent, llm = pandit()
    ans = agent.ask(question)
    console.print(f"\n{ans.text}\n")
    console.print(
        f"[dim]program={ans.program} mode={ans.mode} model={ans.model} {ans.seconds}s tokens={llm.usage}"
    )
    if ans.problems:
        console.print(f"[yellow]validator: {ans.problems}")
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
