"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, FormEvent, ReactNode } from "react";
import { ClarifyChatCard, ScopeChatCard, StatsChatCard, StatsPanel } from "./stats";
import type { StatsCard, ClarifyCard, ScopeCard } from "./stats";
import { OverChart, PartnershipChart, PhaseChart, PitchCard, usePar, Worm } from "./match-charts";
import { FrontPage } from "./home";
import type { News } from "./home";
import type { Partnership } from "./match-charts";

// ESPN commentary marks emphasis with <b>/<strong>/<i>/<em>. Render those as React elements and drop any other
// tag: nothing is ever injected as HTML, so stray or hostile markup cannot run or show up as text.
const ENTITIES: Record<string, string> = { "&amp;": "&", "&quot;": "\"", "&#39;": "'", "&lt;": "<", "&gt;": ">", "&nbsp;": " " };
function RichText({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let bold = false;
  let italic = false;
  text.split(/(<\/?[a-zA-Z][^>]{0,40}>)/).forEach((chunk, index) => {
    const tag = chunk.match(/^<(\/?)([a-zA-Z]+)/);
    if (tag) {
      const name = tag[2].toLowerCase();
      if (name === "b" || name === "strong") bold = tag[1] !== "/";
      else if (name === "i" || name === "em") italic = tag[1] !== "/";
      else if (name === "br") parts.push(<br key={index} />);
      return;
    }
    if (!chunk) return;
    let node: ReactNode = chunk.replace(/&(amp|quot|#39|lt|gt|nbsp);/g, (entity) => ENTITIES[entity] ?? entity);
    if (italic) node = <em>{node}</em>;
    if (bold) node = <strong>{node}</strong>;
    parts.push(<Fragment key={index}>{node}</Fragment>);
  });
  return <>{parts}</>;
}

// ---------------------------------------------------------------- contracts

type Team = { id: string; name: string; abbr: string; color: string; score: string; score_info: string };
type ArchiveMatch = {
  id: string; season: number; series_slug: string; match_slug: string; title: string; label?: string;
  date: string; status: string; status_text: string; venue: string; teams: Team[]; series_name: string;
};
type Batter = { name: string; dismissal: string | Record<string, string>; runs: number; balls: number; fours: number; sixes: number; strike_rate: string | number; not_out: boolean };
type Bowler = { name: string; overs: string | number; maidens: number; runs: string | number; wickets: number; economy: string | number; dots: number };
type Innings = { number: number; team: string; runs: number; wickets: number; overs: number; extras: number; batters: Batter[]; bowlers: Bowler[] };
type Ball = {
  id: string; inning: number; over?: number; label: string; event: string; runs: number; wicket: boolean; boundary: boolean;
  title: string; text: string; commentary?: string; score: string; win_probability: number | null; batter?: string; bowler?: string; team?: string;
};
type Shift = {
  type: "wicket_cluster" | "scoring_burst" | "squeeze" | "chase_swing" | "missed_chance"; inning: number; innings_team: string;
  from: string; to: string; headline: string; favours: string; magnitude: number; score_before: string; score_after: string;
  decisive?: boolean; dismissed?: string[]; evidence?: { label: string; event: string; text: string }[];
};
type Drop = {
  inning: number; over: string; fielder: string | null; batter: string; bowler: string;
  score_then: number; final: number; added: number; balls_after: number; out: boolean;
};
type HallList = { title: string; group: string; unit: string; rows: { name: string; value: number | string }[] };
type HallOfFame = { lists: HallList[]; span: string };
type Theme = "floodlit" | "night" | "pitch" | "pavilion" | "pinkball";
const THEMES: { id: Theme; label: string; dark: boolean }[] = [
  { id: "floodlit", label: "Floodlit", dark: false }, { id: "night", label: "Night match", dark: true },
  { id: "pitch", label: "Pitch", dark: false }, { id: "pavilion", label: "Pavilion", dark: false },
  { id: "pinkball", label: "Pink ball", dark: true },
];
type MatchDetail = {
  match: ArchiveMatch; innings: Innings[]; balls: Ball[];
  ball_coverage: { level: string; label: string; note: string };
  toss: string | Record<string, unknown>; player_awards: string[];
  partnerships?: Partnership[][];
  analytics: {
    turning_points: { team: string; over: number; swing: number; runs: number; wickets: number }[];
    momentum: { team: string; values: number[] }[];
    shifts?: Shift[];
  };
  consistency?: { status: string; note: string; checks: { innings: number; team: string; canonical: string; reference: string; match: boolean }[] };
  commentary_count?: number;
  drops?: Drop[];
  sources: { name: string; provider: string; available: boolean }[];
};
type MatchTab = "overview" | "scorecard" | "balls" | "momentum";
type UiAction = {
  type: "open_match" | "select_match" | "set_archive_filters" | "set_match_tab" | "set_view" | "navigate" | "theme" | "copy_summary";
  series_slug?: string; match_slug?: string; match_id?: string; season?: number; query?: string; tab?: string; view?: string; section?: string; value?: "light" | "dark";
};
type MatchCard = {
  type: "match"; match: ArchiveMatch; toss?: string; player_of_match: string[];
  innings: { team: string; score: string; overs: number; top_batters: { name: string; runs: number; balls: number; not_out: boolean }[]; top_bowlers: { name: string; wickets: number; runs: number | string; overs: number | string }[] }[];
  momentum_shifts: Shift[]; key_moments: { inning: number; label: string; event: string; title: string; text: string; wicket: boolean; boundary: boolean }[];
  coverage: { level: string; label: string }; consistency: { status: string; note: string }; ball_count: number; commentary_count: number;
};
type ListCard = { type: "match_list"; title: string; matches: { title: string; subtitle: string; meta: string; date: string; label: string; series_slug: string; match_slug: string }[] };
type ChatCard = MatchCard | ListCard | StatsCard | ClarifyCard | ScopeCard;
type ChatMessage = { role: "user" | "assistant"; content: string; cards?: ChatCard[]; via?: string };
type GraphNode = { id: string; label: string; kind: string; description: string };
type AgentGraph = { name: string; version: string; framework: string; nodes: GraphNode[]; edges: { from: string; to: string; condition?: string }[] };
type TraceEvent = {
  sequence: number; node: string; label: string; kind: string; summary: string; patch: Record<string, unknown>;
  snapshot: { phase: string; route: string; tools: string[]; result_count: number; action_count: number; model_used: boolean; validation: string };
};
type AgentResult = { reply: string; ui_actions?: UiAction[]; cards?: ChatCard[]; provider?: string | null; model?: string | null; model_used?: boolean };
type AgentStatus = { status: string; provider: string | null; model: string | null; providers: { name: string; model: string; configured: boolean; ready: boolean }[] };
type DocsCatalog = {
  title: string; version: string; principles: string[];
  sources: { name: string; use: string; mode: string }[];
  apis: { method: string; path: string; use: string; request?: string; response: string }[];
  tools: { name: string; owner: string; use: string }[];
  states: { name: string; meaning: string }[];
  nodes: GraphNode[]; transitions: { from: string; to: string; condition?: string }[];
  coverage: { level: string; meaning: string }[];
};
type DataStatus = { cricsheet: { matches: number; seasons: number[]; synced: boolean; sync_command: string }; canonical_source: string };

// Same-origin by default: the dev server proxies /api to FastAPI, so a single
// tunnel URL carries both the page and the API.
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const FALLBACK_SEASONS = [2025, 2024, 2023];
const TABS: { id: MatchTab; label: string }[] = [
  { id: "overview", label: "Overview" }, { id: "scorecard", label: "Scorecard" },
  { id: "balls", label: "Ball by ball" }, { id: "momentum", label: "Momentum" },
];
const DEFAULT_GRAPH: AgentGraph = {
  name: "Pandit", version: "3", framework: "cricket-rag programs",
  nodes: [
    ["frame", "Question frame", "entity", "Link players, teams, seasons, stage, phase and over in code. No LLM."],
    ["checks", "Fast checks", "routing", "Ambiguous name -> ask. Other competitions and missing data -> say so."],
    ["intent", "Intent form", "model", "One small LLM call fills a fixed-choice form: answer, unit, measure, scope."],
    ["route", "Route table", "routing", "The form picks an ordered list of programs to try."],
    ["program", "Run program", "tool", "story, event, stats, match, counts, team phase, fielding, search or finder."],
    ["shape", "Shape check", "validation", "A result keyed by the wrong unit moves on to the next program."],
    ["write", "Writer", "model", "One LLM call writes up to five cited sentences from the evidence card."],
    ["validate", "Validator", "validation", "Reject uncited facts, numbers not in the evidence, unknown names."],
    ["finalize", "Finalize", "output", "Package the reply, tables, clarify options and UI actions."],
  ].map(([id, label, kind, description]) => ({ id, label, kind, description })),
  edges: [],
};
const SHIFT_LABELS: Record<Shift["type"], string> = {
  wicket_cluster: "Collapse", scoring_burst: "Scoring burst", squeeze: "Squeeze", chase_swing: "Chase swing", missed_chance: "Missed chance",
};

// ---------------------------------------------------------------- helpers

function scoreLabel(team?: Team) {
  return team?.score && team.score !== "—" ? team.score : "Yet to bat";
}

function dismissal(value: Batter["dismissal"]) {
  if (typeof value === "string") return value;
  return value?.long ?? value?.short ?? "";
}

function formatToss(value: MatchDetail["toss"]) {
  if (typeof value === "string") return value || "Unavailable";
  const text = value?.text ?? value?.long ?? value?.summary;
  return typeof text === "string" ? text : "Unavailable";
}

function formatDate(value: string) {
  if (!value) return "Date unavailable";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function plural(count: number, word: string, suffix = "s") {
  return `${count} ${word}${count === 1 ? "" : suffix}`;
}

function seasonFromSlug(seriesSlug: string, fallback: number) {
  const found = seriesSlug.match(/(20\d{2})/);
  return found ? Number(found[1]) : fallback;
}

function placeholderMatch(seriesSlug: string, matchSlug: string, season: number): ArchiveMatch {
  return { id: matchSlug, season, series_slug: seriesSlug, match_slug: matchSlug, title: "IPL match", date: "", status: "", status_text: "", venue: "", teams: [], series_name: `IPL ${season}` };
}

function matchSummary(match: ArchiveMatch) {
  const teams = match.teams.map((team) => `${team.abbr} ${scoreLabel(team)}`).join(" vs ");
  return [match.title, teams, match.status_text, [match.venue, formatDate(match.date)].filter(Boolean).join(" · ")].filter(Boolean).join("\n");
}

function setMatchUrl(match: Pick<ArchiveMatch, "series_slug" | "match_slug"> | null) {
  const url = new URL(window.location.href);
  if (match) {
    url.searchParams.set("series", match.series_slug);
    url.searchParams.set("match", match.match_slug);
  } else {
    url.searchParams.delete("series");
    url.searchParams.delete("match");
  }
  if (url.href !== window.location.href) window.history.pushState({}, "", url);
}

function tabForAction(action: UiAction): MatchTab | null {
  const target = action.type === "set_match_tab" ? action.tab : action.type === "set_view" || action.type === "navigate" ? action.view ?? action.section : undefined;
  if (!target) return null;
  if (target === "scorecard" || target === "players") return "scorecard";
  if (target === "analytics" || target === "momentum") return "momentum";
  if (target === "balls" || target === "commentary") return "balls";
  if (target === "overview") return "overview";
  return null;
}

function teamColor(detail: MatchDetail | null, name?: string) {
  return detail?.match.teams.find((team) => team.abbr === name || team.name === name)?.color ?? "var(--muted)";
}

// ---------------------------------------------------------------- app

export default function Home() {
  const [theme, setTheme] = useState<Theme>("floodlit");
  const dark = THEMES.find((item) => item.id === theme)?.dark ?? false;
  const themeMenu = useRef<HTMLDetailsElement>(null);
  const [seasons, setSeasons] = useState<number[]>(FALLBACK_SEASONS);
  const [season, setSeason] = useState(2025);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [matches, setMatches] = useState<ArchiveMatch[]>([]);
  const [total, setTotal] = useState(0);
  const [archiveSource, setArchiveSource] = useState("");
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [archiveError, setArchiveError] = useState("");
  const [selected, setSelected] = useState<ArchiveMatch | null>(null);
  const [detail, setDetail] = useState<MatchDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [matchTab, setMatchTab] = useState<MatchTab>("overview");
  const [inningFilter, setInningFilter] = useState(0);
  const [docsOpen, setDocsOpen] = useState(false);
  const [docs, setDocs] = useState<DocsCatalog | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  const [dataStatus, setDataStatus] = useState<DataStatus | null>(null);
  const [hall, setHall] = useState<HallOfFame | null>(null);
  const [news, setNews] = useState<News | null>(null);
  const [graph, setGraph] = useState<AgentGraph>(DEFAULT_GRAPH);
  const [trace, setTrace] = useState<TraceEvent[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: "assistant", content: "I’m Pandit. Ask about any IPL match — the result, the scorecard, a specific over, or where the game turned. I’ll pull the real match data and open it alongside our chat." },
  ]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [toast, setToast] = useState("");
  const [mobileView, setMobileView] = useState<"chat" | "matches" | "match" | "stats">("matches");
  // Stats opens the workspace on demand (like a match does); it can sit over an open match.
  const [showStats, setShowStats] = useState(false);
  const openStats = useCallback(() => { setShowStats(true); setMobileView("stats"); }, []);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const archiveRequest = useRef(0);
  const detailRequest = useRef(0);

  const notify = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast((current) => current === message ? "" : current), 2400);
  }, []);

  const loadArchive = useCallback(async (year: number, term: string) => {
    const request = ++archiveRequest.current;
    setArchiveLoading(true);
    setArchiveError("");
    try {
      const params = new URLSearchParams({ season: String(year), q: term, limit: "100", offset: "0" });
      const response = await fetch(`${API_BASE}/api/ipl/matches?${params}`);
      if (!response.ok) throw new Error("Archive unavailable");
      const payload = await response.json();
      if (request !== archiveRequest.current) return;
      setMatches(payload.matches ?? []);
      setTotal(payload.pagination?.total ?? 0);
      setArchiveSource(payload.source ?? "");
    } catch {
      if (request !== archiveRequest.current) return;
      setMatches([]);
      setTotal(0);
      setArchiveError("The cricket-rag API is not responding. Start it with `uv run cricrag serve` and try again.");
    } finally {
      if (request === archiveRequest.current) setArchiveLoading(false);
    }
  }, []);

  const openMatch = useCallback(async (match: ArchiveMatch, pushHistory = true, tab: MatchTab = "overview") => {
    const request = ++detailRequest.current;
    setSelected(match);
    setDetail(null);
    setDetailLoading(true);
    setMatchTab(tab);
    setInningFilter(0);
    setShowStats(false);
    setMobileView("match");
    if (pushHistory) setMatchUrl(match);
    try {
      const response = await fetch(`${API_BASE}/api/ipl/matches/${encodeURIComponent(match.series_slug)}/${encodeURIComponent(match.match_slug)}`);
      if (!response.ok) throw new Error("Match unavailable");
      const payload = await response.json() as MatchDetail;
      if (request !== detailRequest.current) return;
      setDetail(payload);
      setSelected(payload.match);
    } catch {
      if (request === detailRequest.current) setDetail(null);
    } finally {
      if (request === detailRequest.current) setDetailLoading(false);
    }
  }, []);

  const clearMatch = useCallback(() => {
    detailRequest.current += 1;
    setSelected(null);
    setDetail(null);
    setDetailLoading(false);
  }, []);

  const openBySlugs = useCallback((seriesSlug: string, matchSlug: string, tab: MatchTab = "overview", pushHistory = true) => {
    const found = matches.find((item) => item.series_slug === seriesSlug && item.match_slug === matchSlug);
    void openMatch(found ?? placeholderMatch(seriesSlug, matchSlug, seasonFromSlug(seriesSlug, season)), pushHistory, tab);
  }, [matches, openMatch, season]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(query.trim()); }, 280);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const request = window.setTimeout(() => { void loadArchive(season, search); }, 0);
    return () => window.clearTimeout(request);
  }, [season, search, loadArchive]);

  // Deep links: ?series=…&match=… opens that match once on load.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const params = new URL(window.location.href).searchParams;
      const seriesSlug = params.get("series");
      const matchSlug = params.get("match");
      if (seriesSlug && matchSlug) {
        setSeason(seasonFromSlug(seriesSlug, 2025));
        void openMatch(placeholderMatch(seriesSlug, matchSlug, seasonFromSlug(seriesSlug, 2025)), false);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [openMatch]);

  useEffect(() => {
    const onPop = () => {
      const params = new URL(window.location.href).searchParams;
      const seriesSlug = params.get("series");
      const matchSlug = params.get("match");
      if (seriesSlug && matchSlug) openBySlugs(seriesSlug, matchSlug, "overview", false);
      else clearMatch();
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [clearMatch, openBySlugs]);

  useEffect(() => {
    const get = (path: string) => fetch(`${API_BASE}${path}`).then((response) => response.ok ? response.json() : Promise.reject());
    Promise.allSettled([get("/api/agent/status"), get("/api/agent/graph"), get("/api/docs/catalog"), get("/api/ipl/seasons"), get("/api/data/status"), get("/api/hall-of-fame"), get("/api/news")])
      .then(([status, graphResult, docsResult, seasonResult, dataResult, hallResult, newsResult]) => {
        if (hallResult.status === "fulfilled") setHall(hallResult.value);
        if (newsResult.status === "fulfilled") setNews(newsResult.value);
        if (status.status === "fulfilled") setAgent(status.value);
        if (graphResult.status === "fulfilled") setGraph(graphResult.value);
        if (docsResult.status === "fulfilled") setDocs(docsResult.value);
        if (seasonResult.status === "fulfilled") {
          const years = (seasonResult.value.seasons ?? []).map((item: { year: number }) => item.year);
          if (years.length) setSeasons(years);
        }
        if (dataResult.status === "fulfilled") setDataStatus(dataResult.value);
      });
  }, []);

  // The chosen theme is a per-viewer convenience; storage may be unavailable, and that's fine.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const saved = (new URL(window.location.href).searchParams.get("theme") ?? window.localStorage.getItem("cricrag-theme")) as Theme | null;
        if (saved && THEMES.some((item) => item.id === saved)) setTheme(saved);
      } catch { /* no storage: keep the default */ }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  const chooseTheme = useCallback((next: Theme) => {
    setTheme(next);
    if (themeMenu.current) themeMenu.current.open = false;
    try { window.localStorage.setItem("cricrag-theme", next); } catch { /* no storage */ }
  }, []);

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, chatLoading]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (traceOpen) setTraceOpen(false);
        else if (docsOpen) setDocsOpen(false);
        return;
      }
      const target = event.target as HTMLElement | null;
      const typing = target?.closest("input, textarea, [contenteditable='true']");
      if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        setMobileView("matches");
        window.setTimeout(() => searchRef.current?.focus(), 0);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [docsOpen, traceOpen]);

  async function copyText(text: string, message: string) {
    try {
      await navigator.clipboard.writeText(text);
      notify(message);
    } catch {
      notify("Copy failed. Your browser blocked clipboard access.");
    }
  }

  function shareMatch() {
    if (selected) void copyText(`${matchSummary(selected)}\n${window.location.href}`, "Match summary and link copied");
  }

  function closeMatch() {
    clearMatch();
    setMatchUrl(null);
    setMobileView("matches");
  }

  function applyActions(actions: UiAction[]) {
    // Open first: opening resets the workspace tab.
    const requestedTab = actions.map(tabForAction).find((tab) => tab !== null) ?? null;
    const opening = actions.find((action) => action.type === "open_match" && action.series_slug && action.match_slug);
    if (opening?.series_slug && opening.match_slug) openBySlugs(opening.series_slug, opening.match_slug, requestedTab ?? "overview");
    for (const action of actions) {
      if (action.type === "set_archive_filters") {
        if (action.season && seasons.includes(action.season)) setSeason(action.season);
        setQuery(action.query ?? "");
      }
      if (action.type === "theme" && action.value) setTheme(action.value === "dark" ? "night" : "floodlit");
      if (action.type === "copy_summary") shareMatch();
    }
    if (!opening && requestedTab && selected) setMatchTab(requestedTab);
  }

  async function sendChat(event?: FormEvent, suggestion?: string) {
    event?.preventDefault();
    const text = (suggestion ?? chatInput).trim();
    if (!text || chatLoading) return;
    const history = messages.slice(-8).map(({ role, content }) => ({ role, content }));
    setMessages((current) => [...current, { role: "user", content: text }]);
    setChatInput("");
    setChatLoading(true);
    setTrace([]);
    setMobileView("chat");
    try {
      const response = await fetch(`${API_BASE}/api/agent/chat/stream`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text, history,
          ui_context: {
            season, query: search, selected_match: selected?.id, series_slug: selected?.series_slug,
            match_slug: selected?.match_slug, teams: selected?.teams.map((team) => team.abbr), active_tab: matchTab,
            match_season: selected?.season, match_label: selected?.label,
            ball_coverage: detail?.ball_coverage.level, theme: dark ? "dark" : "light",
          },
        }),
      });
      if (!response.ok || !response.body) throw new Error("Agent unavailable");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let result: AgentResult | null = null;
      const consume = (line: string) => {
        if (!line.trim()) return;
        const item = JSON.parse(line) as { event: "trace" | "result"; data: TraceEvent | AgentResult };
        if (item.event === "trace") setTrace((current) => [...current, item.data as TraceEvent]);
        else result = item.data as AgentResult;
      };
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        lines.forEach(consume);
        if (done) break;
      }
      consume(buffer);
      if (!result) throw new Error("No result");
      const final = result as AgentResult;
      const via = final.model_used && final.provider ? `${final.provider} · ${final.model} · validated` : "from the evidence (no LLM)";
      setMessages((current) => [...current, { role: "assistant", content: final.reply, cards: final.cards ?? [], via }]);
      applyActions(final.ui_actions ?? []);
    } catch {
      setMessages((current) => [...current, { role: "assistant", content: "I lost the backend for a moment. Your matches are safe — send that again once it’s back." }]);
    } finally {
      setChatLoading(false);
    }
  }

  const visibleBalls = useMemo(() => detail?.balls.filter((ball) => !inningFilter || ball.inning === inningFilter) ?? [], [detail, inningFilter]);
  const llmLabel = agent?.provider ? `${agent.provider} · ${agent.model}` : agent ? "LLM offline · grounded recaps" : "Checking model…";
  const suggestions = selected
    ? ["Where did this match turn?", "Summarise the chase", "Who bowled the best spell in this match?", "What happened in the last over?"]
    : [`Tell me about the ${season} final`, `Most sixes in IPL ${season}`, "Kohli chasing vs batting first", "Who are the best finishers in the IPL?"];

  return (
    <div className={`app ${dark ? "dark" : "light"} theme-${theme} view-${mobileView}${selected ? " has-match" : ""}${showStats ? " stats-open" : ""}${!selected && !showStats ? " home-open" : ""}`}>
      <header className="topbar">
        <button className="brand" onClick={() => { if (selected) closeMatch(); setShowStats(false); setMobileView("matches"); }} aria-label="cricket-rag front page"><span className="brand-mark" /><span>cricket<span>rag</span></span></button>
        <nav className="topnav" aria-label="Main">
          <button className={!showStats ? "active" : ""} onClick={() => { setShowStats(false); setMobileView(selected ? "match" : "matches"); }}>Match centre</button>
          <button className={showStats ? "active" : ""} onClick={openStats} aria-label="Open IPL stats">Stats</button>
          <button onClick={() => setDocsOpen(true)} aria-label="Open documentation">Docs</button>
        </nav>
        <details className="theme-picker" ref={themeMenu}>
          <summary aria-label="Choose a colour theme"><i className={`swatch ${theme}`} /><span>{THEMES.find((item) => item.id === theme)?.label}</span></summary>
          <div className="theme-menu" role="menu">{THEMES.map((item) => <button key={item.id} role="menuitemradio" aria-checked={item.id === theme} className={item.id === theme ? "active" : ""} onClick={() => chooseTheme(item.id)}><i className={`swatch ${item.id}`} />{item.label}</button>)}</div>
        </details>
      </header>
      <div className="ticker" aria-label={`IPL ${season} results`}>
        <div className="ticker-controls">
          <label className="ticker-season" title={archiveSource}><span>IPL</span><select id="season" value={season} onChange={(event) => { setSeason(Number(event.target.value)); }} aria-label="IPL season">
            {seasons.map((year) => <option key={year} value={year}>{year}</option>)}
          </select><small>{archiveLoading ? "…" : plural(total, "match", "es")}</small></label>
          <label className="archive-search"><span>⌕</span><input ref={searchRef} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { setQuery(""); event.currentTarget.blur(); } }} placeholder="Team, venue, final…" aria-label="Search this season's matches" />{query ? <button onClick={() => { setQuery(""); searchRef.current?.focus(); }} aria-label="Clear search">×</button> : <kbd title="Press / to search">/</kbd>}</label>
        </div>
        <div className="ticker-track" key={`${season}-${search}`}>
          {archiveLoading && Array.from({ length: 4 }).map((_, index) => <span className="ticker-card skeleton" key={index} />)}
          {!archiveLoading && matches.map((match, index) => <button className={`ticker-card${selected?.id === match.id ? " selected" : ""}`} style={{ "--i": Math.min(index, 12) } as CSSProperties} key={`t-${match.id}`} onClick={() => void openMatch(match)}>
            <small>{match.label && match.label !== "Match" ? match.label : "Result"} · {formatDate(match.date)}</small>
            {match.teams.map((team) => <span key={team.abbr}><i style={{ background: team.color }} /><strong>{team.abbr}</strong><b>{scoreLabel(team)}</b></span>)}
            <em>{match.status_text}</em>
          </button>)}
          {!archiveLoading && !matches.length && <span className="ticker-empty">{archiveError ? <>Archive offline <button onClick={() => void loadArchive(season, search)}>Try again</button></> : search ? `Nothing in IPL ${season} matches “${search}”` : "No matches"}</span>}
        </div>
      </div>
      <aside className="rail" aria-label="IPL archive">
        <div className="rail-scroll">
          <HallOfFamePanel hall={hall} offline={Boolean(archiveError)} />
        </div>
        <footer className="data-status"><span className={`status-dot ${dataStatus?.cricsheet.synced ? "connected" : archiveError ? "offline" : ""}`} /><div><strong>{dataStatus ? dataStatus.canonical_source : "Checking data…"}</strong><small>{dataStatus?.cricsheet.synced ? `${dataStatus.cricsheet.matches} matches · ${dataStatus.cricsheet.seasons.at(-1)}–${dataStatus.cricsheet.seasons[0]}` : dataStatus ? `Run: ${dataStatus.cricsheet.sync_command}` : ""}</small></div></footer>
      </aside>

      <main className="chat" aria-label="Pandit cricket assistant">
        <header className="chat-head">
          <span className="pandit-avatar">P</span>
          <div><h1>Pandit</h1><small><i className={agent?.provider ? "ready" : agent ? "offline" : ""} />{llmLabel}</small></div>
          <button className={`trace-toggle${traceOpen ? " active" : ""}`} onClick={() => setTraceOpen((value) => !value)} aria-label="Toggle agent trace">Trace{trace.length ? <b>{trace.length}</b> : null}</button>
        </header>
        {selected && <button className="chat-context" onClick={() => setMobileView("match")}><span>DISCUSSING</span><strong>{selected.title} · {selected.series_name}</strong><small>{TABS.find((tab) => tab.id === matchTab)?.label}</small></button>}
        <div className="chat-messages">
          {messages.map((message, index) => <div className={`chat-message ${message.role}`} key={`${message.role}-${index}`}>
            {message.role === "assistant" && <span className="message-mark">P</span>}
            <div className="message-body">
              <p>{message.content}</p>
              {message.cards?.map((card, cardIndex) => card.type === "match"
                ? <MatchChatCard key={cardIndex} card={card} onOpen={(tab) => openBySlugs(card.match.series_slug, card.match.match_slug, tab)} />
                : card.type === "stats"
                  ? <StatsChatCard key={cardIndex} card={card} onOpenStats={openStats} />
                  : card.type === "clarify"
                    ? <ClarifyChatCard key={cardIndex} card={card} disabled={chatLoading} onAsk={(question) => void sendChat(undefined, question)} />
                    : card.type === "scope"
                      ? <ScopeChatCard key={cardIndex} card={card} disabled={chatLoading} onAsk={(question) => void sendChat(undefined, question)} />
                      : <ListChatCard key={cardIndex} card={card} onOpen={(item) => openBySlugs(item.series_slug, item.match_slug)} />)}
              {message.via && <small className="via">{message.via}</small>}
            </div>
          </div>)}
          {chatLoading && <div className="chat-message assistant"><span className="message-mark">P</span><div className="thinking"><i /><i /><i /><span>{trace.at(-1)?.label ?? "Thinking"}</span></div></div>}
          <div ref={chatEndRef} />
        </div>
        <div className="suggestion-list">{suggestions.map((prompt) => <button key={prompt} disabled={chatLoading} onClick={() => void sendChat(undefined, prompt)}>{prompt}</button>)}</div>
        <form className="chat-composer" onSubmit={sendChat}>
          <textarea ref={composerRef} rows={2} aria-label="Message Pandit" value={chatInput} onChange={(event) => setChatInput(event.target.value)} placeholder="Ask about any IPL match, over, player, or turning point…" onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendChat(); } }} />
          <button disabled={!chatInput.trim() || chatLoading} aria-label="Send message">↑</button>
        </form>
      </main>

      <section className="workspace" aria-label="Match workspace">
        {showStats && <StatsPanel season={season} seasons={seasons} matchOpen={Boolean(selected)}
          onBackToMatch={() => { setShowStats(false); setMobileView("match"); }}
          onClose={() => { setShowStats(false); setMobileView("chat"); }} />}
        {!selected && !showStats && <div className="workspace-body fp-body"><FrontPage season={season} onSeason={setSeason} news={news}
          onOpen={(ref) => { if (ref.series_slug && ref.match_slug) openBySlugs(ref.series_slug, ref.match_slug); }}
          onAsk={(question) => { setMobileView("chat"); void sendChat(undefined, question); }}
          onChat={() => setMobileView("chat")}
          hall={<HallOfFamePanel hall={hall} offline={Boolean(archiveError)} />} /></div>}
        {selected && !showStats && <>
          <div className="match-hero">
            <div className="hero-actions"><button className="back-link" onClick={closeMatch}>← Close</button><button className="share-button" onClick={shareMatch}>Copy summary + link</button></div>
            <div className="match-meta"><span>{selected.series_name}{selected.label && selected.label !== "Match" ? ` · ${selected.label}` : ""}</span><span>{formatDate(selected.date)}</span><span>{selected.venue}</span></div>
            <div className="match-scoreboard">{selected.teams.map((team) => <div className="score-team" key={team.abbr}><i style={{ borderColor: team.color }}>{team.abbr}</i><span><small>{team.name}</small><strong>{scoreLabel(team)}</strong><em>{team.score_info}</em></span></div>)}</div>
            <div className="result-strip"><span>Result</span><strong>{selected.status_text || (detailLoading ? "Loading…" : "Unavailable")}</strong></div>
          </div>
          <div className="detail-tabs" role="tablist" aria-label="Match sections">{TABS.map((tab) => <button role="tab" aria-selected={matchTab === tab.id} className={matchTab === tab.id ? "active" : ""} key={tab.id} onClick={() => setMatchTab(tab.id)}>{tab.label}</button>)}</div>
          <div className="workspace-body">
            {detailLoading && <div className="detail-loading"><span /><strong>Loading this match…</strong><small>Scorecard → deliveries → commentary → momentum</small></div>}
            {!detailLoading && !detail && <div className="error-state"><strong>Match detail unavailable</strong><p>Try again when the data source responds.</p><button onClick={() => void openMatch(selected, false)}>Retry</button></div>}
            {detail && matchTab === "overview" && <Overview detail={detail} onTab={setMatchTab} />}
            {detail && matchTab === "scorecard" && <Scorecard innings={detail.innings} />}
            {detail && matchTab === "balls" && <BallByBall detail={detail} balls={visibleBalls} filter={inningFilter} onFilter={setInningFilter} />}
            {detail && matchTab === "momentum" && <Momentum detail={detail} onAsk={(text) => void sendChat(undefined, text)} />}
          </div>
        </>}
      </section>

      <nav className="mobile-nav" aria-label="Sections">
        {(["matches", "chat", "stats", "match"] as const).map((view) => <button key={view} className={mobileView === view ? "active" : ""} onClick={() => { if (view === "stats" || view === "match") setShowStats(view === "stats"); setMobileView(view); }} disabled={view === "match" && !selected}>{{ matches: "Home", chat: "Pandit", stats: "Stats", match: "Match" }[view]}</button>)}
      </nav>
      <DocsDrawer open={docsOpen} docs={docs} onClose={() => setDocsOpen(false)} />
      <TraceDrawer open={traceOpen} graph={graph} trace={trace} loading={chatLoading} onClose={() => setTraceOpen(false)} />
      <div className={`toast${toast ? " show" : ""}`} role="status" aria-live="polite">{toast}</div>
    </div>
  );
}

