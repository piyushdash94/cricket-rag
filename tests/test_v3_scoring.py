from cricrag.eval.v3_baseline import score

REFUSE = [["not in", "outside", "can't"]]


def test_answer_needs_every_group_and_no_question():
    item = {"behaviour": "answer", "checks": [["Tahir"], ["26"]]}
    assert score(item, "Imran Tahir took 26 wickets.", [], [])[0]
    assert not score(item, "Imran Tahir took 260 wickets.", [], [])[0]  # whole numbers only
    assert not score(item, "Which Tahir? Imran Tahir took 26?", [], [])[0]


def test_clarify_needs_two_options():
    item = {"behaviour": "clarify", "checks": [["KL Rahul", "Rahul Tripathi", "Rahul Tewatia"]]}
    assert score(item, "Which Rahul do you mean: KL Rahul or Rahul Tripathi?", [], [])[0]
    assert not score(item, "KL Rahul scored 616.", [], [])[0]


def test_refuse_must_not_run_queries():
    item = {"behaviour": "refuse", "checks": REFUSE}
    assert score(item, "That is outside the IPL archive.", [], [])[0]
    assert not score(item, "That is outside the IPL archive.", [{"name": "champions"}], [])[0]


def test_say_unknown_accepts_published_answer():
    item = {"behaviour": "say_unknown", "checks": REFUSE, "gold_answer": "Ben Cutting (published); no awards"}
    assert score(item, "Ben Cutting was Player of the Match.", [], [])[0]


def test_define_metric_any_rubric_option():
    item = {
        "behaviour": "define_metric",
        "rubric": [
            {"metric": "runs", "must": [["runs"], ["Kohli"]]},
            {"metric": "average", "must": [["average"], ["Sudharsan"]]},
        ],
    }
    ok, note = score(item, "By average (min 500 balls), Sai Sudharsan leads.", [], [])
    assert ok and note == "metric: average"


def test_route_match_needs_open_action():
    item = {"behaviour": "route_match", "checks": [["Gayle"], ["76"]]}
    assert score(item, "Chris Gayle top scored with 76.", [], [{"type": "open_match", "match_id": 1}])[0]
    assert not score(item, "Chris Gayle top scored with 76.", [], [])[0]
