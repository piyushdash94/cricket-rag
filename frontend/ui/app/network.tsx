"use client";

// Network tab: the relational graph (data/graph.duckdb) drawn as small, readable pictures.
// Rivalry = one player's ego network (radial), Fielding web = catches / drops between a team's fielders and
// bowlers (two columns), Rankings = network PageRank (labelled a heuristic), Communities = teammate clusters.
// Every number shown comes from /api/graph/*, which reads SQL-built edge tables.

import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties, FormEvent } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export type GraphNode = { id: number; label: string; role: string; center?: boolean; keeper?: boolean };
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

/** The count an edge is about (dismissals, drops, seasons together, balls). */
function weight(e: GraphEdge): number {
  return Number(e.dismissals ?? e.drops ?? e.seasons ?? e.balls ?? 1) || 1;
}
function unitOf(edges: GraphEdge[]): string {
  const k = edges[0]?.kind;
  return k === "dropped_off" ? "drops" : k === "teammates" ? "seasons together" : k === "bowled_to" ? "balls" : "dismissals";
}

/** Width and shade spread over the range actually shown (min..max), so 5 against 7 is visible, not 5 against 7 out of 0..7. */
function scale(edges: GraphEdge[]) {
  const values = edges.map(weight);
  const lo = Math.min(...values), hi = Math.max(...values);
  const t = (v: number) => (hi === lo ? 1 : (v - lo) / (hi - lo));
  return { lo, hi, width: (v: number) => 2 + 9 * t(v), opacity: (v: number) => 0.35 + 0.65 * t(v) };
}

/** Short label drawn on the edge; the full numbers are in its tooltip and the list under the picture. */
function edgeLabel(e: GraphEdge): string {
  if (e.kind === "dismissed" || e.kind === "bowled_to") return `${e.dismissals}`;
  if (e.kind === "caught_off") return `${e.dismissals}`;
  if (e.kind === "dropped_off") return `${e.drops}`;
  if (e.kind === "teammates") return e.season ? `${e.team} ${e.season}` : `${e.seasons}`;
  return "";
}
function edgeTitle(e: GraphEdge, name: (id: number) => string): string {
  const base = `${name(e.source)} → ${name(e.target)}`;
  if (e.kind === "dismissed" || e.kind === "bowled_to") return `${base}: ${e.dismissals} dismissals, ${e.runs} runs off ${e.balls} balls`;
  if (e.kind === "caught_off") return `${base}: ${e.dismissals} caught or stumped${e.stumpings ? ` (${e.stumpings} stumpings)` : ""}`;
  if (e.kind === "dropped_off") return `${base}: ${e.drops} dropped${e.runs_after !== undefined ? `, ${e.runs_after} runs added after` : ""}`;
  return `${base}: ${edgeLabel(e)}`;
}

/** A count in a pill, readable over any line or theme. */
function Chip({ x, y, text, strong }: { x: number; y: number; text: string; strong?: boolean }) {
  const w = Math.max(20, 8 + text.length * 7.2);
  return <g className={`net-chip${strong ? " strong" : ""}`} transform={`translate(${x},${y})`}>
    <rect x={-w / 2} y={-9} width={w} height={18} rx={9} /><text y={4}>{text}</text>
  </g>;
}

/** Shape carries the role (square = wicketkeeper), so it works in every theme without relying on colour. */
function Node({ n, big }: { n: GraphNode; big?: boolean }) {
  const r = big ? 13 : 8;
  return n.keeper
    ? <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={3} fill="var(--accent)" className="net-node keeper" />
    : <circle r={r} fill={ROLE_COLOR[n.role] ?? "var(--accent)"} className="net-node" />;
}

function Legend({ edges, nodes }: { edges: GraphEdge[]; nodes: GraphNode[] }) {
  if (!edges.length) return null;
  const { lo, hi } = scale(edges);
  const roles = new Set(nodes.map((n) => (n.keeper ? "keeper" : n.role)));
  const label: Record<string, string> = { batter: "batter", bowler: "bowler", fielder: "fielder", keeper: "wicketkeeper", player: "player" };
  return <div className="net-legend">
    <span className="net-legend-scale"><i style={{ height: 2 }} />{lo}<i style={{ height: 11 }} />{hi} {unitOf(edges)}</span>
    {[...roles].map((r) => <span key={r}><b className={`dot ${r}`} />{label[r] ?? r}</span>)}
    {roles.has("keeper") && <small title="Cricsheet has no keeper flag: a player counts as the keeper for a team in a season in which he made a stumping for it">WK = made a stumping for that side that season</small>}
  </div>;
}