// ---------------------------------------------------------------- chat cards

function MatchChatCard({ card, onOpen }: { card: MatchCard; onOpen: (tab: MatchTab) => void }) {
  const decisive = card.momentum_shifts.find((shift) => shift.decisive);
  return <article className="chat-card">
    <header><div><p className="section-kicker">{card.match.series_name}{card.match.label && card.match.label !== "Match" ? ` · ${card.match.label}` : ""} · {formatDate(card.match.date)}</p><h3>{card.match.title}</h3></div><span className={`coverage ${card.coverage.level}`}>{card.ball_count} balls{card.commentary_count ? ` · ${card.commentary_count} commentary` : ""}</span></header>
    <p className="chat-card-result">{card.match.status_text}</p>
    <div className="chat-card-innings">{card.innings.map((inning) => <div key={inning.team}>
      <div className="innings-line"><strong>{inning.team}</strong><b>{inning.score}</b><small>{inning.overs} ov</small></div>
      <ul>
        {inning.top_batters.slice(0, 2).map((row) => <li key={row.name}>{row.name} <b>{row.runs}{row.not_out ? "*" : ""}</b> ({row.balls})</li>)}
        {inning.top_bowlers.slice(0, 1).map((row) => <li key={row.name} className="bowl">{row.name} <b>{row.wickets}/{row.runs}</b> ({row.overs})</li>)}
      </ul>
    </div>)}</div>
    {decisive && <div className="chat-card-shift"><span>{SHIFT_LABELS[decisive.type]}</span><p><strong>{decisive.innings_team}, overs {decisive.from}–{decisive.to}:</strong> {decisive.headline}.</p></div>}
    <footer>
      {card.player_of_match.length > 0 && <small>Player of the match: <strong>{card.player_of_match.join(", ")}</strong></small>}
      <div>{(["overview", "scorecard", "balls", "momentum"] as MatchTab[]).map((tab) => <button key={tab} onClick={() => onOpen(tab)}>{TABS.find((item) => item.id === tab)?.label}</button>)}</div>
    </footer>
    {card.consistency?.status === "verified" && <small className="verified">✓ {card.consistency.note}</small>}
  </article>;
}

