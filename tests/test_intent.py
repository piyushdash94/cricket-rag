"""Intent router: form parsing, route table, result-shape check, and the predict refusal end to end."""

import json

from cricrag.agent import intent as itn
from cricrag.agent.pandit import Pandit
from cricrag.frame import build_frame


class FormLLM:
    available = True

    def __init__(self, form):
        self.form = form

    def chat(self, messages, tools=None, max_tokens=900):
        return {"message": {"content": json.dumps(self.form)}, "model": "fake"}


class NoLLM:
    available = False


def form(**kw):
    return itn.parse(json.dumps(kw))


def test_parse_keeps_only_allowed_values():
    it = itn.parse(
        'noise {"answer": "rank", "unit": "season", "measure": "hundreds", "scope": "nonsense"} tail'
    )
    assert (it.answer, it.unit, it.measure, it.scope) == ("rank", "season", "hundreds", "career")
    assert itn.parse("not json").answer == "other"


def test_routes_for_last_rounds_misses(catalog):
    f = build_frame("Which IPL season had the most centuries?", catalog)
    assert itn.routes(form(answer="rank", unit="season", measure="hundreds"), False, f) == ["stats", "finder"]
    assert (
        itn.routes(form(answer="moment", unit="partnership", measure="partnership"), False, f)[0] == "event"
    )
    assert itn.routes(form(answer="rank", unit="player", measure="maidens"), False, f)[0] == "leaderboard"
    assert itn.routes(form(answer="value", unit="match", measure="margin"), True, f)[0] == "match"
    assert itn.routes(form(answer="value", unit="team", measure="chase"), False, f) == ["stats", "finder"]
    assert itn.routes(form(answer="predict"), False, f) == ["refuse"]


def test_shape_check_rejects_nearby_answers(catalog):
    f = build_frame("Which IPL season had the most centuries?", catalog)
    by_player = [{"title": "best seasons", "columns": ["rank", "player", "season", "hundreds"], "rows": 5}]
    assert (
        itn.shape_fits(form(answer="rank", unit="season"), by_player, f) == "shape_wants_seasons_got_players"
    )
    by_season = [{"title": "", "columns": ["season", "hundreds"], "rows": 19}]
    assert itn.shape_fits(form(answer="rank", unit="season"), by_season, f) is None
    g = build_frame("How many 200-plus team totals were there in IPL 2024?", catalog)
    by_team = [{"title": "", "columns": ["rank", "team", "times"], "rows": 10}]
    assert (
        itn.shape_fits(form(answer="count", unit="innings"), by_team, g)
        == "shape_wants_one_total_got_breakdown"
    )
    assert itn.shape_fits(form(answer="count", unit="innings"), by_team, g, "41 in total") is None


def test_predict_is_refused_without_queries(catalog):
    p = Pandit(None, catalog, NoLLM(), intent_llm=FormLLM({"answer": "predict"}), router="intent")
    a = p.ask("Who will win the next IPL?")
    assert a.program == "refuse" and any(t.get("step") == "intent" for t in a.trace)


def test_rules_router_when_intent_llm_is_down(catalog):
    p = Pandit(None, catalog, NoLLM(), router="intent")  # intent llm unavailable -> rules path
    assert p.ask("Who won the Duleep Trophy in 2024?").program == "refuse"


def test_route_eval_scores_the_first_route(catalog, tmp_path):
    from cricrag.eval import routes as rt

    lab = tmp_path / "l.jsonl"
    lab.write_text(json.dumps({"id": "x", "set": "t", "question": "Who will win the next IPL?", "answer": "predict",
                               "unit": "team", "measure": "titles", "scope": "season", "routes": ["refuse"]}) + "\n")  # fmt: skip
    p = Pandit(
        None, catalog, NoLLM(), intent_llm=FormLLM({"answer": "predict", "unit": "team"}), router="intent"
    )
    rows = rt.run(p, rt.load(lab, None), "plan")
    assert rows[0]["ok"] and rows[0]["form_match"]["answer"] and not rows[0]["form_match"]["measure"]
    assert "ALL | 1/1" in rt.report(rows)
