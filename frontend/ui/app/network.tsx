"use client";

// Network tab: the relational graph (data/graph.duckdb) drawn as small, readable pictures.
// Rivalry = one player's ego network (radial), Fielding web = catches / drops between a team's fielders and
// bowlers, Rankings = quality wickets / quality runs (one stated weight), How they get out = dismissals by bowling
// type (as a batter) and by the victim's place in the order (as a bowler), each split by mode.
// Every number shown comes from /api/graph/*, which reads SQL-built edge tables.

import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties, FormEvent } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export type GraphNode = { id: number; label: string; role: string; center?: boolean; keeper?: boolean };
export type GraphEdge = { source: number; target: number; kind: string; [k: string]: string | number | undefined };
export type GraphPayload = { tool: string; title: string; nodes: GraphNode[]; edges: GraphEdge[]; rows: Record<string, unknown>[]; gap?: string | null; lines?: string[] };
export type GraphCard = GraphPayload & { type: "graph" };

type Tab = "rivalry" | "fielding" | "rankings" | "dismissals" | "grounds";
const TABS: { id: Tab; label: string }[] = [
  { id: "rivalry", label: "Rivalry" }, { id: "fielding", label: "Fielding web" }, { id: "rankings", label: "Rankings" },
  { id: "dismissals", label: "How they get out" }, { id: "grounds", label: "Grounds" },
];
const ROLE_COLOR: Record<string, string> = { batter: "var(--accent)", bowler: "var(--red)", fielder: "var(--amber)", player: "var(--accent)", venue: "var(--green, #2f9e5b)" };

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail ?? `HTTP ${r.status}`);
  return r.json();
}