/** Radial ego network: the strongest link at 12 o'clock, then clockwise by count. */
export function GraphView({ nodes, edges, height = 300, chain = false }: { nodes: GraphNode[]; edges: GraphEdge[]; height?: number; chain?: boolean }) {
  const W = 460, H = height, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 46; // R: the two-player layout
  const center = chain ? undefined : nodes.find((n) => n.center) ?? (nodes.length === 2 ? undefined : nodes[0]);
  const strength = new Map<number, number>();
  for (const e of edges) for (const id of [e.source, e.target]) if (id !== center?.id) strength.set(id, Math.max(strength.get(id) ?? 0, weight(e)));
  const ring = nodes.filter((n) => n !== center).sort((a, b) => (strength.get(b.id) ?? 0) - (strength.get(a.id) ?? 0));
  const pos = new Map<number, { x: number; y: number }>();
  if (center) pos.set(center.id, { x: cx, y: cy });
  const RX = W / 2 - 120, RY = H / 2 - 30;
  const angle = new Map<number, number>();
  if (chain) nodes.forEach((n, i) => pos.set(n.id, { x: 50 + (i * (W - 100)) / Math.max(nodes.length - 1, 1), y: cy + (i % 2 ? -30 : 30) }));
  else if (!center && ring.length === 2) { pos.set(ring[0].id, { x: cx - R, y: cy }); pos.set(ring[1].id, { x: cx + R, y: cy }); }
  else ring.forEach((n, i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(ring.length, 1);
    angle.set(n.id, a);
    pos.set(n.id, { x: cx + RX * Math.cos(a), y: cy + RY * Math.sin(a) });
  });
  const sc = scale(edges);
  const name = (id: number) => nodes.find((n) => n.id === id)?.label ?? String(id);
  return <figure className="net-figure">
    <svg className="net-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Relationship graph">
      {edges.map((e, i) => {
        const a = pos.get(e.source), b = pos.get(e.target);
        if (!a || !b) return null;
        const v = weight(e);
        return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={sc.width(v)} strokeOpacity={sc.opacity(v)} className={`net-edge ${e.kind}`}><title>{edgeTitle(e, name)}</title></line>;
      })}
      {edges.map((e, i) => {
        const a = pos.get(e.source), b = pos.get(e.target);
        if (!a || !b) return null;
        const outer = center && e.source === center.id ? b : center && e.target === center.id ? a : null;
        const inner = outer === a ? b : a;
        const lx = outer ? inner.x + 0.55 * (outer.x - inner.x) : (a.x + b.x) / 2;
        const ly = outer ? inner.y + 0.55 * (outer.y - inner.y) : (a.y + b.y) / 2;
        return <Chip key={i} x={lx} y={ly} text={edgeLabel(e)} strong={weight(e) === sc.hi} />;
      })}
      {nodes.map((n) => { const p = pos.get(n.id); if (!p) return null; const big = n === center;
        return <g key={n.id} transform={`translate(${p.x},${p.y})`}>
          <Node n={n} big={big} />
          {(() => {
            const label = <>{n.label}{n.keeper && <tspan className="net-wk-text"> WK</tspan>}</>;
            const a = angle.get(n.id);
            if (big || a === undefined) return <text y={big ? 28 : 21} className={`net-label${big ? " center" : ""}`}>{label}</text>;
            const c = Math.cos(a), sn = Math.sin(a);
            if (Math.abs(c) < 0.35) return <text y={sn < 0 ? -13 : 22} className="net-label">{label}</text>; // top / bottom
            return <text x={c > 0 ? 13 : -13} y={4} className={`net-label ${c > 0 ? "right" : "left"}`}>{label}</text>;
          })()}
        </g>; })}
    </svg>
    <Legend edges={edges} nodes={nodes} />
  </figure>;
}