function ListChatCard({ card, onOpen }: { card: ListCard; onOpen: (item: ListCard["matches"][number]) => void }) {
  return <article className="chat-card list-card"><header><h3>{card.title}</h3><span className="coverage">{plural(card.matches.length, "match", "es")}</span></header>
    <div>{card.matches.map((item) => <button key={item.match_slug} onClick={() => onOpen(item)}><span>{formatDate(item.date)}{item.label ? ` · ${item.label}` : ""}</span><strong>{item.title}</strong><small>{item.subtitle}</small></button>)}</div>
  </article>;
}

// ---------------------------------------------------------------- workspace tabs

type StoryLine = { kicker: string; text: string };

// Up to `count` items spread across the list, keeping match order.
function spread<T>(items: T[], count: number) {
  if (items.length <= count) return items;
  return Array.from({ length: count }, (_, index) => items[Math.round((index * (items.length - 1)) / (count - 1))]);
}

// A deterministic recap: every number comes straight from the scorecard,
// deliveries, or detected momentum shifts.
function buildMatchStory(detail: MatchDetail): { lines: StoryLine[]; moments: Ball[] } {
  const lines: StoryLine[] = [];
  const { match, innings, analytics } = detail;
  if (match.status_text) lines.push({ kicker: "Result", text: `${match.status_text}${match.venue ? ` at ${match.venue}` : ""}.` });
  for (const inning of innings) {
    const bat = [...inning.batters].sort((a, b) => b.runs - a.runs || a.balls - b.balls)[0];
    const bowl = [...inning.bowlers].sort((a, b) => b.wickets - a.wickets || Number(a.runs) - Number(b.runs))[0];
    const fielding = innings.find((other) => other.team !== inning.team)?.team;
    let text = `${inning.team} made ${inning.runs}/${inning.wickets} in ${inning.overs} overs`;
    if (bat) text += `, led by ${bat.name} (${bat.runs}${bat.not_out ? "*" : ""} off ${bat.balls})`;
    if (bowl && bowl.wickets > 0) text += `; ${bowl.name} took ${bowl.wickets}/${bowl.runs}${fielding ? ` for ${fielding}` : ""}`;
    lines.push({ kicker: inning.number === 1 ? "First innings" : "Chase", text: `${text}.` });
  }
  const decisive = analytics.shifts?.find((shift) => shift.decisive);
  if (decisive) lines.push({ kicker: "Turning point", text: `${SHIFT_LABELS[decisive.type]} in the ${decisive.innings_team} innings, overs ${decisive.from}–${decisive.to}: ${decisive.headline} (${decisive.score_before} → ${decisive.score_after}).` });
  const costliest = [...(detail.drops ?? [])].sort((a, b) => b.added - a.added)[0];
  if (costliest) lines.push({ kicker: "Costliest drop", text: dropText(costliest) });
  const stands = (detail.partnerships ?? []).flatMap((rows, index) => rows.map((row) => ({ ...row, team: innings[index]?.team })));
  const best = stands.sort((a, b) => b.runs - a.runs || a.balls - b.balls)[0];
  if (best && best.players.length) lines.push({ kicker: "Best stand", text: `${best.players.join(" and ")} added ${best.runs}${best.unbroken ? " unbroken" : ""} off ${best.balls} balls${best.team ? ` for ${best.team}` : ""}.` });
  const candidates = detail.balls.filter((ball) => ball.wicket || (ball.boundary && ball.event === "6"));
  return { lines, moments: spread(candidates.length ? candidates : detail.balls.filter((ball) => ball.boundary), 6) };
}

