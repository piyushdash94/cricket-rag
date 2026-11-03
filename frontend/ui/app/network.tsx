"use client";

// Network tab: the relational graph (data/graph.duckdb) drawn as small, readable pictures.
// Rivalry = one player's ego network (radial), Fielding web = catches / drops between a team's fielders and
// bowlers (two columns), Rankings = network PageRank (labelled a heuristic), Communities = teammate clusters.
// Every number shown comes from /api/graph/*, which reads SQL-built edge tables.

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export type GraphNode = { id: number; label: string; role: string; center?: boolean };
export type GraphEdge = { source: number; target: number; kind: string; [k: string]: string | number | undefined };
export type GraphPayload = { tool: string; title: string; nodes: GraphNode[]; edges: GraphEdge[]; rows: Record<string, unknown>[]; gap?: string | null; lines?: string[] };
export type GraphCard = GraphPayload & { type: "graph" };

type Tab = "rivalry" | "fielding" | "rankings" | "communities" | "checks";
const TABS: { id: Tab; label: string }[] = [
  { id: "rivalry", label: "Rivalry" }, { id: "fielding", label: "Fielding web" }, { id: "rankings", label: "Rankings" },
  { id: "communities", label: "Communities" }, { id: "checks", label: "Build checks" },
];
const ROLE_COLOR: Record<string, string> = { batter: "var(--accent)", bowler: "var(--red)", fielder: "var(--amber)", player: "var(--accent)" };

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail ?? `HTTP ${r.status}`);
  return r.json();
}

/** Edge weight shown as line width: the count the edge is about. */
function weight(e: GraphEdge): number {
  return Number(e.dismissals ?? e.drops ?? e.seasons ?? e.balls ?? 1) || 1;
}
/** Short label drawn on the edge; the full numbers are in its tooltip and the list under the picture. */
function edgeLabel(e: GraphEdge): string {
  if (e.kind === "dismissed" || e.kind === "bowled_to") return `${e.dismissals} out`;
  if (e.kind === "caught_off") return `${e.dismissals} c/st`;
  if (e.kind === "dropped_off") return `${e.drops} drop${e.drops === 1 ? "" : "s"}`;
  if (e.kind === "teammates") return e.season ? `${e.team} ${e.season}` : `${e.seasons} yrs`;
  return "";
}
function edgeTitle(e: GraphEdge, name: (id: number) => string): string {
  const base = `${name(e.source)} → ${name(e.target)}`;
  if (e.kind === "dismissed" || e.kind === "bowled_to") return `${base}: ${e.dismissals} dismissals, ${e.runs} runs off ${e.balls} balls`;
  return `${base}: ${edgeLabel(e)}`;
}

/** Radial ego network: the centre player in the middle, neighbours on a ring, width = weight. */
export function GraphView({ nodes, edges, height = 300, chain = false }: { nodes: GraphNode[]; edges: GraphEdge[]; height?: number; chain?: boolean }) {
  const W = 460, H = height, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 46;
  const center = chain ? undefined : nodes.find((n) => n.center) ?? (nodes.length === 2 ? undefined : nodes[0]);
  const ring = nodes.filter((n) => n !== center);
  const pos = new Map<number, { x: number; y: number }>();
  if (center) pos.set(center.id, { x: cx, y: cy });
  if (chain) nodes.forEach((n, i) => pos.set(n.id, { x: 50 + (i * (W - 100)) / Math.max(nodes.length - 1, 1), y: cy + (i % 2 ? -30 : 30) }));
  else if (!center && ring.length === 2) { pos.set(ring[0].id, { x: cx - R, y: cy }); pos.set(ring[1].id, { x: cx + R, y: cy }); }
  else ring.forEach((n, i) => { const a = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(ring.length, 1); pos.set(n.id, { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a) }); });
  const max = Math.max(1, ...edges.map(weight));
  const name = (id: number) => nodes.find((n) => n.id === id)?.label ?? String(id);
  return <svg className="net-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Relationship graph">
    {edges.map((e, i) => {
      const a = pos.get(e.source), b = pos.get(e.target);
      if (!a || !b) return null;
      const w = 1.5 + 6 * (weight(e) / max);
      // label at the middle of each spoke: short labels on separate rays never collide
      const outer = center && e.source === center.id ? b : center && e.target === center.id ? a : null;
      const inner = outer === a ? b : a;
      const lx = outer ? inner.x + 0.5 * (outer.x - inner.x) : (a.x + b.x) / 2;
      const ly = outer ? inner.y + 0.5 * (outer.y - inner.y) : (a.y + b.y) / 2;
      return <g key={i}>
        <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={w} className={`net-edge ${e.kind}`}><title>{edgeTitle(e, name)}</title></line>
        <text x={lx} y={ly + 3} className="net-edge-label">{edgeLabel(e)}</text>
      </g>;
    })}
    {nodes.map((n) => { const p = pos.get(n.id); if (!p) return null; const big = n === center;
      return <g key={n.id} transform={`translate(${p.x},${p.y})`}>
        <circle r={big ? 13 : 8} fill={ROLE_COLOR[n.role] ?? "var(--accent)"} className="net-node" />
        <text y={big ? 28 : 21} className={`net-label${big ? " center" : ""}`}>{n.label}</text>
      </g>; })}
  </svg>;
}

