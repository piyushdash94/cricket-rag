"use client";

// The front page: one season at a glance (final, caps, numbers, closest finishes), every season's scoring
// as a chart you can click to change season, and a reading room for IPL articles. All numbers come from
// /api/home, which is plain SQL on the archive.

import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

type Era = { year: number; first: number | null; sixes: number; run_rate: number; matches: number };
type Leader = { name: string; value: number } | null;
type Ref = { match_id: number; series_slug?: string; match_slug?: string };
type Thriller = Ref & { title: string; date: string; how: string; teams: string[]; scores: string[]; winner: string; result: string };
type HomeData = {
  year: number; available: boolean; matches?: number; runs?: number; sixes?: number; fours?: number; two_hundreds?: number;
  highest?: Ref & { team: string; runs: number; wickets: number; opponent: string };
  final?: (Ref & { winner: string; result: string; date: string; teams: string[] }) | null;
  orange_cap?: Leader; purple_cap?: Leader; most_sixes?: Leader; thrillers?: Thriller[]; eras: Era[];
};
export type Article = { kind: string; title: string; url: string; source: string; year?: number; published?: string };
export type News = { live: Article[]; reading: Article[]; kinds: string[] };

// Franchise colours and names (names change with the era).
const TEAMS: Record<string, { color: string; name: (year: number) => string }> = {
  CSK: { color: "#f2b705", name: () => "Chennai Super Kings" },
  MI: { color: "#004ba0", name: () => "Mumbai Indians" },
  RCB: { color: "#d71920", name: (y) => (y >= 2024 ? "Royal Challengers Bengaluru" : "Royal Challengers Bangalore") },
  KKR: { color: "#3a225d", name: () => "Kolkata Knight Riders" },
  SRH: { color: "#f26522", name: () => "Sunrisers Hyderabad" },
  RR: { color: "#e4007c", name: () => "Rajasthan Royals" },
  DC: { color: "#17479e", name: (y) => (y >= 2019 ? "Delhi Capitals" : "Delhi Daredevils") },
  PBKS: { color: "#dd1f2d", name: (y) => (y >= 2021 ? "Punjab Kings" : "Kings XI Punjab") },
  GT: { color: "#1c2c5b", name: () => "Gujarat Titans" },
  LSG: { color: "#00a0e3", name: () => "Lucknow Super Giants" },
  DCH: { color: "#d8a32a", name: () => "Deccan Chargers" },
  PWI: { color: "#2f9be3", name: () => "Pune Warriors India" },
  GL: { color: "#e04f16", name: () => "Gujarat Lions" },
  RPS: { color: "#6f2c91", name: () => "Rising Pune Supergiant" },
  KTK: { color: "#f15a29", name: () => "Kochi Tuskers Kerala" },
};
const teamColor = (code?: string) => (code && TEAMS[code]?.color) || "var(--muted)";
const teamName = (code: string | undefined, year: number) => (code && TEAMS[code]?.name(year)) || code || "";
const num = (value?: number) => (value ?? 0).toLocaleString("en-IN");
const day = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