function dropText(drop: Drop) {
  const who = drop.fielder ? `${drop.fielder} dropped ${drop.batter}` : `${drop.batter} was dropped`;
  const after = drop.out ? `before falling for ${drop.final}` : `to finish on ${drop.final} not out`;
  return `${who} on ${drop.score_then} at ${drop.over} off ${drop.bowler}'s bowling; ${drop.batter} added ${drop.added} more ${after}.`;
}

function DropsCard({ detail }: { detail: MatchDetail }) {
  const drops = detail.drops ?? [];
  if (!drops.length) return null;
  const cost = drops.reduce((sum, drop) => sum + drop.added, 0);
  return <article className="detail-card drops-card"><div className="card-title"><div><p className="section-kicker">DROPPED CATCHES</p><h3>What the misses cost</h3></div><span className="drop-total" title="Runs the dropped batters scored after their reprieve">+{cost} runs</span></div>
    <ol className="drop-list">{drops.map((drop) => <li key={`${drop.inning}-${drop.over}`}>
      <span className="drop-when"><b>{teamAbbr(detail, detail.innings.find((inning) => inning.number === drop.inning)?.team) ?? `Inns ${drop.inning}`} {drop.over}</b><small>{drop.fielder ?? "fielder not named"}</small></span>
      <span className="drop-who"><strong>{drop.batter} <em>on {drop.score_then}</em></strong><small>off {drop.bowler}</small></span>
      <span className="drop-cost"><b>+{drop.added}</b><small>{drop.final}{drop.out ? "" : "*"} · {plural(drop.balls_after, "ball")} more</small></span>
    </li>)}</ol>
    <p className="card-copy">Audited drops only (medium confidence or better). Runs are the batter’s own, from the delivery after the drop to the end of the innings.</p>
  </article>;
}