/** Fielders (left) to bowlers (right), both ordered by total; width and shade over the shown range; a count on each edge. */
export function Bipartite({ edges: all, nodes, metric, limit = 12 }: { edges: GraphEdge[]; nodes: GraphNode[]; metric: "dismissals" | "drops"; limit?: number }) {
  const [showAll, setShowAll] = useState(false);
  const edges = showAll ? all : all.slice(0, limit);
  const total = (side: "source" | "target") => {
    const m = new Map<number, number>();
    for (const e of edges) m.set(e[side], (m.get(e[side]) ?? 0) + Number(e[metric] ?? 0));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([id]) => id);
  };
  const left = total("source"), right = total("target");
  const row = 28, H = Math.max(left.length, right.length) * row + 34, W = 460, lx = 150, rx = 310;
  const node = (id: number) => nodes.find((n) => n.id === id);
  const name = (id: number) => node(id)?.label ?? String(id);
  const y = (list: number[], id: number) => 26 + list.indexOf(id) * row;
  const sc = scale(edges);
  const curve = (y1: number, y2: number, t: number) => { // the edge's cubic at t, for placing its count
    const mt = 1 - t, xm = (lx + rx) / 2;
    return { x: mt ** 3 * (lx + 6) + 3 * mt * mt * t * xm + 3 * mt * t * t * xm + t ** 3 * (rx - 6), y: (mt ** 3 + 3 * mt * mt * t) * y1 + (3 * mt * t * t + t ** 3) * y2 };
  };
  // Counts sit near their fielder, staggered along his fan of edges (his k-th edge further out), then nudged
  // along the curve until clear of the others: the middle, where edges cross, stays free of labels.
  const placed: { x: number; y: number }[] = [];
  const seen = new Map<number, number>();
  const chips = edges.map((e) => {
    const k = seen.get(e.source) ?? 0; seen.set(e.source, k + 1);
    const y1 = y(left, e.source), y2 = y(right, e.target);
    const t0 = Math.min(0.2 + 0.17 * k, 0.86);
    for (const t of [t0, t0 + 0.07, t0 - 0.07, t0 + 0.14, t0 + 0.21, t0 - 0.12, 0.5, 0.92]) {
      if (t <= 0.08 || t >= 0.95) continue;
      const p = curve(y1, y2, t);
      if (placed.every((q) => Math.abs(q.x - p.x) > 28 || Math.abs(q.y - p.y) > 18)) { placed.push(p); return p; }
    }
    const p = curve(y1, y2, t0); placed.push(p); return p;
  });
  return <figure className="net-figure">
    <svg className="net-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Fielder to bowler web">
      <text x={lx} y={12} className="net-col">FIELDER</text><text x={rx} y={12} className="net-col right">BOWLER</text>
      {edges.map((e, i) => { const y1 = y(left, e.source), y2 = y(right, e.target); const v = Number(e[metric] ?? 0);
        return <path key={i} d={`M${lx + 6},${y1} C${(lx + rx) / 2},${y1} ${(lx + rx) / 2},${y2} ${rx - 6},${y2}`}
          strokeWidth={sc.width(v)} strokeOpacity={sc.opacity(v)} className={`net-edge ${metric === "drops" ? "dropped_off" : "caught_off"}`}><title>{edgeTitle(e, name)}</title></path>; })}
      {edges.map((e, i) => <Chip key={i} x={chips[i].x} y={chips[i].y} text={edgeLabel(e)} strong={Number(e[metric] ?? 0) === sc.hi} />)}
      {left.map((id) => { const n = node(id); return <g key={`l${id}`} transform={`translate(${lx},${y(left, id)})`}>
        {n?.keeper ? <rect x={-6} y={-6} width={12} height={12} rx={2} fill="var(--accent)" className="net-node keeper" /> : <circle r={5.5} fill="var(--amber)" className="net-node" />}
        <text x={-12} y={4} className="net-label left">{n?.keeper && <tspan className="net-wk-text">WK </tspan>}{name(id)}</text>
      </g>; })}
      {right.map((id) => <g key={`r${id}`} transform={`translate(${rx},${y(right, id)})`}><circle r={5.5} fill="var(--red)" className="net-node" /><text x={12} y={4} className="net-label right">{name(id)}</text></g>)}
    </svg>
    <Legend edges={edges} nodes={edges.length ? [...left, ...right].map((id) => node(id)!).filter(Boolean) : []} />
    {all.length > limit && <button className="net-more" onClick={() => setShowAll((v) => !v)}>{showAll ? `Show the top ${limit}` : `Show all ${all.length} pairs`}</button>}
  </figure>;
}