/** Two columns: fielders (left) to bowlers (right); width = dismissals (or drops). */
function Bipartite({ edges, nodes, metric }: { edges: GraphEdge[]; nodes: GraphNode[]; metric: "dismissals" | "drops" }) {
  const left = [...new Set(edges.map((e) => e.source))], right = [...new Set(edges.map((e) => e.target))];
  const row = 26, H = Math.max(left.length, right.length) * row + 30, W = 460, lx = 150, rx = 310;
  const name = (id: number) => nodes.find((n) => n.id === id)?.label ?? String(id);
  const y = (list: number[], id: number) => 22 + list.indexOf(id) * row;
  const max = Math.max(1, ...edges.map((e) => Number(e[metric] ?? 0)));
  return <svg className="net-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Fielder to bowler web">
    <text x={lx} y={10} className="net-col">FIELDER</text><text x={rx} y={10} className="net-col right">BOWLER</text>
    {edges.map((e, i) => { const y1 = y(left, e.source), y2 = y(right, e.target); const v = Number(e[metric] ?? 0);
      return <path key={i} d={`M${lx + 6},${y1} C${(lx + rx) / 2},${y1} ${(lx + rx) / 2},${y2} ${rx - 6},${y2}`}
        strokeWidth={1 + 7 * (v / max)} className={`net-edge ${metric === "drops" ? "dropped_off" : "caught_off"}`}><title>{`${name(e.source)} → ${name(e.target)}: ${v}`}</title></path>; })}
    {left.map((id) => <g key={`l${id}`}><circle cx={lx} cy={y(left, id)} r={5} fill="var(--amber)" /><text x={lx - 10} y={y(left, id) + 4} className="net-label left">{name(id)}</text></g>)}
    {right.map((id) => <g key={`r${id}`}><circle cx={rx} cy={y(right, id)} r={5} fill="var(--red)" /><text x={rx + 10} y={y(right, id) + 4} className="net-label right">{name(id)}</text></g>)}
  </svg>;
}

function Lines({ lines }: { lines?: string[] }) {
  if (!lines?.length) return null;
  return <ul className="net-lines">{lines.map((l) => <li key={l}>{l.replace(/\s*\[G\d+\]$/, "")}</li>)}</ul>;
}

/** The chat card for a relational answer: the subgraph behind it, plus a way into the Network tab. */
export function GraphChatCard({ card, onOpen }: { card: GraphCard; onOpen: () => void }) {
  return <article className="chat-card net-card">
    <header><div><p className="section-kicker">RELATIONAL GRAPH · {card.tool.replace(/_/g, " ")}</p><h3>{card.title}</h3></div></header>
    {card.tool === "catch_pairs" || card.tool === "drops"
      ? <Bipartite edges={card.edges} nodes={card.nodes} metric={card.tool === "drops" ? "drops" : "dismissals"} />
      : <GraphView nodes={card.nodes} edges={card.edges} height={320} chain={card.tool === "connection"} />}
    <footer><small>Edge widths are SQL counts from the archive.</small><div><button onClick={onOpen}>Open Network</button></div></footer>
  </article>;
}

type Options = { seasons: string[]; teams: string[] };
type Hit = { id: number; name: string; matches: number };
const findPlayers = (q: string) => get<Hit[]>(`/api/graph/search?q=${encodeURIComponent(q)}`).catch(() => [] as Hit[]);
type PlayerNet = { id: number; name: string; faced: number; bowled: number; sections: GraphPayload[] };