function HallOfFamePanel({ hall, offline }: { hall: HallOfFame | null; offline: boolean }) {
  const groups = useMemo(() => Array.from(new Set((hall?.lists ?? []).map((list) => list.group))), [hall]);
  const [group, setGroup] = useState("batting");
  if (!hall) return <div className="hall">{offline ? <div className="error-state"><strong>Archive unavailable</strong><p>Start the API to load the all-time lists.</p></div> : Array.from({ length: 3 }).map((_, index) => <div className="hall-list skeleton" key={index} />)}</div>;
  return <div className="hall">
    <div className="hall-head"><strong>All-time IPL</strong><span>{hall.span}</span></div>
    <div className="hall-groups">{groups.map((name) => <button key={name} className={group === name ? "active" : ""} onClick={() => setGroup(name)}>{name}</button>)}</div>
    <div className="hall-scroll" key={group}>{hall.lists.filter((list) => list.group === group).map((list, index) => <section className="hall-list" style={{ "--i": index } as CSSProperties} key={list.title}>
      <h4>{list.title}</h4>
      <ol>{list.rows.map((row, index) => <li key={`${row.name}-${index}`}><span>{index + 1}</span><strong>{row.name}</strong><b>{row.value}{list.unit && index === 0 ? <small> {list.unit}</small> : null}</b></li>)}</ol>
    </section>)}</div>
  </div>;
}