export function FrontPage({ season, onSeason, onOpen, onAsk, onChat, news, hall }: {
  season: number; onSeason: (year: number) => void; onOpen: (ref: Ref) => void; onAsk: (question: string) => void;
  onChat: () => void; news: News | null; hall: ReactNode;
}) {
  const [data, setData] = useState<HomeData | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/api/home?season=${season}`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error("unavailable"))))
      .then((payload: HomeData) => { setData(payload); setFailed(false); })
      .catch((error: Error) => { if (error.name !== "AbortError") setFailed(true); });
    return () => controller.abort();
  }, [season]);

  const page = data?.year === season ? data : null;
  const champion = page?.final?.winner;
  const runnerUp = page?.final?.teams.find((team) => team !== champion);

  return <div className="fp" style={{ "--champion": teamColor(champion) } as CSSProperties}>
    <header className="fp-hero">
      <div className="fp-hero-top">
        <div className="fp-year" aria-label={`IPL ${season}`}><span>IPL</span><strong>{season}</strong></div>
        <div className="fp-champion">
          {page?.final ? <>
            <p className="fp-crown"><i style={{ background: teamColor(champion) }} />Champions</p>
            <h2>{teamName(champion, season)}</h2>
            <p className="fp-final">{page.final.result} in the final against {teamName(runnerUp, season)}, {day(page.final.date)}.</p>
            <div className="fp-actions">
              <button className="fp-primary" onClick={() => page.final && onOpen(page.final)}>Open the final</button>
              <button className="fp-ghost" onClick={() => onAsk(`How was the IPL ${season} final won?`)}>Ask Pandit how it was won</button>
              <button className="fp-ghost fp-chat-only" onClick={onChat}>Chat with Pandit</button>
            </div>
          </> : <>
            <p className="fp-crown">IPL {season}</p>
            <h2>{failed ? "The archive isn't answering" : page ? "No final in the archive" : "Loading the season…"}</h2>
            {failed && <p className="fp-final">Start the API with <code>uv run cricrag serve</code>, then reload.</p>}
          </>}
        </div>
      </div>
      {page && page.eras.length > 0 && <EraChart eras={page.eras} season={season} onSeason={onSeason} />}
    </header>

    {page?.available && <>
      <dl className="fp-numbers">
        <div><dt>Matches played</dt><dd>{num(page.matches)}</dd></div>
        <div><dt>Runs</dt><dd>{num(page.runs)}</dd></div>
        <div><dt>Sixes</dt><dd>{num(page.sixes)}</dd></div>
        <div><dt>Totals of 200+</dt><dd>{num(page.two_hundreds)}</dd></div>
        {page.highest && <div className="fp-highest"><dt>Highest total</dt><dd><button onClick={() => page.highest && onOpen(page.highest)}><i style={{ background: teamColor(page.highest.team) }} />{page.highest.team} {page.highest.runs}/{page.highest.wickets}<small>v {page.highest.opponent}</small></button></dd></div>}
      </dl>

      <section className="fp-caps" aria-label="Season leaders">
        <Cap tone="orange" label="Orange cap" leader={page.orange_cap} unit="runs" />
        <Cap tone="purple" label="Purple cap" leader={page.purple_cap} unit="wickets" />
        <Cap tone="six" label="Most sixes" leader={page.most_sixes} unit="sixes" />
      </section>

      {(page.thrillers?.length ?? 0) > 0 && <section className="fp-section">
        <div className="fp-head"><h3>Closest finishes</h3><p>Ties, wins by five runs or fewer, and chases won with one ball or none to spare.</p></div>
        <ol className="fp-thrillers">{page.thrillers?.map((match) => <li key={match.match_id}>
          <button onClick={() => onOpen(match)}>
            <span className="fp-when">{day(match.date)}<small>{match.title}</small></span>
            <span className="fp-sides">{match.teams.map((team, index) => <span key={team} className={team === match.winner ? "won" : ""}><i style={{ background: teamColor(team) }} />{team}<b>{match.scores[index]}</b></span>)}</span>
            <span className="fp-how">{match.how}</span>
          </button>
        </li>)}</ol>
      </section>}
    </>}

    <ReadingRoom news={news} />
    <section className="fp-hall-mobile" aria-label="All-time lists">{hall}</section>
  </div>;
}

function Cap({ tone, label, leader, unit }: { tone: string; label: string; leader?: Leader; unit: string }) {
  if (!leader) return null;
  return <div className={`fp-cap ${tone}`}><span>{label}</span><strong>{leader.name}</strong><em>{num(leader.value)} {unit}</em></div>;
}

// Average completed first-innings total for every season; click a season to load it.
function EraChart({ eras, season, onSeason }: { eras: Era[]; season: number; onSeason: (year: number) => void }) {
  const [hover, setHover] = useState<number | null>(null);
  const points = useMemo(() => eras.filter((era) => era.first != null), [eras]);
  const geometry = useMemo(() => {
    const width = 900, height = 200, left = 48, right = 14, top = 30, bottom = 38;
    const values = points.map((era) => era.first as number);
    const lo = Math.floor((Math.min(...values) - 6) / 10) * 10, hi = Math.ceil((Math.max(...values) + 6) / 10) * 10;
    const maxSix = Math.max(...points.map((era) => era.sixes));
    const x = (index: number) => left + (index * (width - left - right)) / Math.max(points.length - 1, 1);
    const y = (value: number) => top + ((hi - value) * (height - top - bottom)) / (hi - lo);
    const line = points.map((era, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(era.first as number).toFixed(1)}`).join(" ");
    const area = `${line} L${x(points.length - 1).toFixed(1)},${height - bottom} L${x(0).toFixed(1)},${height - bottom} Z`;
    const ticks = Array.from({ length: (hi - lo) / 10 + 1 }, (_, index) => lo + index * 10).filter((value) => (value - lo) % 20 === 0);
    return { width, height, bottom, x, y, line, area, ticks, maxSix };
  }, [points]);
  const { width, height, bottom, x, y, line, area, ticks, maxSix } = geometry;
  const focus = points.find((era) => era.year === (hover ?? season)) ?? points.at(-1);
  const impact = points.findIndex((era) => era.year === 2023);

  return <figure className="fp-era">
    <figcaption>
      <span>Average first-innings total, every season</span>
      {focus && <span className="fp-readout"><b>{focus.year}</b> {focus.first} runs, {focus.sixes} sixes a match, {focus.run_rate} an over</span>}
    </figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} role="group" aria-label="Average first-innings total by season. Choose a season to open it." onMouseLeave={() => setHover(null)}>
      <defs><linearGradient id="fp-area" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="currentColor" stopOpacity=".28" /><stop offset="1" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs>
      {ticks.map((value) => <g key={value} className="fp-grid"><line x1={48} x2={width - 14} y1={y(value)} y2={y(value)} /><text x={38} y={y(value) + 5} textAnchor="end">{value}</text></g>)}
      {points.map((era, index) => <rect key={`six-${era.year}`} className="fp-six" x={x(index) - 7} width={14} y={height - bottom - (era.sixes / maxSix) * 26} height={(era.sixes / maxSix) * 26} />)}
      {impact > 0 && <g className="fp-mark"><line x1={(x(impact) + x(impact - 1)) / 2} x2={(x(impact) + x(impact - 1)) / 2} y1={10} y2={height - bottom} /><text x={(x(impact) + x(impact - 1)) / 2 - 8} y={20} textAnchor="end">Impact Player rule from 2023</text></g>}
      <path className="fp-area" d={area} fill="url(#fp-area)" />
      <path className="fp-line" d={line} pathLength={1} key={season} />
      {points.map((era, index) => {
        const active = era.year === season;
        return <g key={era.year} className={`fp-point${active ? " active" : ""}`} role="button" tabIndex={0} aria-label={`IPL ${era.year}: ${era.first} average first-innings total`} aria-pressed={active}
          onClick={() => onSeason(era.year)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSeason(era.year); } }}
          onMouseEnter={() => setHover(era.year)} onFocus={() => setHover(era.year)} onBlur={() => setHover(null)}>
          <rect x={x(index) - 22} y={0} width={44} height={height} fill="transparent" />
          <circle cx={x(index)} cy={y(era.first as number)} r={active ? 6 : 3.5} />
          {active && <circle className="fp-ring" cx={x(index)} cy={y(era.first as number)} r={11} />}
          <text x={x(index)} y={height - 10} textAnchor="middle">{String(era.year).slice(2)}</text>
        </g>;
      })}
    </svg>
    <p className="fp-legend"><i className="fp-key-line" />Average first-innings total <i className="fp-key-bar" />Sixes per match</p>
  </figure>;
}