export function NetworkPanel({ onClose, onAsk }: { onClose: () => void; onAsk: (q: string) => void }) {
  const [tab, setTab] = useState<Tab>("rivalry");
  const [opts, setOpts] = useState<Options | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [season, setSeason] = useState("");
  const [query, setQuery] = useState("Virat Kohli");
  const [hits, setHits] = useState<Hit[]>([]);
  const [player, setPlayer] = useState<PlayerNet | null>(null);
  const [team, setTeam] = useState("CSK");
  const [metric, setMetric] = useState<"dismissals" | "drops">("dismissals");
  const [field, setField] = useState<{ catches: GraphPayload; drops: GraphPayload } | null>(null);
  const [kind, setKind] = useState<"bowler" | "batter">("bowler");
  const [ranks, setRanks] = useState<{ note: string; rows: Record<string, string | number>[] } | null>(null);
  const [groups, setGroups] = useState<{ size: number; players: string[] }[] | null>(null);
  const [checks, setChecks] = useState<{ report: { section: string; check: string; value: string }[]; counts: Record<string, number> } | null>(null);
  const sq = season ? `season=${encodeURIComponent(season)}` : "";

  const [pid, setPid] = useState<number | null>(null);

  useEffect(() => {
    get<Options>("/api/graph/options")
      .then((o) => { setOpts(o); return findPlayers("Virat Kohli"); })
      .then((found) => { setHits(found); setPid(found[0]?.id ?? null); })
      .catch((e) => setError(String(e.message ?? e)));
  }, []);
  useEffect(() => {
    if (pid !== null) get<PlayerNet>(`/api/graph/player/${pid}?${sq}`).then(setPlayer).catch((e) => setError(String(e.message ?? e)));
  }, [pid, sq]);
  const search = useCallback(async (event?: FormEvent) => {
    event?.preventDefault();
    if (query.trim().length < 2) return;
    const found = await findPlayers(query.trim());
    setHits(found);
    if (found[0]) setPid(found[0].id);
  }, [query]);

  useEffect(() => { if (tab === "fielding") get<{ catches: GraphPayload; drops: GraphPayload }>(`/api/graph/fielding?team=${team}&${sq}`).then(setField).catch(() => setField(null)); }, [tab, team, sq]);
  useEffect(() => { if (tab === "rankings") get<{ note: string; rows: Record<string, string | number>[] }>(`/api/graph/rankings?kind=${kind}&${sq}`).then(setRanks).catch(() => setRanks(null)); }, [tab, kind, sq]);
  useEffect(() => { if (tab === "communities") get<{ groups: { size: number; players: string[] }[] }>(`/api/graph/communities?${sq}`).then((r) => setGroups(r.groups)).catch(() => setGroups(null)); }, [tab, sq]);
  useEffect(() => { if (tab === "checks") get<typeof checks>("/api/graph/status").then(setChecks).catch(() => setChecks(null)); }, [tab]);

  const web = field ? (metric === "drops" ? field.drops : field.catches) : null;
  const rankMax = useMemo(() => Math.max(1, ...(ranks?.rows ?? []).map((r) => Number(r.score))), [ranks]);

  if (error && !opts) {
    return <div className="stats-panel"><div className="stats-hero"><div className="hero-actions"><button className="back-link" onClick={onClose}>← Close</button></div></div>
      <div className="workspace-empty"><span>◎</span><h3>Relational graph not built yet</h3><p>Run this once in the project root, then reload:</p>
        <code className="stats-command">uv run cricrag build-graph</code><p className="stats-note">{error}</p></div></div>;
  }

  return <div className="stats-panel">
    <div className="stats-hero">
      <div className="hero-actions"><button className="back-link" onClick={onClose}>← Close</button></div>
      <p className="section-kicker">RELATIONAL GRAPH</p>
      <h2>Who plays whom</h2>
      <div className="stats-year">
        <label htmlFor="net-season">Season</label>
        <select id="net-season" value={season} onChange={(e) => setSeason(e.target.value)}>
          <option value="">All seasons</option>
          {(opts?.seasons ?? []).slice().reverse().map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
    </div>
    <div className="detail-tabs" role="tablist" aria-label="Network sections">{TABS.map((t) =>
      <button role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""} key={t.id} onClick={() => setTab(t.id)}>{t.label}</button>)}
    </div>
    <div className="workspace-body net-body">
      {tab === "rivalry" && <>
        <form className="net-search" onSubmit={search}>
          <input aria-label="Player" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Player name" />
          <button>Show</button>
          {hits.length > 1 && <select aria-label="Pick a player" value={pid ?? ""} onChange={(e) => setPid(Number(e.target.value))}>
            {hits.map((h) => <option key={h.id} value={h.id}>{h.name} ({h.matches} matches)</option>)}</select>}
        </form>
        {player && <>
          <p className="stats-note">{player.name}: {player.faced.toLocaleString()} balls faced, {player.bowled.toLocaleString()} bowled in this archive.</p>
          {player.sections.map((sec) => <section className="chat-card net-section" key={sec.title}>
            <header><h3>{sec.title}</h3><button className="net-ask" onClick={() => onAsk(askFor(sec, player.name, season))}>Ask Pandit</button></header>
            {sec.gap ? <p className="stats-note">{sec.gap}</p> : <>
              {sec.tool === "catch_pairs" ? <Bipartite edges={sec.edges} nodes={sec.nodes} metric="dismissals" /> : <GraphView nodes={sec.nodes} edges={sec.edges} />}
              <Lines lines={sec.lines} />
            </>}
          </section>)}
        </>}
      </>}
      {tab === "fielding" && <>
        <div className="net-search">
          <select aria-label="Team" value={team} onChange={(e) => setTeam(e.target.value)}>{(opts?.teams ?? []).map((t) => <option key={t}>{t}</option>)}</select>
          <div className="seg" role="group" aria-label="Measure">
            <button className={metric === "dismissals" ? "active" : ""} onClick={() => setMetric("dismissals")}>Catches & stumpings</button>
            <button className={metric === "drops" ? "active" : ""} onClick={() => setMetric("drops")}>Drops</button>
          </div>
        </div>
        {web && (web.gap ? <p className="stats-note">{web.gap}</p> : <section className="chat-card net-section">
          <header><h3>{team} · {metric === "drops" ? "dropped catches off each bowler" : "c / st X b Y"} · {season || "all seasons"}</h3></header>
          <Bipartite edges={web.edges} nodes={web.nodes} metric={metric} />
          <p className="stats-note">{metric === "drops" ? "Audited drops (ipl_fielding, medium confidence or better); runs added after each drop are in the table on the match page." : "From Cricsheet's fielding records, matched to the wicket ball."}</p>
        </section>)}
      </>}
      {tab === "rankings" && <>
        <div className="seg" role="group" aria-label="Kind">
          <button className={kind === "bowler" ? "active" : ""} onClick={() => setKind("bowler")}>Bowlers</button>
          <button className={kind === "batter" ? "active" : ""} onClick={() => setKind("batter")}>Batters</button>
        </div>
        {ranks && <section className="chat-card net-section">
          <header><h3>Network rank · {season || "all seasons"}</h3></header>
          <p className="stats-note">{ranks.note}. {kind === "bowler" ? "A wicket counts for more when the batter is hard for other good bowlers to dismiss." : "Runs per dismissal count for more against bowlers who dismiss good batters."}</p>
          <ol className="net-rank">{ranks.rows.map((r) => <li key={String(r.player)}>
            <span>{r.player}</span><i style={{ width: `${(100 * Number(r.score)) / rankMax}%` }} /><b>{r.score}</b><small>{kind === "bowler" ? `${r.wickets} wkts` : `${r.runs} runs`}</small></li>)}</ol>
        </section>}
      </>}
      {tab === "communities" && groups && <section className="chat-card net-section">
        <header><h3>Teammate communities · {season || "all seasons"}</h3></header>
        <p className="stats-note">Louvain clusters on the teammate graph (played for the same team in the same season). Names are ordered by matches played.</p>
        {groups.map((g, i) => <div className="net-group" key={i}><b>{g.size} players</b><div>{g.players.map((p) => <span key={p}>{p}</span>)}</div></div>)}
      </section>}
      {tab === "checks" && checks && <section className="chat-card net-section">
        <header><h3>How the graph was checked</h3></header>
        <table className="net-table"><tbody>{checks.report.map((r) => <tr key={r.check}><td>{r.section}</td><td>{r.check}</td><td className={r.value.includes("MISMATCH") ? "bad" : ""}>{r.value}</td></tr>)}</tbody></table>
        <p className="stats-note">{Object.entries(checks.counts).map(([k, v]) => `${k}: ${v.toLocaleString()}`).join(" · ")}</p>
      </section>}
    </div>
  </div>;
}

function askFor(sec: GraphPayload, name: string, season: string): string {
  const s = season ? ` in ${season}` : "";
  if (sec.tool === "nemesis") return `Who is ${name}'s nemesis${s}?`;
  if (sec.tool === "bunnies") return `Who is ${name}'s favourite victim${s}?`;
  if (sec.tool === "teammates") return `Who has ${name} played with the most${s}?`;
  return `Which fielder-bowler pairs involve ${name}${s}?`;
}