function teamAbbr(detail: MatchDetail, name?: string) {
  if (!name) return undefined;
  return detail.match.teams.find((team) => team.name === name || team.abbr === name)?.abbr ?? name;
}

function inningsTag(detail: MatchDetail, ball: Ball) {
  return teamAbbr(detail, ball.team ?? detail.innings.find((inning) => inning.number === ball.inning)?.team) ?? `Inns ${ball.inning}`;
}

function Overview({ detail, onTab }: { detail: MatchDetail; onTab: (tab: MatchTab) => void }) {
  const { lines, moments } = useMemo(() => buildMatchStory(detail), [detail]);
  return <div className="overview-grid">
    <article className="detail-card story-card"><div className="card-title"><div><p className="section-kicker">MATCH STORY</p><h3>How it was won</h3></div><span className="story-badge" title="Built only from scorecard, deliveries and commentary">Grounded recap</span></div>
      <ol className="story-lines">{lines.map((line) => <li key={line.kicker}><span>{line.kicker}</span><p>{line.text}</p></li>)}</ol>
      {moments.length > 0 && <div className="key-moments"><p className="section-kicker">KEY MOMENTS</p>{moments.map((ball) => <div className={`moment${ball.wicket ? " wicket" : " boundary"}`} key={ball.id}><b>{inningsTag(detail, ball)} {ball.label}</b><em>{ball.event}</em><span><strong>{ball.title}</strong><RichText text={ball.commentary || ball.text} /></span></div>)}<button className="wide-action" onClick={() => onTab("balls")}>Full delivery timeline →</button></div>}
    </article>
    <DropsCard detail={detail} />
    {(detail.partnerships?.length ?? 0) > 0 && <article className="detail-card story-card"><div className="card-title"><div><p className="section-kicker">PARTNERSHIPS</p><h3>How each innings was built</h3></div></div>
      <PartnershipChart innings={detail.innings.map((inning, index) => ({ team: inning.team, color: teamColor(detail, inning.team), rows: detail.partnerships?.[index] ?? [] }))} />
    </article>}
    <article className="detail-card"><div className="card-title"><div><p className="section-kicker">MATCH NOTES</p><h3>Known facts</h3></div></div><dl className="facts"><div><dt>Toss</dt><dd>{formatToss(detail.toss)}</dd></div><div><dt>Player of the match</dt><dd>{detail.player_awards.join(", ") || "Unavailable"}</dd></div><div><dt>Deliveries</dt><dd>{detail.balls.length} ({detail.ball_coverage.label}){detail.commentary_count ? `, ${detail.commentary_count} with commentary` : ""}</dd></div></dl></article>
    <PitchCard matchSlug={detail.match.match_slug} />
    <article className="detail-card"><div className="card-title"><div><p className="section-kicker">PROVENANCE</p><h3>Where this came from</h3></div>{detail.consistency && <span className={`consistency ${detail.consistency.status}`}>{detail.consistency.status}</span>}</div>
      {detail.sources.map((source) => <div className="source-row" key={source.name}><i className={source.available ? "on" : ""} /><span><strong>{source.name}</strong><small>{source.provider}</small></span></div>)}
      {detail.consistency && <p className="card-copy">{detail.consistency.note}{detail.consistency.checks.length > 0 && ` ${detail.consistency.checks.map((check) => `${check.team}: ${check.canonical} vs ${check.reference}`).join("; ")}.`}</p>}
    </article>
  </div>;
}

