"""Deterministic entity linking for the question frame.

Lessons carried over from cricbot's evals:
- full names, fan nicknames and dominant surnames / first names all resolve; a shared name with no
  dominant player (3x the involvement of the next) is *ambiguous* and must be asked about, not guessed;
- typos ("kholi", "bumrha") are corrected only against established players, by edit distance;
- surnames that are also English words ("Head", "Green", "Short") need a capital letter;
- years are 2008 to the latest season, never a run or wicket count;
- team names are era-aware: "Hyderabad" is DCH before 2013 and SRH after.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz.distance import DamerauLevenshtein

TEAM_NAMES: dict[str, str] = {
    "chennai super kings": "CSK",
    "super kings": "CSK",
    "chennai": "CSK",
    "csk": "CSK",
    "mumbai indians": "MI",
    "mumbai": "MI",
    "mi": "MI",
    "royal challengers bengaluru": "RCB",
    "royal challengers bangalore": "RCB",
    "royal challengers": "RCB",
    "bengaluru": "RCB",
    "bangalore": "RCB",
    "rcb": "RCB",
    "kolkata knight riders": "KKR",
    "knight riders": "KKR",
    "kolkata": "KKR",
    "kkr": "KKR",
    "sunrisers hyderabad": "SRH",
    "sunrisers": "SRH",
    "srh": "SRH",
    "deccan chargers": "DCH",
    "deccan": "DCH",
    "dch": "DCH",
    "punjab kings": "PBKS",
    "kings xi punjab": "PBKS",
    "kings xi": "PBKS",
    "punjab": "PBKS",
    "pbks": "PBKS",
    "kxip": "PBKS",
    "rajasthan royals": "RR",
    "rajasthan": "RR",
    "rr": "RR",
    "delhi capitals": "DC",
    "delhi daredevils": "DC",
    "daredevils": "DC",
    "delhi": "DC",
    "dc": "DC",
    "dd": "DC",
    "gujarat titans": "GT",
    "titans": "GT",
    "gt": "GT",
    "gujarat lions": "GL",
    "gl": "GL",
    "lucknow super giants": "LSG",
    "lucknow": "LSG",
    "lsg": "LSG",
    "pune warriors india": "PWI",
    "pune warriors": "PWI",
    "pwi": "PWI",
    "rising pune supergiants": "RPS",
    "rising pune supergiant": "RPS",
    "rps": "RPS",
    "kochi tuskers kerala": "Kochi",
    "kochi tuskers": "Kochi",
    "kochi": "Kochi",
    "ktk": "Kochi",
}
CANON = {"KXIP": "PBKS"}  # archive inconsistency: matches.team1/2 says KXIP where balls says PBKS


def canon(code: str) -> str:
    return CANON.get(code, code)


def codes(team: str) -> list[str]:
    """Every code the archive uses for one franchise."""
    return [team, *[k for k, v in CANON.items() if v == team]]


# City names that point at different franchises in different eras: (code, first_year, last_year).
ERA_CITIES: dict[str, list[tuple[str, int, int]]] = {
    "hyderabad": [("DCH", 2008, 2012), ("SRH", 2013, 2100)],
    "gujarat": [("GL", 2016, 2017), ("GT", 2022, 2100)],
    "pune": [("PWI", 2011, 2013), ("RPS", 2016, 2017)],
}

# Safe global nicknames -> full archive name (fielding's list was built for a 22-man roster; the
# ambiguous ones there, like "murali", "ms", "ro", "dinesh", are left out on purpose).
NICKNAMES: dict[str, str] = {
    "bhuvi": "Bhuvneshwar Kumar",
    "mahi": "MS Dhoni",
    "msd": "MS Dhoni",
    "thala": "MS Dhoni",
    "abd": "AB de Villiers",
    "abdv": "AB de Villiers",
    "jaddu": "Ravindra Jadeja",
    "faf": "Faf du Plessis",
    "hitman": "Rohit Sharma",
    "viru": "Virender Sehwag",
    "yuvi": "Yuvraj Singh",
    "bhajji": "Harbhajan Singh",
    "cheeku": "Virat Kohli",
    "chiku": "Virat Kohli",
    "sky": "Suryakumar Yadav",
    "boom": "Jasprit Bumrah",
    "jassi": "Jasprit Bumrah",
    "dk": "Dinesh Karthik",
    "polly": "Kieron Pollard",
    "dre russ": "Andre Russell",
    "watto": "Shane Watson",
    "maxi": "Glenn Maxwell",
    "gauti": "Gautam Gambhir",
    "jinks": "Ajinkya Rahane",
    "kl": "KL Rahul",
    "miyan": "Mohammed Siraj",
    "washi": "Washington Sundar",
    "gilly": "Adam Gilchrist",
    "hardik": "Hardik Pandya",
    "krunal": "Krunal Pandya",
    "kuldeep": "Kuldeep Yadav",
    "shubman": "Shubman Gill",
    "tilak": "Tilak Varma",
    "davey": "David Warner",
    "sanju": "Sanju Samson",
    "rishabh": "Rishabh Pant",
}

# Surnames / first names that are ordinary English words: link only when capitalised.
COMMON_WORDS = {
    "head",
    "green",
    "short",
    "wood",
    "hope",
    "price",
    "king",
    "bird",
    "rain",
    "will",
    "may",
    "mark",
    "lewis",
    "dean",
    "rich",
    "young",
    "hill",
    "little",
    "brown",
    "white",
    "cook",
    "morris",
    "rose",
    "bell",
    "chase",
    "salt",
    "stone",
    "fine",
    "ward",
    "hunt",
    "long",
    "power",
    "patience",
    "rush",
    "sharp",
    "over",
    "mid",
    "point",
    "cover",
    "the",
    "and",
    "who",
    "what",
    "how",
    "did",
    "was",
    "in",
    "on",
    "at",
    "a",
    "an",
    "of",
    "to",
    "by",
    "for",
    "with",
    "his",
    "her",
    "him",
    "it",
    "is",
    "be",
    "do",
    "run",
    "runs",
    "six",
    "four",
    "out",
    "catch",
    "drop",
    "dropped",
    "ball",
    "bat",
    "win",
    "won",
    "final",
    "match",
    "most",
    "best",
    "first",
    "last",
    "next",
    "game",
    "season",
    "team",
    "player",
    "players",
    "turn",
    "spell",
    "overs",
    "innings",
    "score",
    "chasing",
    "partnership",
    "describe",
    "tell",
    "show",
    "moment",
}

YEAR = re.compile(r"\b(20(?:0[89]|1\d|2\d))\b")
YEAR_RANGE = re.compile(r"\b(20[0-2]\d)\s*(?:-|–|to|till|until|through)\s*(20[0-2]\d|\d{2})\b")
WORD = re.compile(r"[A-Za-z][A-Za-z'.-]*")


@dataclass(frozen=True)
class Player:
    name: str
    involvement: int  # balls faced + bowled; the dominance prior


@dataclass
class Catalog:
    """Everything the linker needs, loaded once from the archive (or built directly in tests)."""

    season_years: dict[str, int]  # '2007/08' -> 2008
    team_codes: set[str]
    players: list[Player]
    by_token: dict[str, list[Player]] = field(default_factory=dict)
    by_full: dict[str, Player] = field(default_factory=dict)

    def __post_init__(self) -> None:
        tok: dict[str, list[Player]] = defaultdict(list)
        for p in self.players:
            self.by_full[p.name.casefold()] = p
            parts = [w.casefold() for w in WORD.findall(p.name)]
            keys = {parts[-1]} if parts else set()
            if len(parts) >= 2 and len(parts[0]) > 2 and "." not in parts[0]:
                keys.add(parts[0])  # first names ("Rashid"), not initials ("MS")
            if len(parts) >= 3:
                keys.add(" ".join(parts[-2:]))  # "de villiers", "du plessis"
            for k in keys:
                tok[k].append(p)
        self.by_token = {k: sorted(v, key=lambda p: -p.involvement) for k, v in tok.items()}

    @property
    def latest_year(self) -> int:
        return max(self.season_years.values())

    def season_for(self, year: int) -> str | None:
        return next((s for s, y in self.season_years.items() if y == year), None)


@dataclass
class Linked:
    players: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)  # token -> candidate full names
    corrected: dict[str, str] = field(default_factory=dict)  # typo -> token used


def dominant(cands: list[Player]) -> Player | None:
    if len(cands) == 1:
        return cands[0]
    if cands and cands[0].involvement >= 3 * max(cands[1].involvement, 1):
        return cands[0]
    return None


def link_players(question: str, cat: Catalog, min_typo_involvement: int = 300) -> Linked:
    out = Linked()
    text = " " + re.sub(r"[^\w'.-]+", " ", question) + " "
    low = text.casefold()
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    found: dict[str, int] = {}

    def add(name: str, a: int, b: int) -> None:
        taken.append((a, b))
        found.setdefault(name, a)
        if name not in out.players:
            out.players.append(name)

    # 1. full names, longest first
    for full in sorted(cat.by_full, key=len, reverse=True):
        for m in re.finditer(rf"(?<![\w]){re.escape(full)}(?![\w])", low):
            if free(m.start(), m.end()):
                add(cat.by_full[full].name, m.start(), m.end())
    # 2. nicknames
    for nick, full in NICKNAMES.items():
        for m in re.finditer(rf"(?<![\w]){re.escape(nick)}(?![\w])", low):
            if free(m.start(), m.end()) and full.casefold() in cat.by_full:
                add(full, m.start(), m.end())
    # 3. two-word surname keys, then single tokens (surname or first name), with typo correction
    tokens = [(m.group(0), m.start(), m.end()) for m in WORD.finditer(text)]
    i = 0
    while i < len(tokens):
        word, a, b = tokens[i]
        if i + 1 < len(tokens):
            pair = f"{word} {tokens[i + 1][0]}".casefold()
            if pair in cat.by_token and free(a, tokens[i + 1][2]):
                _resolve(pair, cat.by_token[pair], out, add, a, tokens[i + 1][2])
                i += 2
                continue
        key = word.casefold().strip(".'")
        if free(a, b) and len(key) > 2:
            if key in COMMON_WORDS and not (word[0].isupper() and key in cat.by_token):
                i += 1
                continue
            if key in cat.by_token:
                _resolve(key, cat.by_token[key], out, add, a, b)
            elif len(key) >= 5 and key not in COMMON_WORDS:
                fixed = _typo(key, cat, min_typo_involvement)
                if fixed:
                    out.corrected[key] = fixed
                    _resolve(fixed, cat.by_token[fixed], out, add, a, b)
        i += 1
    out.players = sorted(out.players, key=lambda n: found[n])  # question order
    return out


def _resolve(key: str, cands: list[Player], out: Linked, add, a: int, b: int) -> None:
    already = [c for c in cands if c.name in out.players]
    if already:
        return
    best = dominant(cands)
    if best:
        add(best.name, a, b)
    else:
        out.ambiguous[key] = [c.name for c in cands[:6]]


def _typo(key: str, cat: Catalog, min_involvement: int) -> str | None:
    limit = 1 if len(key) <= 7 else 2
    best: tuple[int, str] | None = None
    for token, cands in cat.by_token.items():
        if " " in token or abs(len(token) - len(key)) > limit or cands[0].involvement < min_involvement:
            continue
        d = DamerauLevenshtein.distance(key, token)
        if d <= limit and (best is None or d < best[0]):
            best = (d, token)
    return best[1] if best else None


def link_teams(question: str, cat: Catalog, years: list[int]) -> list[str]:
    low = " " + re.sub(r"[^\w]+", " ", question.casefold()) + " "
    found: list[str] = []
    taken: list[tuple[int, int]] = []
    for alias in sorted(TEAM_NAMES, key=len, reverse=True):
        for m in re.finditer(rf" {re.escape(alias)} ", low):
            if all(m.end() <= x or m.start() >= y for x, y in taken):
                taken.append((m.start(), m.end()))
                code = TEAM_NAMES[alias]
                if code in cat.team_codes and code not in found:
                    found.append(code)
    for city, eras in ERA_CITIES.items():
        for m in re.finditer(rf" {city} ", low):
            if not all(m.end() <= x or m.start() >= y for x, y in taken):
                continue
            hits = [c for c, lo, hi in eras if not years or any(lo <= y <= hi for y in years)]
            for c in hits:
                if c in cat.team_codes and c not in found:
                    found.append(c)
    return found


def link_years(question: str, cat: Catalog) -> list[int]:
    latest = cat.latest_year
    years: set[int] = set()
    for a, b in YEAR_RANGE.findall(question):
        lo, hi = int(a), int(b) if len(b) == 4 else int(a[:2] + b)
        if 2008 <= lo <= hi <= latest:
            years.update(range(lo, hi + 1))
    for y in YEAR.findall(question):
        if 2008 <= int(y) <= latest:
            years.add(int(y))
    low = question.casefold()
    if re.search(r"\b(last|latest|previous|this|current) (season|year|ipl)\b", low):
        years.add(latest)  # the archive's newest completed season
    return sorted(years)
