"""IPL reading for the UI's left rail (and `cricrag news`): links only, no article text is stored.

    live     ESPN's public cricket news feed (the one cricdata's ESPN calls sit beside), kept to stories
             that mention the IPL, sorted into rules / trends / records by keywords, cached for six hours
    reading  a short, hand-checked list of ESPNcricinfo pieces on rule changes, trends and records, so
             the rail is useful in the off-season when the live feed has no IPL stories

Nothing here feeds Pandit's answers: numbers in answers still come only from SQL.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

FEED = "https://site.api.espn.com/apis/site/v2/sports/cricket/8048/news"
UA = {"User-Agent": "Mozilla/5.0 (cricket-rag; IPL reading list)"}
TTL = 6 * 3600

IPL = re.compile(r"\b(IPL|Indian Premier League)\b")
KINDS = {
    "rules": re.compile(
        r"\b(rule|rules|regulation\w*|impact player|playing conditions?|ball change|DRS|retention|auction rules?|"
        r"ban(ned)?|BCCI (allows?|approves?|scraps?)|new ball)\b",
        re.I,
    ),
    "records": re.compile(r"\b(records?|fastest|highest|most|milestone|first ever|history)\b", re.I),
    "trends": re.compile(
        r"\b(trends?|analysis|stats|numbers|strike rates?|economy|powerplays?|200-plus|250|death overs|how .* changed)\b",
        re.I,
    ),
}

# Checked by hand on 30 Oct 2026 (search-indexed ESPNcricinfo / ESPN stories). Titles as published.
READING: list[dict[str, Any]] = [
    {
        "kind": "rules",
        "year": 2023,
        "title": "IPL 2023: Impact Player - all you need to know about the new rule",
        "url": "https://www.espncricinfo.com/story/ipl-2023-impact-player-all-you-need-to-know-about-the-new-rule-1350616",
    },
    {
        "kind": "rules",
        "year": 2023,
        "title": "IPL 2023: How much impact will the Impact Player rule have?",
        "url": "https://www.espncricinfo.com/story/ipl-2023-how-much-impact-will-the-impact-player-rule-have-1365678",
    },
    {
        "kind": "rules",
        "year": 2025,
        "title": "IPL to allow one ball change on demand to counter dew",
        "url": "https://global.espn.com/cricket/story/_/id/44342497/ipl-2025-ipl-allow-one-ball-change-demand-counter-dew",
    },
    {
        "kind": "trends",
        "year": 2026,
        "title": "IPL 2026: The season of the Indian batter",
        "url": "https://www.espn.com.au/cricket/story/_/id/48609114/ipl-2026-stats-summer-indian-batter",
    },
    {
        "kind": "trends",
        "year": 2025,
        "title": "IPL 2025 trends: More 200-plus scores than ever, but too few thrillers",
        "url": "https://www.espncricinfo.com/story/ipl-2025-trends-too-many-200-plus-scores-but-not-many-thrillers-1489188",
    },
    {
        "kind": "trends",
        "year": 2025,
        "title": "IPL 2025 vs IPL 2024: Spinners rule as six appeal dips",
        "url": "https://www.espncricinfo.com/story/ipl-2025-vs-ipl-2024-spinners-rule-as-six-appeal-dips-1484014",
    },
    {
        "kind": "trends",
        "year": 2025,
        "title": "IPL 2025 vs IPL 2024: Scoring rates dip as finishers lose their fizz",
        "url": "https://www.espncricinfo.com/story/ipl-2025-vs-2024-run-rates-keep-dipping-as-finishers-lose-their-fizz-1482614",
    },
    {
        "kind": "trends",
        "year": 2024,
        "title": "Stats: IPL 2024, a turbo-charged, batter-dominated season like no other",
        "url": "https://www.espncricinfo.com/story/stats-ipl-2024-a-turbo-charged-batter-dominated-season-like-no-other-1430018",
    },
    {
        "kind": "records",
        "year": 2025,
        "title": "IPL 2025: Fastest hundreds in the IPL - Abhishek Sharma joins the list",
        "url": "https://www.espncricinfo.com/story/ipl-2025-fastest-hundreds-in-the-ipl-abhishek-sharma-joins-the-list-1480930",
    },
    {
        "kind": "records",
        "year": 2025,
        "title": "Fastest hundreds in the IPL - Priyansh Arya enters the top five",
        "url": "https://www.espncricinfo.com/story/priyansh-arya-hits-the-joint-fourth-fastest-century-in-ipl-history-1480377",
    },
    {
        "kind": "records",
        "year": 2024,
        "title": "Who has the best batting average in the IPL?",
        "url": "https://www.espncricinfo.com/story/who-has-the-best-batting-average-in-the-ipl-ask-steven-1428337",
    },
    {
        "kind": "records",
        "year": 2024,
        "title": "Sunrisers break the record for the highest IPL total in a six-fest",
        "url": "https://africa.espn.com/cricket/story/_/id/39820940/srh-vs-mi-stats-sunrisers-break-record-highest-ever-ipl-total-six-fest",
    },
]


def kind_of(text: str) -> str:
    return next((k for k, rx in KINDS.items() if rx.search(text)), "news")


def _source(url: str) -> str:
    return "ESPNcricinfo" if "espncricinfo.com" in url else "ESPN"


def parse_feed(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """ESPN feed JSON -> IPL stories with a web link."""
    out = []
    for a in payload.get("articles") or []:
        text = f"{a.get('headline', '')} {a.get('description', '')}"
        leagues = " ".join(str(c.get("description", "")) for c in a.get("categories") or [])
        url = ((a.get("links") or {}).get("web") or {}).get("href")
        if not url or not (IPL.search(text) or IPL.search(leagues)):
            continue
        out.append(
            {
                "kind": kind_of(text),
                "title": a.get("headline", "").strip(),
                "summary": (a.get("description") or "").strip()[:160],
                "url": url.replace("http://", "https://", 1),
                "published": a.get("published"),
                "source": _source(url),
            }
        )
    return out


def live(cache: Path | None = None, fetch=None) -> list[dict[str, Any]]:
    """Current IPL stories from the ESPN feed, cached on disk; [] when offline or nothing is about the IPL."""
    if cache and cache.is_file() and time.time() - cache.stat().st_mtime < TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    try:
        if fetch is None:
            import httpx

            fetch = lambda: httpx.get(FEED, params={"limit": 100}, headers=UA, timeout=10).json()  # noqa: E731
        stories = parse_feed(fetch())
    except Exception:
        return []
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(stories), encoding="utf-8")
    return stories


def articles(cache: Path | None = None, fetch=None) -> dict[str, Any]:
    return {
        "live": live(cache, fetch),
        "reading": [{**r, "source": _source(r["url"])} for r in READING],
        "kinds": ["rules", "trends", "records"],
    }