/** Fielders (rows) by bowlers (columns): the count in each cell, shaded over the shown range. Reads exactly at any density. */
export function PairMatrix({ edges, nodes, metric }: { edges: GraphEdge[]; nodes: GraphNode[]; metric: "dismissals" | "drops" }) {
  const sum = (side: "source" | "target") => {
    const m = new Map<number, number>();
    for (const e of edges) m.set(e[side], (m.get(e[side]) ?? 0) + Number(e[metric] ?? 0));
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  };
  const rows = sum("source"), cols = sum("target");
  const cell = new Map(edges.map((e) => [`${e.source}:${e.target}`, e]));
  const node = (id: number) => nodes.find((n) => n.id === id);
  const sc = scale(edges);
  const tint = metric === "drops" ? "var(--red)" : "var(--amber)";
  return <figure className="net-figure">
    <div className="pair-matrix-wrap">
      <table className="pair-matrix" style={{ "--tint": tint } as CSSProperties}>
        <thead><tr><th scope="col" className="corner">Fielder ↓ · Bowler →</th>
          {cols.map(([id]) => <th key={id} scope="col"><span>{node(id)?.label}</span></th>)}<th scope="col" className="total"><span>Total</span></th></tr></thead>
        <tbody>{rows.map(([fid, total]) => { const f = node(fid); return <tr key={fid}>
          <th scope="row">{f?.keeper && <b className="wk" title="Made a stumping for this side that season">WK</b>}{f?.label}</th>
          {cols.map(([bid]) => { const e = cell.get(`${fid}:${bid}`); const v = Number(e?.[metric] ?? 0);
            const share = e ? Math.round(18 + 70 * (sc.hi === sc.lo ? 1 : (v - sc.lo) / (sc.hi - sc.lo))) : 0;
            return <td key={bid} title={e ? edgeTitle(e, (id) => node(id)?.label ?? String(id)) : undefined}
              className={e ? (v === sc.hi ? "hit top" : "hit") : ""} style={e ? { "--share": `${share}%` } as CSSProperties : undefined}>{e ? v : ""}</td>; })}
          <td className="total">{total}</td>
        </tr>; })}</tbody>
      </table>
    </div>
    <Legend edges={edges} nodes={[...rows, ...cols].map(([id]) => node(id)!).filter(Boolean)} />
  </figure>;
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
  const [view, setView] = useState<"matrix" | "web">("matrix");
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
          <div className="seg" role="group" aria-label="View">
            <button className={view === "matrix" ? "active" : ""} onClick={() => setView("matrix")}>Matrix</button>
            <button className={view === "web" ? "active" : ""} onClick={() => setView("web")}>Web</button>
          </div>
        </div>
        {web && (web.gap ? <p className="stats-note">{web.gap}</p> : <section className="chat-card net-section">
          <header><h3>{team} · {metric === "drops" ? "dropped catches off each bowler" : "c / st X b Y"} · {season || "all seasons"}</h3></header>
          {view === "matrix" ? <PairMatrix edges={web.edges} nodes={web.nodes} metric={metric} /> : <Bipartite edges={web.edges} nodes={web.nodes} metric={metric} limit={10} />}
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

type RankRow = { player: string; score: number; wickets?: number };

/** Front page: one season's network in two small, read-only charts, with a way into the Network tab. */
export function SeasonNetwork({ season, onOpen }: { season: number; onOpen: () => void }) {
  const [ranks, setRanks] = useState<{ rows: RankRow[]; min_balls: number } | null>(null);
  const [pairs, setPairs] = useState<GraphPayload | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    Promise.all([
      get<{ rows: RankRow[]; min_balls: number }>(`/api/graph/rankings?kind=bowler&year=${season}&top=5`),
      get<GraphPayload>(`/api/graph/pairs?year=${season}&top=5`),
    ]).then(([r, p]) => { if (live) { setRanks(r); setPairs(p); setFailed(false); } })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [season]);
  if (failed) return null; // no graph built: the front page simply has no network section
  const max = Math.max(1, ...(ranks?.rows ?? []).map((r) => r.score));
  return <section className="fp-section fp-network" aria-label={`IPL ${season} player network`}>
    <div className="fp-head fp-head-row">
      <div><h3>The network</h3><p>Who dismissed whom in {season}, read as a graph: bowlers ranked by whose wickets they took, and the catches that linked fielders to bowlers.</p></div>
      <button className="fp-link" onClick={onOpen}>Open the network →</button>
    </div>
    <div className="fp-net-grid">
      <article className="fp-net-card">
        <p className="section-kicker">NETWORK RANK · BOWLERS</p>
        {ranks ? <ol className="net-rank compact">{ranks.rows.map((r) => <li key={r.player}>
          <span>{r.player}</span><i style={{ width: `${(100 * r.score) / max}%` }} /><b>{r.score}</b><small>{r.wickets} wkts</small></li>)}</ol>
          : <div className="fp-net-skeleton" />}
        <p className="stats-note">PageRank on the dismissal graph, min {ranks?.min_balls ?? 120} balls. A heuristic, not an official stat.</p>
      </article>
      <article className="fp-net-card">
        <p className="section-kicker">CATCHING PAIRS · C / ST X B Y</p>
        {pairs ? (pairs.gap ? <p className="stats-note">{pairs.gap}</p> : <Bipartite edges={pairs.edges} nodes={pairs.nodes} metric="dismissals" />)
          : <div className="fp-net-skeleton" />}
      </article>
    </div>
  </section>;
}
