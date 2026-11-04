"""Small text helpers shared by indexing, the agent and validation."""

from __future__ import annotations

import html
import re

_TAG = re.compile(r"<[^>]+>")
_BOLD = re.compile(r"<(b|strong)>(.*?)</\1>", re.I | re.S)
_WS = re.compile(r"\s+")
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")


def strip_html(text: str | None) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", text or ""))).strip()


_COMMENT = re.compile(r"<!--.*?(-->|$)", re.S)
_BLOCK = re.compile(r"<(script|style|iframe|blockquote)\b.*?(</\1>|$)", re.I | re.S)
SNIPPET_CHARS = 300  # ESPN commentary is shown as short, cited snippets only (Rules.md 17)


def snippet(text: str | None, limit: int = SNIPPET_CHARS) -> str:
    """Plain text for display: no HTML, comments or embeds, at most `limit` characters, cut at a word."""
    out = strip_html(_BLOCK.sub(" ", _COMMENT.sub(" ", text or "")))
    if len(out) <= limit:
        return out
    cut = out[: limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or out[:limit]) + "…"


def bold_spans(text: str | None) -> list[str]:
    """Commentators bold the notable event of a ball; the fielding work measured it as a precision signal."""
    return [strip_html(m.group(2)) for m in _BOLD.finditer(text or "") if strip_html(m.group(2))]


def numbers(text: str) -> set[str]:
    """Numbers as normalised strings ('973', '7.5'); commas inside numbers are removed first."""
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    out = set()
    for raw in NUMBER.findall(cleaned):
        out.add(str(float(raw)).rstrip("0").rstrip(".") if "." in raw else str(int(raw)))
    return out


def fts_query(text: str) -> str:
    """LanceDB FTS takes a query string; keep words only so punctuation can't break parsing."""
    return " ".join(re.findall(r"[A-Za-z0-9']+", text)).replace("'", " ").strip()
