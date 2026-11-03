"""Commentary shown to people is a short plain-text snippet (Rules.md 17), whatever its source."""

from __future__ import annotations

from cricrag.agent.pandit import _display
from cricrag.matchpage import cap_commentary
from cricrag.text import SNIPPET_CHARS, snippet

BLOG = (
    "<b>Bhuvi nails the yorker, RCB are champions!</b> "
    + "Presentation: 'I have given my youth, my prime and my experience to this franchise' - Kohli. " * 20
    + '<!-- twitter --><blockquote class="twitter-tweet"><a href="https://twitter.com/x/status/1234567890123">'
    "pic.twitter.com/abc</a></blockquote> Fan comment: unreal scenes in Ahmedabad!"
)


def test_snippet_strips_html_comments_embeds_and_caps_at_a_word():
    out = snippet(BLOG)
    assert len(out) <= SNIPPET_CHARS + 1 and out.endswith("…")
    assert out.startswith("Bhuvi nails the yorker, RCB are champions!")
    for raw in ("<", ">", "href", "twitter", "&quot;"):
        assert raw not in out
    assert not out[:-1].endswith(" ")


def test_snippet_keeps_short_text_and_handles_empty():
    assert snippet("Full and straight, <b>bowled him!</b>") == "Full and straight, bowled him!"
    assert snippet(None) == "" and snippet("") == ""
    assert snippet("<!-- unclosed comment then nothing") == ""


def test_cap_commentary_on_a_match_page():
    detail = {
        "balls": [
            {"label": "19.6", "text": "Kumar to Shashank, SIX. " + BLOG, "commentary": BLOG},
            {"label": "1.1"},
        ],
        "analytics": {"shifts": [{"evidence": [{"label": "19.6", "text": BLOG}]}]},
    }
    out = cap_commentary(detail)
    last = out["balls"][0]
    assert len(last["text"]) <= SNIPPET_CHARS + 1 and len(last["commentary"]) <= SNIPPET_CHARS + 1
    assert "<a" not in last["commentary"] and "<!--" not in last["text"]
    assert len(out["analytics"]["shifts"][0]["evidence"][0]["text"]) <= SNIPPET_CHARS + 1
    assert out["balls"][1] == {"label": "1.1"}


def test_extractive_display_snippets_commentary_but_keeps_tags():
    line = f"IPL 2025 Final over 19 BODY: {BLOG} [C1]"
    shown = _display(line)
    assert shown.endswith("… [C1]") and "BODY" not in shown and "<b>" not in shown
    assert _display(f"C2: {BLOG}").startswith("C2: Bhuvi nails")
    assert _display("Turning point: PBKS innings [M1]") == "Turning point: PBKS innings [M1]"