function Scorecard({ innings }: { innings: Innings[] }) {
  return <div className="scorecard-stack">{innings.map((inning) => <article className="detail-card scorecard-card" key={inning.number}><div className="innings-title"><span>{inning.number}</span><div><p className="section-kicker">{inning.number === 1 ? "FIRST INNINGS" : "SECOND INNINGS"}</p><h3>{inning.team}</h3></div><strong>{inning.runs}/{inning.wickets} <small>{inning.overs} ov · extras {inning.extras}</small></strong></div>
    <div className="table-wrap"><table><thead><tr><th>Batter</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead><tbody>{inning.batters.map((batter) => <tr key={batter.name}><td><strong>{batter.name}{batter.not_out ? "*" : ""}</strong><small>{dismissal(batter.dismissal) || (batter.not_out ? "not out" : "")}</small></td><td><b>{batter.runs}</b></td><td>{batter.balls}</td><td>{batter.fours}</td><td>{batter.sixes}</td><td>{batter.strike_rate}</td></tr>)}</tbody></table></div>
    <div className="table-wrap bowling-table"><table><thead><tr><th>Bowler</th><th>O</th><th>M</th><th>R</th><th>W</th><th>Econ</th><th>Dots</th></tr></thead><tbody>{inning.bowlers.map((bowler) => <tr key={bowler.name}><td><strong>{bowler.name}</strong></td><td>{bowler.overs}</td><td>{bowler.maidens}</td><td>{bowler.runs}</td><td><b>{bowler.wickets}</b></td><td>{bowler.economy}</td><td>{bowler.dots}</td></tr>)}</tbody></table></div>
  </article>)}</div>;
}

function overNumber(ball: Ball) {
  return ball.over ?? Number.parseInt(ball.label, 10);
}

// Group consecutive deliveries into overs so a 240-ball match stays scannable.
function groupOvers(balls: Ball[]) {
  const groups: { key: string; inning: number; over: number; balls: Ball[] }[] = [];
  for (const ball of balls) {
    const over = overNumber(ball);
    const last = groups.at(-1);
    if (last && last.inning === ball.inning && last.over === over) last.balls.push(ball);
    else groups.push({ key: `${ball.inning}-${over}-${groups.length}`, inning: ball.inning, over, balls: [ball] });
  }
  return groups;
}

type BallKind = "all" | "wickets" | "boundaries" | "commentary";

function BallByBall({ detail, balls, filter, onFilter }: { detail: MatchDetail; balls: Ball[]; filter: number; onFilter: (value: number) => void }) {
  const [kind, setKind] = useState<BallKind>("all");
  const innings = Array.from(new Set(detail.balls.map((ball) => ball.inning)));
  const teamFor = (number: number) => teamAbbr(detail, detail.balls.find((ball) => ball.inning === number && ball.team)?.team ?? detail.innings.find((inning) => inning.number === number)?.team);
  const shown = kind === "all" ? balls : balls.filter((ball) => kind === "wickets" ? ball.wicket : kind === "boundaries" ? ball.boundary : Boolean(ball.commentary));
  const overs = groupOvers(shown);
  const counts = { wickets: balls.filter((ball) => ball.wicket).length, boundaries: balls.filter((ball) => ball.boundary).length, commentary: balls.filter((ball) => ball.commentary).length };
  return <article className="detail-card balls-card">
    <div className="card-title"><div><p className="section-kicker">DELIVERY TIMELINE</p><h3>Ball by ball</h3></div><span className={`coverage ${detail.ball_coverage.level}`}>{detail.ball_coverage.label}</span></div>
    <p className="card-copy">{detail.ball_coverage.note}</p>
    <div className="innings-filter"><button className={filter === 0 ? "active" : ""} onClick={() => onFilter(0)}>All innings</button>{innings.map((number) => <button className={filter === number ? "active" : ""} key={number} onClick={() => onFilter(number)}>{teamFor(number) ?? `Innings ${number}`}</button>)}<span className="filter-gap" />
      <button className={kind === "all" ? "active" : ""} onClick={() => setKind("all")}>{balls.length} balls</button>
      <button className={kind === "wickets" ? "active" : ""} onClick={() => setKind("wickets")}>{counts.wickets} wickets</button>
      <button className={kind === "boundaries" ? "active" : ""} onClick={() => setKind("boundaries")}>{counts.boundaries} boundaries</button>
      {counts.commentary > 0 && <button className={kind === "commentary" ? "active" : ""} onClick={() => setKind("commentary")}>{counts.commentary} commentary</button>}
    </div>
    <div className="ball-list">{overs.length ? overs.map((group) => {
      const runs = group.balls.reduce((sum, ball) => sum + ball.runs, 0);
      const end = group.balls.at(-1)?.score;
      return <section className="over-group" key={group.key}><header><strong>{filter === 0 && innings.length > 1 ? `${teamFor(group.inning) ?? `Inns ${group.inning}`} · ` : ""}Over {group.over + 1}</strong>{kind === "all" && <span>{plural(runs, "run")}{end ? ` · ${end}` : ""}</span>}</header>
        {group.balls.map((ball) => <div className={`ball-row${ball.wicket ? " wicket" : ball.boundary ? " boundary" : /wd|nb/.test(ball.event) ? " extra" : ""}`} key={ball.id}><span>{ball.label}</span><b>{ball.event}</b><div><strong>{ball.title || `Delivery ${ball.label}`}</strong><p>{ball.text ? <RichText text={ball.text} /> : "No description."}</p>{ball.commentary && <q><RichText text={ball.commentary} /></q>}</div>{ball.score && <em>{ball.score}</em>}</div>)}
      </section>;
    }) : <div className="empty-state"><strong>{detail.balls.length ? "No deliveries match this filter" : "No delivery records for this match"}</strong><p>{detail.balls.length ? "Switch back to all balls or another innings." : "The scorecard is still available."}</p></div>}</div>
  </article>;
}

