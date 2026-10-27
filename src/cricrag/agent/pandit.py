"""Pandit, rebuilt around programs instead of a tool loop.

    frame (code) -> route (code) -> ONE program -> evidence card -> ONE LLM call writes -> validate
      clarify   shared name with no dominant player
      refuse    out of scope (cricbot's scope rules)
      story     a match's facts + momentum shifts + key events        "where did the 2025 final turn"
      event     SQL on the events table                               "how was Kohli out in the 2016 final"
      stats     cricbot's verified queries, in-process                 "best death bowler for RCB in 2025"
      search    BM25 over commentary with the frame's filters         "was there a DRS controversy in that game"

The LLM never picks entities or numbers: it writes prose from the card. Every card line has a tag
([E#] events, [S#] stats, [M#] match facts, [C#] commentary) and the validator checks the reply against them.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cricrag import events as ev
from cricrag.agent import intent as itn
from cricrag.agent.llm import GroqClient, LLMUnavailable
from cricrag.agent.validate import Sources, check, normalise
from cricrag.entities import Catalog, link_players
from cricrag.frame import build_frame, find_matches
from cricrag.retrieval.hybrid import Filters, Retriever

STORY = re.compile(
    r"\b(turn(ed|ing)?|recap|summar\w*|story|key moments?|momentum|how did .+ (win|lose|lost)|"
    r"what happened in|walk me through)\b",
    re.I,
)
MOMENT = re.compile(r"\b(how|who|when|which|describe|what happened|show|list|tell me about|where)\b", re.I)
GAP = "I don't have a verified query for that in the IPL archive, so I can't answer it reliably."
COUNT = re.compile(
    r"\b(most|how many|number of|count|tally|total|leading|top \d+|career|in all|best|average|"
    r"economy|strike[- ]?rate|run[- ]?rate|record)\b",
    re.I,
)
UNSUPPORTED = re.compile(
    r"\b(captain(s|cy|ed)?|skipper|umpires?|auctions?|salar(y|ies)|price|bids?|retained|retention|coach(es)?|"
    r"attendance|viewership|injur\w*|prize money|brand value|speed guns?|km/?h|kph)\b",
    re.I,
)
OTHER_COMP = re.compile(
    r"\b(duleep|ranji|vijay hazare|syed mushtaq|deodhar|irani|big bash|bbl|psl|cpl|sa20|ilt20|the hundred|"
    r"county|ashes|test (match|series|cricket)|odis?|t20is?|world cup|champions trophy|asia cup|wpl|"
    r"women'?s premier league)\b",
    re.I,
)
RESULT = re.compile(
    r"\b(by how many|margin|won|win|winners?|beat|lost|result|scorecard|top[- ]scor\w*|man of the match|"
    r"player of the match)\b",
    re.I,
)
ORDINAL = re.compile(r"\b(first|opening|inaugural)\s+(ever\s+)?(ipl\s+)?(match|game)\b", re.I)
ABSTAIN = re.compile(
    r"\b(does not|doesn't|do not|cannot|can't|unable to)\s+(answer|contain|state|include|provide|show|list|"
    r"mention|specify|give)|not (in|provided in|available in) the evidence|"
    r"\b(is|are) (missing|not given|not provided|not listed)\b|\bcannot be (completed|determined|answered)\b|"
    r"\bno [\w ]{1,40} (is|are) (given|provided|listed|shown)\b",
    re.I,
)
SEASON_SCOPE = re.compile(r"\b(single[- ]seasons?|in a season|per season|best seasons?)\b", re.I)
ASKS_SEASON = re.compile(r"\b(seasons?|year|edition|campaign|(19|20)\d\d)\b", re.I)
LOW = re.compile(r"\b(lowest|least|fewest|worst|minimum|smallest)\b", re.I)
HIGH = re.compile(r"\b(highest|most|biggest|largest)\b", re.I)


WHICH_SEASON = re.compile(r"\bwhich (ipl )?(season|year|edition)\b", re.I)
RATE = re.compile(r"\b(run[- ]?rate|scoring rate|economy|boundar\w*|dot[- ]?ball\w*|strike[- ]?rate)\b", re.I)


def mismatch(question: str, summary: str, players_named: bool = False) -> str | None:
    """Named arbitration: cricbot answered a nearby question, not this one."""
    if WHICH_SEASON.search(question) and players_named:
        return "subject_season_got_players"
    if SEASON_SCOPE.search(summary) and not ASKS_SEASON.search(question):
        return "scope_single_season"
    if LOW.search(question) and HIGH.search(summary) and not LOW.search(summary):
        return "polarity_high_for_low"
    return None


EVENT_OF = {"partnership": "partnership", "hat_tricks": "hattrick", "hundreds": "hundred", "fifties": "fifty",
            "wickets": "wicket", "dismissals": "wicket", "drops": "drop", "maidens": "maiden",
            "five_wicket_hauls": "haul", "four_wicket_hauls": "haul", "fastest": "fifty"}  # fmt: skip


def FILTERED(frame) -> bool:
    return bool(frame.players or frame.teams or frame.seasons or frame.stage)


WRITER = """You are Pandit, an IPL analyst. Answer the question using only the EVIDENCE.
- Lead with the direct answer. Name the players involved. At most 5 sentences, plain prose.
- End every sentence that states a fact with the evidence tag(s) exactly as written, e.g. [E1] or [S1][C2].
- Use only the numbers shown; never add up, average or derive new ones.
- For "where did it turn" questions, lead with the line marked DECISIVE and who caused it.
- Numbers: only from E, S or M lines, or C headers. Never from commentary BODY text.
- A VERIFIED ANSWER line is correct: restate its names and numbers first, then add detail only if asked.
- If the evidence does not answer the question, say exactly what is missing."""


class Trace(list):
    """The answer's trace. `on_step` sees each step as it is appended, so a server can stream it live."""

    def __init__(self, steps: list[dict[str, Any]], on_step: Callable[[dict[str, Any]], None] | None = None):
        super().__init__(steps)
        self.on_step = on_step
        if on_step:
            for step in steps:
                on_step(step)

    def append(self, step: dict[str, Any]) -> None:
        super().append(step)
        if self.on_step:
            self.on_step(step)