const KIND_LABELS: Record<string, string> = { all: "Everything", rules: "Rule changes", trends: "Trends", records: "Records", news: "News" };

function ReadingRoom({ news }: { news: News | null }) {
  const [kind, setKind] = useState("all");
  if (!news) return null;
  const items = [...news.live, ...news.reading].filter((item) => kind === "all" || item.kind === kind);
  return <section className="fp-section fp-reading" aria-label="Reading room">
    <div className="fp-head"><h3>Reading room</h3><p>Rule changes, trends and records from ESPNcricinfo, with new IPL stories from ESPN&apos;s feed first.</p></div>
    <div className="fp-pills" role="tablist" aria-label="Article type">{["all", ...news.kinds].map((name) => <button key={name} role="tab" aria-selected={kind === name} className={kind === name ? "active" : ""} onClick={() => setKind(name)}>{KIND_LABELS[name] ?? name}</button>)}</div>
    <ul className="fp-articles">{items.map((item) => <li key={item.url} className={`kind-${item.kind}`}>
      <a href={item.url} target="_blank" rel="noopener noreferrer">
        <span className="fp-kind">{item.published ? "New" : KIND_LABELS[item.kind] ?? item.kind}</span>
        <strong>{item.title}</strong>
        <small>{item.source}{item.year ? `, ${item.year}` : item.published ? `, ${day(item.published.slice(0, 10))}` : ""}<span aria-hidden="true">↗</span></small>
      </a>
    </li>)}</ul>
  </section>;
}