function Momentum({ detail, onAsk }: { detail: MatchDetail; onAsk: (text: string) => void }) {
  const shifts = detail.analytics.shifts ?? [];
  const par = usePar(detail.match.season);
  const sides = detail.innings.slice(0, 2).map((inning) => ({ inning: inning.number, team: inning.team, runs: inning.runs,
    wickets: inning.wickets, color: teamColor(detail, inning.team) }));
  return <div className="momentum-layout">
    <article className="detail-card"><div className="card-title"><div><p className="section-kicker">MOMENTUM SHIFTS</p><h3>Where the match moved</h3></div><button className="ask-button" onClick={() => onAsk("Where did this match turn, and why?")}>Ask Pandit to explain ↗</button></div>
      <p className="card-copy">Detected from every delivery: wicket clusters, scoring bursts, squeezes, chase-equation swings, and dropped catches.</p>
      {shifts.length ? <ol className="shift-list">{shifts.map((shift, index) => <li key={`${shift.inning}-${shift.from}-${index}`} className={shift.decisive ? "decisive" : ""} style={{ "--i": index } as CSSProperties}>
        <span className="shift-type" style={{ color: teamColor(detail, shift.innings_team) }}>{SHIFT_LABELS[shift.type]}{shift.decisive ? " · turning point" : ""}</span>
        <div><strong>{shift.innings_team} innings, overs {shift.from}–{shift.to}</strong><p>{shift.headline}. {shift.score_before} → {shift.score_after}.</p>{shift.evidence?.filter((item) => item.text).slice(0, 2).map((item) => <q key={item.label}>{item.label}: {item.text}</q>)}</div>
      </li>)}</ol> : <p className="card-copy">Momentum shifts need delivery-level data, which this match does not have.</p>}
    </article>
    <article className="detail-card"><div className="card-title"><div><p className="section-kicker">OVER BY OVER</p><h3>Runs per over against par</h3></div></div>
      <OverChart sides={sides} balls={detail.balls} par={par} season={detail.match.season} />
    </article>
    <article className="detail-card"><div className="card-title"><div><p className="section-kicker">THE WORM</p><h3>Runs as the innings went</h3></div></div>
      <Worm sides={sides} balls={detail.balls} par={par} />
    </article>
    <article className="detail-card"><div className="card-title"><div><p className="section-kicker">PHASE BY PHASE</p><h3>Powerplay, middle and death</h3></div></div>
      <PhaseChart sides={sides} balls={detail.balls} />
    </article>
  </div>;
}

// ---------------------------------------------------------------- drawers

function DocsDrawer({ open, docs, onClose }: { open: boolean; docs: DocsCatalog | null; onClose: () => void }) {
  return <aside className={`docs-drawer${open ? " open" : ""}`} aria-label="cricket-rag documentation"><header><div><p className="section-kicker">LIVING DOCUMENTATION</p><strong>{docs?.title ?? "cricket-rag API"}</strong><small>API patterns, ownership, state, and provenance</small></div><button onClick={onClose} aria-label="Close documentation">×</button></header><div className="docs-scroll">
    <section><h3>Architecture rules</h3>{(docs?.principles ?? ["Loading the documentation catalog…"]).map((item) => <p className="doc-principle" key={item}>{item}</p>)}</section>
    <section><h3>Data sources</h3>{docs?.sources.map((source) => <article className="doc-source" key={source.name}><strong>{source.name}</strong><p>{source.use}</p><small>{source.mode}</small></article>)}</section>
    <section><h3>API contracts</h3>{docs?.apis.map((api) => <article className="api-contract" key={api.path}><div><b>{api.method}</b><code>{api.path}</code></div><p>{api.use}</p>{api.request && <small>Request · {api.request}</small>}<small>Response · {api.response}</small></article>)}</section>
    <section><h3>Tools and node ownership</h3>{docs?.tools.map((tool) => <article className="ownership" key={tool.name}><code>{tool.name}</code><span>owned by <b>{tool.owner}</b></span><p>{tool.use}</p></article>)}</section>
    <section><h3>Pipeline state</h3>{docs?.states.map((state) => <article className="state-contract" key={state.name}><code>{state.name}</code><p>{state.meaning}</p></article>)}</section>
    <section><h3>State transitions</h3><div className="transition-list">{docs?.transitions.map((edge, index) => <div key={`${edge.from}-${edge.to}-${index}`}><code>{edge.from}</code><span>→</span><code>{edge.to}</code>{edge.condition && <small>{edge.condition}</small>}</div>)}</div></section>
    <section><h3>Ball-data ladder</h3>{docs?.coverage.map((item) => <article className="coverage-contract" key={item.level}><span className={`coverage ${item.level}`}>{item.level}</span><p>{item.meaning}</p></article>)}</section>
  </div></aside>;
}

function TraceDrawer({ open, graph, trace, loading, onClose }: { open: boolean; graph: AgentGraph; trace: TraceEvent[]; loading: boolean; onClose: () => void }) {
  const current = trace.at(-1);
  const visited = new Set(trace.map((event) => event.node));
  return <aside className={`trace-panel${open ? " open" : ""}`} aria-label="Live pipeline trace"><header className="trace-header"><div><p className="section-kicker">LIVE PIPELINE</p><strong>Agent pipeline</strong><small>{graph.nodes.length} nodes · v{graph.version}</small></div><button onClick={onClose} aria-label="Close agent trace">×</button></header><div className="trace-scroll">
    <section className="graph-card"><div className="graph-card-head"><span>Programs and checks</span><em>{loading ? "RUNNING" : "READY"}</em></div><div className="graph-flow">{graph.nodes.map((node, index) => <div key={node.id}><div className={`graph-node${current?.node === node.id && loading ? " active" : visited.has(node.id) ? " complete" : ""}`}><span>{visited.has(node.id) ? "✓" : index + 1}</span><div><strong>{node.label}</strong><small>{node.description}</small></div></div>{index < graph.nodes.length - 1 && <i className="flow-arrow">↓</i>}</div>)}</div></section>
    <section className="state-card"><div className="graph-card-head"><span>Current state</span><em>{current ? `#${current.sequence}` : "IDLE"}</em></div>{current ? <div className="state-grid"><div><span>Phase</span><strong>{current.label}</strong></div><div><span>Route</span><strong>{current.snapshot.route}</strong></div><div><span>Results</span><strong>{current.snapshot.result_count}</strong></div><div><span>Actions</span><strong>{current.snapshot.action_count}</strong></div><div><span>LLM used</span><strong>{current.snapshot.model_used ? "yes" : "no"}</strong></div><div><span>Validation</span><strong>{current.snapshot.validation}</strong></div></div> : <p className="trace-empty">Send Pandit a message to watch every step.</p>}</section>
    <section className="timeline-card"><div className="graph-card-head"><span>State transitions</span><em>{trace.length} EVENTS</em></div><div className="trace-timeline">{trace.length ? trace.map((event) => <details className="trace-event" key={`${event.sequence}-${event.node}`} open={event.sequence === current?.sequence}><summary><span>{String(event.sequence).padStart(2, "0")}</span><div><strong>{event.label}</strong><small>{event.kind}</small></div><i>⌄</i></summary><p>{event.summary}</p><pre>{JSON.stringify(event.patch, null, 2)}</pre></details>) : <p className="trace-empty">No steps yet. The pipeline is loaded and ready.</p>}</div></section>
  </div></aside>;
}