@dataclass
class Answer:
    text: str
    program: str
    frame: dict[str, Any]
    sources: Sources
    mode: str = "llm"
    problems: list[str] = field(default_factory=list)
    model: str | None = None
    seconds: float = 0.0
    trace: list[dict[str, Any]] = field(default_factory=list)


class Pandit:
    def __init__(
        self,
        retriever: Retriever,
        catalog: Catalog,
        llm: GroqClient,
        events_db=None,
        cricbot_root: str | None = None,
        planner: str = "hybrid",
        intent_llm=None,
        router: str = "rules",
    ):
        self.r, self.cat, self.llm, self.edb = retriever, catalog, llm, events_db
        self.root, self.planner = cricbot_root, planner
        self.intent_llm, self.router = intent_llm or llm, router
        self.find_con = None  # opened on first use

    # ------------------------------------------------------------------ routing
    def ask(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> Answer:
        t0 = time.time()
        frame = build_frame(question, self.cat, history)
        src, trace = Sources(), Trace([{"step": "frame", **frame.as_dict()}], on_step)
        if frame.ambiguous and not frame.players:
            token, cands = next(iter(frame.ambiguous.items()))
            return self._done(
                f"Which {token.title()} do you mean: {', '.join(cands[:-1])} or {cands[-1]}?",
                "clarify",
                frame,
                src,
                trace,
                t0,
                mode="clarify",
            )
        if (m := OTHER_COMP.search(question)) and not re.search(r"\bipl\b", question, re.I):
            trace.append({"step": "route", "rule": "other_competition", "term": m.group(0)})
            return self._done(
                f"I only cover the IPL, so I can't answer questions about {m.group(0)}.",
                "refuse",
                frame,
                src,
                trace,
                t0,
                mode="refuse",
            )
        if m := UNSUPPORTED.search(question):
            trace.append({"step": "route", "rule": "unsupported_concept", "term": m.group(0)})
            return self._done(
                f"The archive has ball-by-ball and scorecard data but no {m.group(0).lower()} records, "
                "so I can't answer that reliably.",
                "gap",
                frame,
                src,
                trace,
                t0,
                mode="gap",
            )
        matches = find_matches(frame) if (frame.seasons or frame.teams or frame.stage) else []
        one = matches[0]["match_id"] if frame.stage == "final" and len(matches) == 1 else None
        if (
            one and RESULT.search(question) and not STORY.search(question) and not ev.event_types(question)
        ) or (ORDINAL.search(question) and (one := self._first_match(frame))):
            mid = one
            trace.append({"step": "route", "rule": "single_match_facts", "match": mid})
            from cricrag.archive import connect
            from cricrag.story import facts

            with connect() as con:
                sheet = facts(con, mid)
            src.stats["M1"] = {"question": f"match {mid}", "reply": " ".join(sheet)}
            return self._write(question, "match", [f"{' '.join(sheet)} [M1]"], frame, src, trace, t0)
        if self.router == "intent" and self.intent_llm.available:
            ans = self._ask_intent(question, history, frame, matches, trace, t0)
            if ans is not None:
                return ans
        types = ev.event_types(question)
        trace.append({"step": "route", "matches": [m["match_id"] for m in matches], "event_types": types})
        lines: list[str] = []
        if STORY.search(question) and len(matches) == 1 and self.edb is not None:
            program, lines = "story", self._story(matches[0]["match_id"], src)
        elif (
            types
            and self.edb is not None
            and (MOMENT.search(question) or ev.DESC.search(question) or ev.ASC.search(question))
            and not COUNT.search(question)
            and (frame.players or frame.seasons or frame.teams)
        ):
            program, lines = "event", self._event(frame, [t for t in types if t != "shift"] or types, src)
        else:
            program = "stats"
        if program == "stats" or not lines:
            if (
                self.edb is not None
                and COUNT.search(question)
                and (fc := ev.fielding_counts(self.edb, frame))
                and fc[1]
            ):
                kind, rows = fc
                src.stats["S1"] = {
                    "question": question,
                    "reply": f"{kind.title()} (archive; scorecard fielders, audited drops): "
                    + ", ".join(f"{n} {c}" for n, c in rows),
                }
                return self._write(
                    question, "fielding", [f"{src.stats['S1']['reply']} [S1]"], frame, src, trace, t0
                )
            asked = question
            for typo, fixed in frame.corrected.items():  # the frame already corrected it ("kholi" -> "kohli")
                asked = re.sub(rf"\b{re.escape(typo)}\b", fixed, asked, flags=re.I)
            st = self._stats(asked, history, src) if self.root else {"kind": "none"}
            why = (
                mismatch(question, st["summary"], bool(link_players(st["summary"], self.cat).players))
                if st["kind"] == "stats"
                else None
            )
            prior = None
            trace.append({"step": "stats", "kind": st["kind"], "calls": st.get("calls"), "mismatch": why})
            if st["kind"] in ("refuse", "clarify"):
                return self._done(st["reply"], st["kind"], frame, src, trace, t0, mode=st["kind"])
            if st["kind"] == "stats" and not why:
                lines = [f"VERIFIED ANSWER: {st['summary']} [S1]", f"{st['facts']} [S1]"]
                ans = self._write(question, "stats", lines, frame, src, trace, t0)
                abstained = ans.mode == "llm" and ABSTAIN.search(ans.text)
                unverified = ans.mode == "extractive" and ans.problems  # writer failed the checks twice
                if not (abstained or unverified):
                    return ans
                prior = ans  # kept if every fallback also fails
                trace.append(
                    {
                        "step": "fallback",
                        "rule": "writer_abstained_on_stats" if abstained else "writer_failed_checks",
                    }
                )
            elif (
                frame.teams
                and (frame.phase or RATE.search(question))
                and (lines := self._team_phase(frame, src))
            ):
                return self._write(question, "team_phase", lines, frame, src, trace, t0)
            src = Sources()
            if (
                self.edb is not None
                and COUNT.search(question)
                and (lb := ev.leaderboard(self.edb, frame))
                and lb[1]
            ):
                label, rows = lb
                src.stats["S1"] = {
                    "question": question,
                    "reply": f"{label.capitalize()} (archive events, count per player): "
                    + ", ".join(f"{n} {c}" for n, c in rows),
                }
                trace.append({"step": "leaderboard", "label": label})
                return self._write(
                    question, "leaderboard", [f"{src.stats['S1']['reply']} [S1]"], frame, src, trace, t0
                )
            # a named event (partnership, wicket...) is a lookup, not a search
            if (
                types
                and self.edb is not None
                and (lines := self._event(frame, [t for t in types if t != "shift"] or types, src))
            ):
                trace.append({"step": "fallback", "rule": "event_after_stats"})
                return self._write(question, "event", lines, frame, src, trace, t0)
            moment = bool(MOMENT.search(question) or types)
            if not COUNT.search(question) and st["kind"] != "gap" and FILTERED(frame) and moment:
                program, lines = "search", self._search(frame, src)
            else:
                return self._find(question, frame, src, trace, t0, st.get("reply"), prior)
        trace.append({"step": program, "evidence_lines": len(lines), "chunks": len(src.chunks)})
        return self._write(question, program, lines, frame, src, trace, t0)

    # ------------------------------------------------------------------ intent router
    def _ask_intent(self, question, history, frame, matches, trace, t0) -> Answer | None:
        """Form -> ordered programs -> first result whose shape fits. None = intent unavailable (use rules)."""
        prev = next((t["content"] for t in reversed(history or []) if t.get("role") == "user"), None)
        try:
            it = itn.classify(self.intent_llm, question, prev)
        except LLMUnavailable as exc:
            trace.append({"step": "intent_unavailable", "error": str(exc)[:120]})
            return None
        one = matches[0]["match_id"] if len(matches) == 1 else None
        order = itn.routes(it, bool(one), frame)
        trace.append({"step": "intent", **it.as_dict(), "routes": order})
        prior, gap_reply = None, None
        for prog in order:
            src = Sources()
            if prog == "finder":
                return self._find(question, frame, src, trace, t0, gap_reply, prior, hint=it.as_dict())
            got = self._program(prog, question, history, frame, it, one, src, trace)
            if isinstance(got, tuple):  # cricbot refused or asked to clarify, or a gap reply to keep
                kind, text = got
                if kind in ("refuse", "clarify"):
                    return self._done(text, kind, frame, src, trace, t0, mode=kind)
                gap_reply = gap_reply or text
                continue
            if not got:
                continue
            ans = self._write(question, prog, got, frame, src, trace, t0)
            abstained = ans.mode == "llm" and ABSTAIN.search(ans.text)
            if not (abstained or (ans.mode == "extractive" and ans.problems)):
                return ans
            prior = prior or ans
            trace.append(
                {"step": "fallback", "rule": "writer_abstained" if abstained else "writer_failed_checks"}
            )
        return prior or self._done(gap_reply or GAP, "gap", frame, Sources(), trace, t0, mode="gap")

    def _program(self, prog, question, history, frame, it, one, src, trace):
        """Run one program: evidence lines, None (nothing / shape doesn't fit), or (kind, text)."""
        note = {"step": "try", "program": prog}
        trace.append(note)
        if prog == "refuse":
            return ("refuse", "I answer from IPL records only, so I can't predict future results.")
        if prog in ("story", "match") and not one:
            return None
        if prog == "story":
            return self._story(one, src) if self.edb is not None else None
        if prog == "match":
            from cricrag.archive import connect
            from cricrag.story import facts

            with connect() as con:
                sheet = facts(con, one)
            src.stats["M1"] = {"question": f"match {one}", "reply": " ".join(sheet)}
            return [f"{' '.join(sheet)} [M1]"] if sheet else None
        if prog == "event":
            types = [t for t in ev.event_types(question) if t != "shift"] or (
                [EVENT_OF[it.measure]] if it.measure in EVENT_OF else []
            )
            return self._event(frame, types, src) if types and self.edb is not None else None
        if prog in ("leaderboard", "fielding"):
            got = (
                (ev.leaderboard if prog == "leaderboard" else ev.fielding_counts)(self.edb, frame)
                if self.edb
                else None
            )
            if not got or not got[1]:
                return None
            label, rows = got
            src.stats["S1"] = {"question": question, "reply": f"{label.capitalize()} (archive, count per player): "
                               + ", ".join(f"{n} {c}" for n, c in rows)}  # fmt: skip
            return [f"{src.stats['S1']['reply']} [S1]"]
        if prog == "team_phase":
            return self._team_phase(frame, src) if frame.teams else None
        if prog == "search":
            return self._search(frame, src) if FILTERED(frame) else None
        # stats: cricbot's verified queries, then the shape check
        asked = question
        for typo, fixed in frame.corrected.items():
            asked = re.sub(rf"\b{re.escape(typo)}\b", fixed, asked, flags=re.I)
        st = self._stats(asked, history, src) if self.root else {"kind": "none"}
        note["kind"] = st["kind"]
        if st["kind"] in ("refuse", "clarify", "gap"):
            return (st["kind"], st.get("reply") or GAP) if st["kind"] != "gap" or st.get("reply") else None
        if st["kind"] != "stats":
            return None
        why = itn.shape_fits(it, st.get("tables", []), frame, st["facts"]) or mismatch(
            question, st["summary"], bool(link_players(st["summary"], self.cat).players)
        )
        note.update(calls=st.get("calls"), mismatch=why)
        return None if why else [f"VERIFIED ANSWER: {st['summary']} [S1]", f"{st['facts']} [S1]"]

    # ------------------------------------------------------------------ programs
    def _first_match(self, frame) -> int | None:
        from cricrag.archive import connect

        where = "NOT coalesce(is_cancelled, false)"
        params = list(frame.seasons)
        if params:
            where += f" AND season IN ({','.join('?' * len(params))})"
        with connect() as con:
            row = con.execute(
                f"SELECT match_id FROM matches WHERE {where} ORDER BY start_date LIMIT 1", params
            ).fetchall()
        return row[0][0] if row else None

    def _find(self, question, frame, src, trace, t0, gap_reply=None, prior=None, hint=None) -> Answer:
        """Finder: generated read-only SQL, guarded (see cricrag.finder). Only reached instead of GAP."""
        from cricrag import finder

        if self.find_con is None and self.edb is not None and self.llm.available:
            try:
                self.find_con = finder.connect()
            except Exception as exc:
                trace.append({"step": "finder_unavailable", "error": str(exc)[:160]})
        if self.find_con is None:
            return prior or self._done(gap_reply or GAP, "gap", frame, src, trace, t0, mode="gap")
        try:
            res = finder.find(self.llm, self.find_con, frame, hint)
        except LLMUnavailable:
            res = {"kind": "gap", "reason": "llm unavailable"}
        trace.append({"step": "finder", **{k: v for k, v in res.items() if k != "rows"}})
        if res["kind"] != "rows":
            why = res.get("reason", "")
            text = gap_reply or (
                f"I can't answer that from the IPL archive: {why.rstrip('.')}."
                if why and not why.startswith(("DuckDB", "the query", "reply with", "llm"))
                else GAP
            )
            return prior or self._done(text, "gap", frame, src, trace, t0, mode="gap")
        src.stats["S1"] = {"question": question, "reply": finder.evidence(res)}
        ans = self._write(question, "finder", [f"{src.stats['S1']['reply']} [S1]"], frame, src, trace, t0)
        ans.text += "\n\n(Computed by a generated query over the archive; the SQL is in the trace.)"
        return ans

    def _event(self, frame, types: list[str], src: Sources) -> list[str]:
        rows, total = ev.query(self.edb, frame, types)
        lines = [
            f"{total} matching event(s) in the archive; showing {len(rows)}." if total > len(rows) else ""
        ]
        for i, row in enumerate(rows, 1):
            tag = f"E{i}"
            src.stats[tag] = {"question": "event", "reply": ev.describe_row(row)}
            lines.append(f"{src.stats[tag]['reply']} [{tag}]")
        lines += self._colour([r["chunk_id"] for r in rows[:4]], [r["label"] for r in rows[:4]], src)
        return [x for x in lines if x] if rows else []

    def _story(self, mid: int, src: Sources) -> list[str]:
        from cricrag.archive import connect
        from cricrag.story import facts

        with connect() as con:
            sheet = facts(con, mid)
        src.stats["M1"] = {"question": f"match {mid}", "reply": " ".join(sheet)}
        lines = [f"{' '.join(sheet)} [M1]"]
        q = "SELECT * FROM events WHERE match_id = ? AND type IN ('shift','hattrick','haul','hundred','drop','finish') ORDER BY inning, over"
        cur = self.edb.execute(q, [mid])
        rows = [dict(zip([d[0] for d in cur.description], r, strict=True)) for r in cur.fetchall()]
        rows.sort(key=lambda r: not (r["type"] == "shift" and r["close"]))  # the decisive shift first
        for i, row in enumerate(rows[:8], 1):
            src.stats[f"E{i}"] = {"question": "event", "reply": ev.describe_row(row, short=True)}
            lines.append(
                f"{'DECISIVE ' if row['type'] == 'shift' and row['close'] else ''}{src.stats[f'E{i}']['reply']} [E{i}]"
            )
        key = [r for r in rows if r["type"] == "shift"][:3]
        return lines + self._colour([r["chunk_id"] for r in key], [r["label"] for r in key], src)

    def _stats(self, question: str, history, src: Sources) -> dict[str, Any]:
        from cricrag import cricbot

        st = cricbot.ask(str(self.root), question, history, self.planner)
        if st["kind"] == "stats":
            src.stats["S1"] = {"question": question, "reply": f"{st['summary']}\n{st['facts']}"}
            src.tables = st.get("blocks", [])
        return st

    def _team_phase(self, frame, src: Sources) -> list[str]:
        """Team scoring in a phase (or whole innings) vs the league, from SQL: run rate, wickets, boundary %, dots."""
        from cricrag.archive import connect

        side = "bowl" if ev.team_role(frame.question) == "bowling" else "bat"
        flt = [("phase = ?", [frame.phase])] if frame.phase else []
        if frame.seasons:
            flt.append((f"season IN ({','.join('?' * len(frame.seasons))})", list(frame.seasons)))
        metric = """round(6.0 * sum(runs_total) / nullif(sum(is_legal::INT), 0), 2), sum(is_wicket::INT),
            round(100.0 * sum((is_four OR is_six)::INT) / nullif(sum(is_legal::INT), 0), 1),
            round(100.0 * sum((is_legal AND runs_total = 0)::INT) / nullif(sum(is_legal::INT), 0), 1), count(DISTINCT match_id)"""

        def run(con, extra: list) -> tuple:
            where = " AND ".join([w for w, _ in extra + flt] or ["true"])
            return con.execute(
                f"SELECT {metric} FROM b WHERE {where}", [v for _, p in extra + flt for v in p]
            ).fetchall()[0]

        with connect() as con:
            con.execute("CREATE OR REPLACE TEMP VIEW b AS" + ev.BASE.split(" AS", 1)[1].replace("cb.", ""))
            team, league = run(con, [(f"{side} = ?", [frame.teams[0]])]), run(con, [])
        if not team[4]:
            return []
        what = f"{frame.teams[0]} {'batting' if side == 'bat' else 'bowling'}, {frame.phase or 'whole innings'}, {', '.join(frame.seasons) or 'all seasons'}"
        src.stats["S1"] = {
            "question": frame.question,
            "reply": (
                f"{what}: run rate {team[0]}, wickets {team[1]}, boundary % {team[2]}, dot % {team[3]} in {team[4]} matches. "
                f"League, same filter: run rate {league[0]}, boundary % {league[2]}, dot % {league[3]}."
            ),
        }
        return [f"{src.stats['S1']['reply']} [S1]"]

    def _search(self, frame, src: Sources) -> list[str]:
        f = Filters.from_frame(frame)
        f.role = ev.team_role(frame.question)
        res = self.r.search(frame.question, f, k=6, mode="bm25")
        if len(res.hits) < 3:  # nothing lexical to hold on to: try meaning
            res = self.r.search(frame.question, f, k=6, mode="hybrid")
        out = []
        for h in res.hits:
            tag = self._tag(h.row, src)
            out.append(f"HEADER: {h.header} BODY: {h.body[:400]} [{tag}]")
        return out

    def _colour(self, ids: list[str], labels: list[str], src: Sources) -> list[str]:
        """Commentary for chosen overs, fetched by id: only the ball that matters, not the whole over."""
        rows = {r["id"]: r for r in self.r.by_ids(list(dict.fromkeys(ids)))}
        out = []
        for cid, label in zip(ids, labels, strict=True):
            if row := rows.get(cid):
                tag = self._tag(row, src)
                ball = next((ln for ln in row["body"].split("\n") if ln.startswith(f"{label} ")), row["body"])
                out.append(f"Commentary {tag}: {ball[:220]}")
        return out

    @staticmethod
    def _tag(row: dict[str, Any], src: Sources) -> str:
        for t, r in src.chunks.items():
            if r["id"] == row["id"]:
                return t
        tag = f"C{len(src.chunks) + 1}"
        src.chunks[tag] = row
        return tag

    # ------------------------------------------------------------------ writing
    def _write(self, question, program, lines, frame, src, trace, t0) -> Answer:
        if not lines:
            return self._done(
                "I couldn't find that in the IPL archive.", program, frame, src, trace, t0, mode="empty"
            )
        extractive = "\n".join(re.sub(r"^(HEADER: |Commentary )", "", ln) for ln in lines[:8])
        if not self.llm.available:
            return self._done(extractive, program, frame, src, trace, t0, mode="extractive")
        msgs = [
            {"role": "system", "content": WRITER},
            {"role": "user", "content": f"QUESTION: {question}\nEVIDENCE:\n" + "\n".join(lines)},
        ]
        try:
            out = self.llm.chat(msgs, max_tokens=500)
            text = normalise((out["message"].get("content") or "").strip())
            problems = check(text, question, src, self.cat, used_tools=True)
            if problems:
                msgs += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "Fix: " + "; ".join(problems)},
                ]
                text = normalise(
                    (self.llm.chat(msgs, max_tokens=500)["message"].get("content") or "").strip()
                )
                problems = check(text, question, src, self.cat, used_tools=True)
            trace.append({"step": "validate", "problems": problems})
            if problems:
                self.llm.evict(msgs[:2], max_tokens=500)  # don't replay a rejected reply on the next run
                self.llm.evict(msgs, max_tokens=500)
                return self._done(
                    extractive,
                    program,
                    frame,
                    src,
                    trace,
                    t0,
                    mode="extractive",
                    problems=problems,
                    model=out["model"],
                )
            return self._done(text, program, frame, src, trace, t0, model=out["model"])
        except LLMUnavailable as exc:
            trace.append({"step": "llm_unavailable", "error": str(exc)[:160]})
            return self._done(extractive, program, frame, src, trace, t0, mode="extractive")

    @staticmethod
    def _done(text, program, frame, src, trace, t0, mode="llm", problems=None, model=None) -> Answer:
        return Answer(
            text,
            program,
            frame.as_dict(),
            src,
            mode,
            problems or [],
            model,
            round(time.time() - t0, 2),
            trace,
        )


def as_json(a: Answer) -> str:
    return json.dumps(
        {"program": a.program, "mode": a.mode, "text": a.text, "trace": a.trace}, default=str, indent=1
    )
