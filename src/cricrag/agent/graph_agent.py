"""PanditGraph: a LangGraph agent that adds a relational branch beside Pandit's stats and narrative programs.

    frame (code) -> route (rules, each one named) -+-> clarify ------------------------------> END
                                                    +-> relational (templated graph tool) -> write -> END
                                                    +-> pandit (stats: DuckDB / narrative: LanceDB) -> END

- Entities, teams and seasons come from the deterministic QuestionFrame (Rule 4); the route is chosen by
  named regex rules (Rule 9) and recorded in the trace.
- The relational node runs one tool from cricrag.graph.tools over data/graph.duckdb. Every number in its
  evidence is SQL-derived; the writer may only restate them, with [G#] citations, and is validated.
- Everything else is handed to Pandit unchanged, so stats and narrative keep their own validators.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

import duckdb
from langgraph.graph import END, START, StateGraph

from cricrag.agent.llm import LLMUnavailable
from cricrag.agent.pandit import Answer, Trace
from cricrag.agent.validate import Sources
from cricrag.frame import build_frame
from cricrag.graph import tools as gt
from cricrag.graph import venues as gv
from cricrag.text import numbers

# ------------------------------------------------------------------ route rules (named)

TEAMMATE = re.compile(r"\b(team-?mates?|played (together|with|alongside)|connect(ed|ion)s?|link(ed|s)?|degrees? of separation|six degrees)\b", re.I)  # fmt: skip
CATCH = re.compile(r"\b(catch(es|ing)?|caught|c ?& ?b|stump(ed|ings?)|keeper[- ]bowler|fielder[- ]bowler|combination|combo)\b", re.I)  # fmt: skip
DROP = re.compile(r"\b(drop(s|ped|ping)?|put down|spilled|grassed)\b", re.I)
NEMESIS = re.compile(r"\b(nemesis|nemeses|trouble[sd]?|kryptonite|struggle[sd]?|got (him|\w+) out most|dismiss(ed|es)? \w+ (the )?most|most dismissals of|owns?|bogey)\b", re.I)  # fmt: skip
BUNNY = re.compile(r"\b(bunny|bunnies|victims?|favou?rite (victim|wicket)|whom did|who did \w+ dismiss|dismissed most by)\b", re.I)  # fmt: skip
H2H = re.compile(r"\b(vs\.?|versus|against|head[- ]to[- ]head|h2h|match-?up|record (against|vs)|faced?|face-?off|battle|rivalry)\b", re.I)  # fmt: skip
RANK = re.compile(
    r"\b(page ?rank|network (rank|score)|centrality|most influential|graph rank|quality (wickets|runs))\b",
    re.I,
)
# Pandit already answers these well: batting partnerships, scorecards, and match moments.
PANDIT_FIRST = re.compile(r"\b(partnerships?|stand|put on|added together|scorecard|turn(ed|ing)?|final|how was \w+ out|batting together)\b", re.I)  # fmt: skip
PAIR_WORDS = re.compile(r"\b(pairs?|partners?|combination|combo|c ?& ?b|duo)\b", re.I)
# Grounds: only clear venue cues come here; "average score at Wankhede" stays with Pandit's stats.
VENUE_LIKE = re.compile(r"\b(plays? (like|similar)|similar (to|grounds?|venues?|pitch\w*)|like (the )?\w+ (pitch|ground|wicket|surface)|"
                        r"(ground|venue|pitch) (types?|famil\w*|clusters?)|(kinds?|types?|famil\w*) of (ipl )?(grounds?|venues?|pitch\w*)|"
                        r"(spin|pace|batting|bowling)[- ]friendly (grounds?|venues?|pitch\w*))\b", re.I)  # fmt: skip
VENUE_PAR = re.compile(r"\b(above par|below par|v(s|ersus)? par|against (the )?par|venue[- ]adjusted|best (venues?|grounds?)|"
                       r"worst (venues?|grounds?)|favou?rite (venues?|grounds?)|where does \w+(?: \w+)? (bat|bowl) best|"
                       r"masters? (of|at)|dominat\w* at|best (batters?|bowlers?|players?) at|who (bats|bowls) best at)\b", re.I)  # fmt: skip
BOWL_SIDE = re.compile(r"\b(bowl(s|ing|ers?)?|economy|wickets?)\b", re.I)


def route(question: str, n_players: int, n_teams: int) -> tuple[str, str]:
    """(program, rule). 'pandit' means: not a relational question."""
    q = question
    if PANDIT_FIRST.search(q) and not TEAMMATE.search(q) and not RANK.search(q):
        return "pandit", "pandit_first_cue"
    if n_players >= 2:
        if TEAMMATE.search(q):
            return "connection", "two_players_teammate_cue"
        if DROP.search(q):
            return "drops", "two_players_drop_cue"
        if CATCH.search(q):
            return "catch_pairs", "two_players_catch_cue"
        if H2H.search(q) or NEMESIS.search(q) or BUNNY.search(q) or re.search(r"\bdismiss", q, re.I):
            return "head_to_head", "two_players_matchup_cue"
        return "pandit", "two_players_no_relational_cue"
    if n_players == 1:
        if TEAMMATE.search(q):
            return "teammates", "one_player_teammate_cue"
        if DROP.search(q) and re.search(r"\b(off|bowling|whose)\b", q, re.I):
            return "drops", "one_player_drop_pair_cue"
        if CATCH.search(q) and (PAIR_WORDS.search(q) or re.search(r"\b(off|bowling|whose)\b", q, re.I)):
            return "catch_pairs", "one_player_catch_pair_cue"
        if BUNNY.search(q):
            return "bunnies", "one_player_bunny_cue"
        if NEMESIS.search(q):
            return "nemesis", "one_player_nemesis_cue"
        return "pandit", "one_player_no_relational_cue"
    if RANK.search(q):
        return "ranking", "network_rank_cue"
    if CATCH.search(q) and PAIR_WORDS.search(q):
        return "catch_pairs", "catch_pair_cue"
    if DROP.search(q) and PAIR_WORDS.search(q):
        return "drops", "drop_pair_cue"
    return "pandit", "no_relational_cue"


# ------------------------------------------------------------------ writer + validator

WRITER = """You are Pandit, an IPL analyst. Answer the question using only the EVIDENCE lines.
- Lead with the direct answer. Name the players. At most 4 sentences, plain prose.
- End every sentence that states a fact with its evidence tag(s) exactly as written, e.g. [G1] or [G1][G2].
- Use only the numbers shown, written as digits; never add, subtract, average or derive new ones.
- If a line starts "Tied for the most", name every tied player and say they are tied; never crown one of them.
- For quality wickets or quality runs, say once how they are weighted, in the evidence's words.
- If the evidence does not answer the question, say exactly what is missing."""

GTAG = re.compile(r"\[(G\d+)\]")
NAME = re.compile(r"\b[A-Z][a-zA-Z'-]+(?: (?:de |du |van |ul )?[A-Z][a-zA-Z'-]+)+\b")


WORDS = {w: i for i, w in enumerate(
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"])} | {"once": 1, "twice": 2, "thrice": 3, "thirty": 30, "forty": 40, "fifty": 50}  # fmt: skip
WORD_NUM = re.compile(rf"\b({'|'.join(WORDS)})\b", re.I)
TEAM_FULL = {"Chennai Super Kings", "Mumbai Indians", "Royal Challengers Bengaluru", "Royal Challengers Bangalore",
             "Kolkata Knight Riders", "Sunrisers Hyderabad", "Deccan Chargers", "Punjab Kings", "Kings XI Punjab",
             "Rajasthan Royals", "Delhi Capitals", "Delhi Daredevils", "Gujarat Titans", "Gujarat Lions",
             "Lucknow Super Giants", "Pune Warriors", "Rising Pune Supergiant", "Kochi Tuskers Kerala"}  # fmt: skip


def clean(text: str) -> str:
    """LLMs write 【G1】, [ G1 ], narrow no-break spaces and curly quotes: map them to plain text."""
    text = text.replace("【", "[").replace("】", "]").replace("\u202f", " ").replace("\u00a0", " ")
    text = text.replace("\u2011", "-").replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\[\s*(G\d+)\s*\]", r"[\1]", text).strip()


def validate(reply: str, lines: list[str], question: str) -> list[str]:
    """Citations exist, every number (digits or words like 'seven') is in the evidence, names are in it too."""
    problems = []
    if not reply.strip():
        return ["empty reply"]
    tags = {t for line in lines for t in GTAG.findall(line)}
    allowed_nums = numbers(" ".join(lines)) | numbers(question)
    evidence_text = " ".join(lines) + " " + question
    for sent in re.split(r"(?<=[.!?])\s+", reply.strip()):
        if not sent or not re.search(r"\w", sent):
            continue
        cites = set(GTAG.findall(sent))
        if cites - tags:
            problems.append(f"unknown citation {sorted(cites - tags)}")
        if (numbers(GTAG.sub("", sent)) or WORD_NUM.search(sent)) and not cites:
            problems.append(f"uncited numbers: {sent[:60]}")
    said = numbers(GTAG.sub("", reply)) | {str(WORDS[w.lower()]) for w in WORD_NUM.findall(reply)}
    extra = said - allowed_nums
    if extra:
        problems.append(f"numbers not in evidence: {sorted(extra)}")
    for name in NAME.findall(GTAG.sub("", reply)):
        if name not in evidence_text and name.split()[-1] not in evidence_text and name not in TEAM_FULL:
            problems.append(f"name not in evidence: {name}")
    return problems


def extractive(ev: gt.Evidence) -> str:
    """No LLM, or the writer failed twice: show the evidence itself, cited."""
    if ev.gap:
        return ev.gap
    return " ".join(line if line.rstrip().endswith("]") else line + "." for line in ev.lines[:5])


# ------------------------------------------------------------------ the graph


class State(TypedDict, total=False):
    question: str
    history: list[dict[str, str]]
    frame: Any
    program: str
    rule: str
    venue: str | None
    evidence: Any
    answer: Answer


class PanditGraph:
    """Same `ask()` contract as Pandit, so the server and CLI can use either."""

    def __init__(
        self,
        catalog,
        graph_db: Path,
        llm=None,
        pandit_factory: Callable[[], Any] | None = None,
    ):
        self.cat, self.graph_db, self.llm = catalog, Path(graph_db), llm
        self._pandit, self._factory = None, pandit_factory
        self._con: duckdb.DuckDBPyConnection | None = None
        self._trace: Trace | None = None
        self._t0 = 0.0
        self.app = self._compile()

    # LangGraph wiring ------------------------------------------------
    def _compile(self):
        g = StateGraph(State)
        nodes: dict[str, Any] = {"frame": self._frame, "route": self._route, "clarify": self._clarify,
                                 "relational": self._relational, "write": self._write, "pandit": self._delegate}  # fmt: skip
        for name, fn in nodes.items():
            g.add_node(name, fn)
        g.add_edge(START, "frame")
        g.add_edge("frame", "route")
        g.add_conditional_edges(
            "route",
            lambda s: s["program"] if s["program"] in ("clarify", "pandit") else "relational",
            {"clarify": "clarify", "pandit": "pandit", "relational": "relational"},
        )
        g.add_edge("relational", "write")
        g.add_edge("write", END)
        g.add_edge("clarify", END)
        g.add_edge("pandit", END)
        return g.compile()

    def mermaid(self) -> str:
        return self.app.get_graph().draw_mermaid()

    # public ------------------------------------------------------------
    def ask(self, question: str, history=None, on_step=None) -> Answer:
        self._t0, self._trace = time.time(), Trace([], on_step)
        out = self.app.invoke({"question": question, "history": history or []})
        ans = out["answer"]
        ans.seconds = round(time.time() - self._t0, 2)
        return ans

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            self._con = duckdb.connect(str(self.graph_db), read_only=True)
        return self._con

    @property
    def trace(self) -> Trace:
        assert self._trace is not None
        return self._trace

    # nodes -----------------------------------------------------------
    def _frame(self, s: Any) -> dict[str, Any]:
        frame = build_frame(s["question"], self.cat, s.get("history"))
        return {"frame": frame}

    def _route(self, s: Any) -> dict[str, Any]:
        f = s["frame"]
        venue = self._venue(s["question"])
        if venue and VENUE_LIKE.search(s["question"]) and not f.players:
            program, rule = "venue_like", "venue_like_cue"
        elif (
            VENUE_LIKE.search(s["question"])
            and not f.players
            and re.search(r"\b(grounds|venues|types|famil)", s["question"], re.I)
        ):
            program, rule = "venue_families", "venue_families_cue"
        elif VENUE_PAR.search(s["question"]) and len(f.players) == 1:
            program, rule = "player_venues", "player_venue_par_cue"
        elif venue and VENUE_PAR.search(s["question"]) and not f.players:
            program, rule = "venue_masters", "venue_masters_cue"
        elif f.ambiguous and not f.players:
            program, rule = "pandit", "ambiguous_name_to_pandit"  # Pandit owns the clarify card
        else:
            program, rule = route(s["question"], len(f.players), len(f.teams))
        if program not in ("pandit", "clarify") and not self.graph_db.is_file():
            program, rule = "clarify", "graph_not_built"
        elif program == "head_to_head" and not self._has_matchup(f.players[:2]):
            program, rule = "pandit", "no_balls_between_players"  # two team-mates, not a matchup
        self.trace.append(
            {
                "step": "relational_route",
                "rule": rule,
                "program": program,
                **({"venue": venue} if venue else {}),
            }
        )
        return {"program": program, "rule": rule, "venue": venue}

    def _venue(self, question: str) -> str | None:
        if not self.graph_db.is_file():
            return None
        try:
            return gv.find_venue(self.con, question)
        except Exception:  # an older graph without venue tables
            return None

    def _has_matchup(self, names: list[str]) -> bool:
        ids = [gt.player_id(self.con, n) for n in names]
        if None in ids or len(ids) < 2:
            return True  # let the tool report the gap
        a, b = ids
        q = "SELECT coalesce(sum(balls), 0) FROM bowled_to WHERE (bowler_id = ? AND batter_id = ?) OR (bowler_id = ? AND batter_id = ?)"
        return self._one(q, [a, b, b, a]) > 0

    def _clarify(self, s: Any) -> dict[str, Any]:
        text = "The relational graph isn't built yet. Run `uv run cricrag build-graph`, then ask again."
        return {"answer": self._answer(text, "relational", s, Sources(), mode="gap")}

    def _delegate(self, s: Any) -> dict[str, Any]:
        if self._pandit is None:
            if not self._factory:
                text = "That isn't a relational question, and no stats/narrative agent is configured."
                return {"answer": self._answer(text, "pandit", s, Sources(), mode="gap")}
            self._pandit = self._factory()
        ans = self._pandit.ask(s["question"], s.get("history"), on_step=self.trace.on_step)
        ans.trace = list(self.trace) + list(ans.trace)
        return {"answer": ans}

    def _relational(self, s: Any) -> dict[str, Any]:
        f, program, con = s["frame"], s["program"], self.con
        ids = [pid for name in f.players if (pid := gt.player_id(con, name)) is not None]
        team = f.teams[0] if f.teams else None
        seasons = f.seasons or None
        if len(ids) < len(f.players):
            missing = [n for n in f.players if gt.player_id(con, n) is None]
            ev = gt.Evidence(program, gap=f"{', '.join(missing)} is not in the relational graph.")
        elif program == "head_to_head":
            ev = gt.head_to_head(con, ids[0], ids[1], seasons)
        elif program == "connection":
            ev = gt.connection(con, ids[0], ids[1])
        elif program == "nemesis":
            ev = gt.nemesis(con, ids[0], seasons, team=team)
        elif program == "bunnies":
            ev = gt.bunnies(con, ids[0], seasons, team=team)
        elif program == "teammates":
            ev = gt.teammates(con, ids[0], seasons)
        elif program == "venue_like":
            ev = gt.venue_like(con, s["venue"], seasons)
        elif program == "venue_families":
            ev = gt.venue_families(con, seasons)
        elif program in ("venue_masters", "player_venues"):
            role = "bowl" if BOWL_SIDE.search(s["question"]) else "bat"
            if program == "venue_masters":
                ev = gt.venue_masters(con, s["venue"], role, seasons)
            else:
                ev = gt.player_venues(con, ids[0], role, seasons, venue=s.get("venue"))
        elif program in ("catch_pairs", "drops"):
            fielder, bowler = self._fielding_roles(ids, program)
            fn = gt.catch_pairs if program == "catch_pairs" else gt.drops
            ev = fn(con, fielder=fielder, bowler=bowler, team=team, seasons=seasons)
        else:  # ranking
            kind = "batter" if re.search(r"\bbat(ter|sman|smen|ters)?s?\b", s["question"], re.I) else "bowler"
            ev = gt.ranking(con, kind, seasons)
        self.trace.append({"step": "relational", "tool": ev.tool, "rows": len(ev.rows), "gap": ev.gap,
                           "players": f.players, "team": team, "seasons": seasons})  # fmt: skip
        return {"evidence": ev}

    def _one(self, sql: str, params: list) -> int:
        row = self.con.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def _fielding_roles(self, ids: list[int], program: str) -> tuple[int | None, int | None]:
        """One player: a fielder if they field more than they bowl in these edges. Two: (fielder, bowler)."""
        table = "fielding_dismissal" if program == "catch_pairs" else "dropped"

        def count(col, pid):
            return self._one(f"SELECT coalesce(sum(n), 0) FROM {table} WHERE {col} = ?", [pid])

        if not ids:
            return None, None
        if len(ids) == 1:
            pid = ids[0]
            return (pid, None) if count("fielder_id", pid) >= count("bowler_id", pid) else (None, pid)
        a, b = ids[:2]
        q = f"SELECT coalesce(sum(n), 0) FROM {table} WHERE fielder_id = ? AND bowler_id = ?"
        ab, ba = self._one(q, [a, b]), self._one(q, [b, a])
        return (a, b) if ab >= ba else (b, a)

    def _write(self, s: Any) -> dict[str, Any]:
        ev: gt.Evidence = s["evidence"]
        src = Sources()
        for i, line in enumerate(ev.lines, 1):  # tags follow lines (a tie line has no row of its own)
            src.stats[f"G{i}"] = {"question": ev.tool, "reply": line}
        if ev.gap or not self.llm or not getattr(self.llm, "available", False):
            mode = "gap" if ev.gap else "extractive"
            return {"answer": self._answer(extractive(ev), "relational", s, src, mode=mode, ev=ev)}
        msgs = [
            {"role": "system", "content": WRITER},
            {"role": "user", "content": f"QUESTION: {s['question']}\n\nEVIDENCE:\n" + "\n".join(ev.lines)},
        ]
        problems: list[str] = []
        model = None
        for attempt in range(2):
            try:
                out = self.llm.chat(msgs, max_tokens=400)
            except LLMUnavailable as exc:
                self.trace.append({"step": "llm_unavailable", "error": str(exc)[:160]})
                break
            model = out.get("model")
            text = clean(out["message"].get("content") or "")
            problems = validate(text, ev.lines, s["question"])
            self.trace.append({"step": "validate", "attempt": attempt + 1, "problems": problems})
            if not problems:
                return {"answer": self._answer(text, "relational", s, src, model=model, ev=ev)}
            msgs += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Fix these problems and reply again: " + "; ".join(problems)}]  # fmt: skip
        ans = self._answer(extractive(ev), "relational", s, src, mode="extractive", ev=ev, model=model)
        ans.problems = problems
        return {"answer": ans}

    def _answer(self, text, program, s: Any, src: Sources, mode="llm", ev=None, model=None) -> Answer:
        ans = Answer(text, program, s["frame"].as_dict() if s.get("frame") else {}, src, mode=mode, model=model,
                     trace=list(self.trace))  # fmt: skip
        if ev is not None:
            ans.extra["graph"] = ev.as_payload()
        return ans