/** The count an edge is about (dismissals, drops, seasons together, balls). */
function weight(e: GraphEdge): number {
  return Math.abs(Number(e.dismissals ?? e.drops ?? e.similarity ?? e.value ?? e.seasons ?? e.balls ?? 1)) || 1;
}
function unitOf(edges: GraphEdge[]): string {
  const k = edges[0]?.kind;
  return k === "dropped_off" ? "drops" : k === "teammates" ? "seasons together" : k === "bowled_to" ? "balls" : k === "similar" ? "% alike" : k === "venue_par" ? "v par" : "dismissals";
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
  if (e.kind === "profile") return `${e.dismissals}`;
  if (e.kind === "similar") return `${e.similarity}%`;
  if (e.kind === "venue_par") return `${Number(e.value) > 0 ? "+" : ""}${e.value}`;
  return "";
}
function edgeTitle(e: GraphEdge, name: (id: number) => string): string {
  const base = `${name(e.source)} → ${name(e.target)}`;
  if (e.kind === "dismissed" || e.kind === "bowled_to") return `${base}: ${e.dismissals} dismissals, ${e.runs} runs off ${e.balls} balls`;
  if (e.kind === "caught_off") return `${base}: ${e.dismissals} caught or stumped${e.stumpings ? ` (${e.stumpings} stumpings)` : ""}`;
  if (e.kind === "dropped_off") return `${base}: ${e.drops} dropped${e.runs_after !== undefined ? `, ${e.runs_after} runs added after` : ""}`;
  if (e.kind === "profile") return `${base}: ${e.dismissals} wickets`;
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
  const label: Record<string, string> = { batter: "batter", bowler: "bowler", fielder: "fielder", keeper: "wicketkeeper", player: "player", venue: "ground" };
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
        const t = outer && outer.y > inner.y + 4 ? 0.7 : 0.55; // edges running down keep their count clear of the centre's name
        const lx = outer ? inner.x + t * (outer.x - inner.x) : (a.x + b.x) / 2;
        const ly = outer ? inner.y + t * (outer.y - inner.y) : (a.y + b.y) / 2;
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

/** The numbers behind a rivalry picture, as a short table (the writer's sentences stay in the chat). */
const COLS: Record<string, [string, string][]> = {
  nemesis: [["bowler", "Bowler"], ["dismissals", "Outs"], ["balls", "Balls"], ["runs", "Runs"], ["strike_rate", "SR"]],
  bunnies: [["batter", "Batter"], ["dismissals", "Outs"], ["balls", "Balls"], ["runs", "Runs"], ["strike_rate", "SR"]],
};
function MiniTable({ sec, name }: { sec: GraphPayload; name: string }) {
  const cols = sec.tool === "catch_pairs"
    ? [[sec.rows[0]?.fielder === name ? "bowler" : "fielder", sec.rows[0]?.fielder === name ? "Bowler" : "Fielder"], ["dismissals", "Wkts"],
      ...(sec.rows.some((r) => Number(r.stumpings ?? 0) > 0) ? [["stumpings", "Stumped"]] : [])] as [string, string][]
    : COLS[sec.tool];
  if (!cols || !sec.rows.length) return null;
  return <table className="net-mini"><thead><tr>{cols.map(([, h]) => <th key={h}>{h}</th>)}</tr></thead>
    <tbody>{sec.rows.slice(0, 6).map((r, i) => <tr key={i}>{cols.map(([k]) => <td key={k}>{String(r[k] ?? "")}</td>)}</tr>)}</tbody></table>;
}
function shortTitle(sec: GraphPayload, name: string): string {
  if (sec.tool === "nemesis") return `Got ${name} out most`;
  if (sec.tool === "bunnies") return `${name}'s most frequent victims`;
  if (sec.tool === "catch_pairs") return sec.rows[0]?.fielder === name ? `Catches ${name} took, by bowler` : `Caught off ${name}'s bowling`;
  return sec.title;
}
/** One-off links (every count 1) say nothing about a rivalry: those sections are left out. */
const worthShowing = (sec: GraphPayload) => sec.tool !== "teammates" && sec.rows.some((r) => Number(r.dismissals ?? 0) >= 2);

/** The chat card for a relational answer: the subgraph behind it, plus a way into the Network tab. */
export function GraphChatCard({ card, onOpen }: { card: GraphCard; onOpen: () => void }) {
  if (card.tool === "teammates") return null; // seasons together: the answer's text says it; no picture
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
  const [outs, setOuts] = useState<Profile | null>(null);
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
  useEffect(() => { if (tab === "dismissals" && pid !== null) get<Profile>(`/api/graph/dismissals/${pid}?${sq}`).then(setOuts).catch(() => setOuts(null)); }, [tab, pid, sq]);

  const web = field ? (metric === "drops" ? field.drops : field.catches) : null;
  const rankMax = useMemo(() => Math.max(1, ...(ranks?.rows ?? []).map((r) => Number(r.score))), [ranks]);

  if (error && !opts) {
    return <div className="stats-panel"><div className="stats-hero"><div className="hero-actions"><button className="back-link" onClick={onClose}>← Close</button></div></div>
      <div className="workspace-empty"><span>◎</span><h3>Relational graph not built yet</h3><p>Run this once in the project root, then reload:</p>
        <code className="stats-command">uv run cricrag build-graph</code><p className="stats-note">{error}</p></div></div>;
  }

  return <div className="stats-panel net-panel">
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
      {(tab === "rivalry" || tab === "dismissals" || tab === "grounds") && <form className="net-search" onSubmit={search}>
          <input aria-label="Player" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Player name" />
          <button>Show</button>
          {hits.length > 1 && <select aria-label="Pick a player" value={pid ?? ""} onChange={(e) => setPid(Number(e.target.value))}>
            {hits.map((h) => <option key={h.id} value={h.id}>{h.name} ({h.matches} matches)</option>)}</select>}
        </form>}
      {tab === "rivalry" && <>
        {player && <>
          <p className="stats-note">{player.name}: {player.faced.toLocaleString()} balls faced, {player.bowled.toLocaleString()} bowled in this archive.</p>
          {player.sections.filter((sec) => !sec.gap && worthShowing(sec)).map((sec, i) => <section className="chat-card net-section" key={`${sec.tool}${i}`}>
            <header><h3>{shortTitle(sec, player.name)}</h3><button className="net-ask" onClick={() => onAsk(askFor(sec, player.name, season))}>Ask Pandit</button></header>
            {sec.tool === "catch_pairs" ? <Bipartite edges={sec.edges} nodes={sec.nodes} metric="dismissals" /> : <GraphView nodes={sec.nodes} edges={sec.edges} />}
            <MiniTable sec={sec} name={player.name} />
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
          <header><h3>{kind === "bowler" ? "Quality wickets" : "Quality runs"} · {season || "all seasons"}</h3></header>
          <p className="stats-note">{ranks.note}</p>
          <table className="net-mini net-rank-table"><thead><tr><th>#</th><th>{kind === "bowler" ? "Bowler" : "Batter"}</th><th className="bar">{kind === "bowler" ? "Quality wkts" : "Quality runs"}</th>
            {kind === "bowler" ? <><th>Wkts</th><th title="Batting positions 1-3">Top</th><th title="Positions 4-7">Middle</th><th title="Positions 8-11">Tail</th></> : <><th>Runs</th><th>Balls</th><th>Outs</th></>}</tr></thead>
            <tbody>{ranks.rows.map((r, i) => <tr key={String(r.player)}><td>{i + 1}</td><td>{r.player}</td>
              <td className="bar"><i style={{ width: `${(100 * Number(r.score)) / rankMax}%` }} /><b>{r.score}</b></td>
              {kind === "bowler" ? <><td>{r.wickets}</td><td>{r["top order"]}</td><td>{r["middle order"]}</td><td>{r.tail}</td></> : <><td>{r.runs}</td><td>{r.balls}</td><td>{r.outs}</td></>}</tr>)}</tbody></table>
        </section>}
      </>}
      {tab === "dismissals" && outs && <>
        {outs.batter.total < MIN_PROFILE && outs.bowler.total < MIN_PROFILE && <p className="stats-note">Fewer than {MIN_PROFILE} bowler-credited wickets for {outs.name} {season ? `in ${season}` : "in the archive"}: too few to profile.</p>}
        {outs.batter.total >= MIN_PROFILE && <ProfileCard p={outs} side="batter" season={season} onAsk={onAsk} />}
        {outs.bowler.total >= MIN_PROFILE && <ProfileCard p={outs} side="bowler" season={season} onAsk={onAsk} hands={outs.hand} />}
        <p className="stats-note">Wickets credited to the bowler only: run outs are the fielder&apos;s and are left out. Bowling type from ESPN profiles (pace is every speed); top order = batting 1-3, middle = 4-7, tail = 8 and below.</p>
      </>}
      {tab === "grounds" && <GroundsTab season={season} pid={pid} player={player?.name ?? query} onAsk={onAsk} />}
    </div>
  </div>;
}

type Side = { total: number; groups: { group: string; n: number; modes: Record<string, number> }[] };
type Profile = { id: number; name: string; season: string | null; batter: Side; bowler: Side; hand: Side };
const MODES = ["caught", "bowled", "lbw", "stumped", "hit wicket"];
const MIN_PROFILE = 5; // a profile of one or two wickets is noise (Kohli the bowler: 4)

/** One player and his wicket profile: a radial picture (who or what got him, by count) and the same counts by mode. */
function ProfileCard({ p, side, season, onAsk, hands }: { p: Profile; side: "batter" | "bowler"; season: string; onAsk: (q: string) => void; hands?: Side }) {
  const s = p[side];
  const nodes: GraphNode[] = [{ id: 0, label: p.name, role: side, center: true },
    ...s.groups.map((g, i) => ({ id: i + 1, label: g.group, role: side === "batter" ? "bowler" : "batter" }))];
  const edges: GraphEdge[] = s.groups.map((g, i) => ({ source: i + 1, target: 0, kind: "profile", dismissals: g.n }));
  const modes = MODES.filter((m) => s.groups.some((g) => g.modes[m]));
  const hi = Math.max(...s.groups.flatMap((g) => modes.map((m) => g.modes[m] ?? 0)));
  const scope = season ? ` in ${season}` : "";
  return <section className="chat-card net-section">
    <header><h3>{side === "batter" ? `How ${p.name} got out · ${s.total} wickets` : `Whom ${p.name} dismissed · ${s.total} wickets`}</h3>
      <button className="net-ask" onClick={() => onAsk(side === "batter" ? `How does ${p.name} get out against spin and pace${scope}?` : `Which batting positions has ${p.name} dismissed most${scope}?`)}>Ask Pandit</button></header>
    <GraphView nodes={nodes} edges={edges} height={280} />
    <table className="net-mini net-modes"><thead><tr><th>{side === "batter" ? "Bowling" : "Batter's place"}</th>{modes.map((m) => <th key={m}>{m}</th>)}<th>Total</th><th>Share</th></tr></thead>
      <tbody>{s.groups.map((g) => <tr key={g.group}><th scope="row">{g.group}</th>
        {modes.map((m) => { const v = g.modes[m] ?? 0; return <td key={m} className={v ? "hit" : ""} style={v ? { "--share": `${Math.round(15 + 70 * v / hi)}%` } as CSSProperties : undefined}>{v || ""}</td>; })}
        <td className="total">{g.n}</td><td className="total">{Math.round((100 * g.n) / s.total)}%</td></tr>)}</tbody></table>
    {hands && hands.total > 0 && <ModeTable s={hands} head="Batter's hand" />}
  </section>;
}

function ModeTable({ s, head }: { s: Side; head: string }) {
  const modes = MODES.filter((m) => s.groups.some((g) => g.modes[m]));
  const hi = Math.max(...s.groups.flatMap((g) => modes.map((m) => g.modes[m] ?? 0)));
  return <table className="net-mini net-modes"><thead><tr><th>{head}</th>{modes.map((m) => <th key={m}>{m}</th>)}<th>Total</th><th>Share</th></tr></thead>
    <tbody>{s.groups.map((g) => <tr key={g.group}><th scope="row">{g.group}</th>
      {modes.map((m) => { const v = g.modes[m] ?? 0; return <td key={m} className={v ? "hit" : ""} style={v ? { "--share": `${Math.round(15 + 70 * v / hi)}%` } as CSSProperties : undefined}>{v || ""}</td>; })}
      <td className="total">{g.n}</td><td className="total">{Math.round((100 * g.n) / s.total)}%</td></tr>)}</tbody></table>;
}

type Feature = { key: string; label: string; unit: string; value: number | null; delta: number };
type VenueRow = Record<string, string | number | null>;
type Venue = { name: string; short: string; matches: number; family: string | null; like: GraphPayload; features: Feature[]; batters: VenueRow[]; bowlers: VenueRow[] };
type Families = { venues: { name: string; matches: number }[]; families: { name: string; venues: string[]; matches: number }[]; min_matches: number };
const fmt = (v: number | null, unit: string) => v === null ? "–" : unit === "%" ? `${v.toFixed(1)}%` : unit === "runs" ? v.toFixed(0) : v.toFixed(2);
const signed = (v: number, unit: string) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(unit === "runs" ? 0 : unit === "%" ? 1 : 2)}${unit === "%" ? " pts" : ""}`;

/** Grounds as a network: the grounds that play alike (an era-adjusted profile), the families that graph forms, and
 *  who beats each ground's par. Par = everyone else at the ground in the same seasons. */
function GroundsTab({ season, pid, player, onAsk }: { season: string; pid: number | null; player: string; onAsk: (q: string) => void }) {
  const sq = season ? `season=${encodeURIComponent(season)}` : "";
  const [list, setList] = useState<Families | null>(null);
  const [name, setName] = useState("");
  const [v, setV] = useState<Venue | null>(null);
  const [role, setRole] = useState<"bat" | "bowl">("bat");
  const [mine, setMine] = useState<{ name: string; rows: VenueRow[] } | null>(null);
  useEffect(() => { get<Families>(`/api/graph/venues?${sq}`).then((r) => { setList(r); setName((n) => (r.venues.some((x) => x.name === n) ? n : r.venues[0]?.name ?? "")); }).catch(() => setList(null)); }, [sq]);
  useEffect(() => { if (name) get<Venue>(`/api/graph/venue?name=${encodeURIComponent(name)}&${sq}`).then(setV).catch(() => setV(null)); }, [name, sq]);
  useEffect(() => { if (pid !== null) get<{ name: string; rows: VenueRow[] }>(`/api/graph/player-venues/${pid}?role=${role}&${sq}`).then(setMine).catch(() => setMine(null)); }, [pid, role, sq]);
  if (!list) return null;
  if (!list.venues.length) return <p className="stats-note">No ground has {list.min_matches} matches in this range.</p>;
  const scope = season ? ` in ${season}` : "";
  return <>
    <div className="net-search">
      <select aria-label="Ground" value={name} onChange={(e) => setName(e.target.value)}>{list.venues.map((x) => <option key={x.name} value={x.name}>{x.name} ({x.matches})</option>)}</select>
    </div>
    {v && <section className="chat-card net-section">
      <header><h3>{v.short} plays like…</h3><button className="net-ask" onClick={() => onAsk(`Which grounds play like ${v.name}${scope}?`)}>Ask Pandit</button></header>
      <p className="stats-note">{v.matches} matches · family: <b>{v.family ?? "–"}</b>. Similarity compares six numbers, each taken against the IPL of the same seasons (100% = the same profile).</p>
      {v.like.gap ? <p className="stats-note">{v.like.gap}</p> : <GraphView nodes={v.like.nodes} edges={v.like.edges} height={300} />}
      <table className="net-mini"><thead><tr><th>Profile</th><th>{v.short}</th><th>v IPL, same seasons</th></tr></thead>
        <tbody>{v.features.map((f) => <tr key={f.key}><td>{f.label}</td><td>{fmt(f.value, f.unit)}</td><td className={f.delta >= 0 ? "up" : "down"}>{signed(f.delta, f.unit)}</td></tr>)}</tbody></table>
    </section>}
    {v && <section className="chat-card net-section">
      <header><h3>Who beats {v.name}&apos;s par</h3><button className="net-ask" onClick={() => onAsk(`Who are the best batters at ${v.name} against par${scope}?`)}>Ask Pandit</button></header>
      <div className="net-two">
        <ParTable rows={v.batters} role="bat" title="Batters · runs above par per 100 balls" who="player" />
        <ParTable rows={v.bowlers} role="bowl" title="Bowlers · runs saved per 24 balls" who="player" />
      </div>
      <p className="stats-note">Par = everyone else at this ground in the same seasons; min 120 balls; rates steadied toward par by 60 balls, so a short hot streak cannot top the list.</p>
    </section>}
    {mine && <section className="chat-card net-section">
      <header><h3>{mine.name} by ground</h3>
        <div className="seg" role="group" aria-label="Role">
          <button className={role === "bat" ? "active" : ""} onClick={() => setRole("bat")}>Batting</button>
          <button className={role === "bowl" ? "active" : ""} onClick={() => setRole("bowl")}>Bowling</button>
        </div></header>
      {mine.rows.length ? <ParTable rows={mine.rows} role={role} title={role === "bat" ? "Runs above par per 100 balls" : "Runs saved per 24 balls"} who="venue" bars />
        : <p className="stats-note">{player} has not {role === "bat" ? "batted" : "bowled"} 60 balls at any ground{scope}.</p>}
    </section>}
    <section className="chat-card net-section">
      <header><h3>Families of grounds{scope}</h3><button className="net-ask" onClick={() => onAsk(`What are the different types of IPL grounds${scope}?`)}>Ask Pandit</button></header>
      <p className="stats-note">Each ground is linked to the three it plays most like; the communities of that graph are the families, named by their strongest traits.</p>
      <div className="net-families">{list.families.map((f) => <div key={f.name}><b>{f.name}</b><span>{f.venues.map((x) => <button key={x} className={x === name ? "on" : ""} onClick={() => setName(x)}>{x}</button>)}</span></div>)}</div>
    </section>
  </>;
}

function ParTable({ rows, role, title, who, bars }: { rows: VenueRow[]; role: "bat" | "bowl"; title: string; who: "player" | "venue"; bars?: boolean }) {
  const key = role === "bat" ? "above_par" : "runs_saved";
  const hi = Math.max(1, ...rows.map((r) => Math.abs(Number(r[key]))));
  return <div className="net-par"><p className="section-kicker">{title}</p>
    <table className="net-mini"><thead><tr><th>{who === "player" ? (role === "bat" ? "Batter" : "Bowler") : "Ground"}</th><th>{role === "bat" ? "+/100" : "Saved/24"}</th>
      {role === "bat" ? <><th>SR v par</th><th>Avg v par</th></> : <><th>Econ v par</th><th>Wkts</th></>}<th>Balls</th></tr></thead>
      <tbody>{rows.map((r) => { const val = Number(r[key]); return <tr key={String(r[who])}><td>{String(r[who])}</td>
        <td className={`par ${val >= 0 ? "up" : "down"}`}>{bars && <i style={{ width: `${(48 * Math.abs(val)) / hi}px` }} />}{val > 0 ? "+" : ""}{val}</td>
        {role === "bat" ? <><td>{r.strike_rate} <small>v {r.par_strike_rate}</small></td><td>{r.average ?? "no out"} <small>v {r.par_average ?? "–"}</small></td></>
          : <><td>{r.economy} <small>v {r.par_economy}</small></td><td>{r.wickets}</td></>}
        <td>{r.balls}</td></tr>; })}</tbody></table></div>;
}

function askFor(sec: GraphPayload, name: string, season: string): string {
  const s = season ? ` in ${season}` : "";
  if (sec.tool === "nemesis") return `Who is ${name}'s nemesis${s}?`;
  if (sec.tool === "bunnies") return `Who is ${name}'s favourite victim${s}?`;
  return `Which fielder-bowler pairs involve ${name}${s}?`;
}

type RankRow = { player: string; score: number; wickets?: number; "top order"?: number };

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
      <div><h3>The network</h3><p>Who dismissed whom in {season}: bowlers ranked by the quality of the batters they got out, and the catches that linked fielders to bowlers.</p></div>
      <button className="fp-link" onClick={onOpen}>Open the network →</button>
    </div>
    <div className="fp-net-grid">
      <article className="fp-net-card">
        <p className="section-kicker">QUALITY WICKETS · BOWLERS</p>
        {ranks ? <ol className="net-rank compact">{ranks.rows.map((r) => <li key={r.player}>
          <span>{r.player}<small>{r.wickets} wkts · {r["top order"]} top order</small></span><i style={{ width: `${(100 * r.score) / max}%` }} /><b>{r.score}</b></li>)}</ol>
          : <div className="fp-net-skeleton" />}
        <p className="stats-note">Each wicket weighted by the batter&apos;s average × strike rate that season, against the league&apos;s. Min {ranks?.min_balls ?? 120} balls.</p>
      </article>
      <article className="fp-net-card">
        <p className="section-kicker">CATCHING PAIRS · C / ST X B Y</p>
        {pairs ? (pairs.gap ? <p className="stats-note">{pairs.gap}</p> : <Bipartite edges={pairs.edges} nodes={pairs.nodes} metric="dismissals" />)
          : <div className="fp-net-skeleton" />}
      </article>
    </div>
  </section>;
}
