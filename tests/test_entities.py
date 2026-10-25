from cricrag.entities import link_players, link_teams, link_years
from cricrag.frame import build_frame


def names(q, cat):
    return link_players(q, cat).players


def test_full_name_and_surname(catalog):
    assert names("How did Virat Kohli bat?", catalog) == ["Virat Kohli"]
    assert names("bumrah's spell", catalog) == ["Jasprit Bumrah"]


def test_first_name_dominance_rashid_is_rashid_khan(catalog):
    # cricbot v2 finding: "Rashid" resolved to Adil Rashid. The 3x dominance rule fixes it.
    assert names("Rashid's economy", catalog) == ["Rashid Khan"]


def test_shared_name_is_ambiguous_not_guessed(catalog):
    linked = link_players("How many runs did Rahul score in 2022?", catalog)
    assert linked.players == []
    assert set(linked.ambiguous["rahul"]) >= {"KL Rahul", "Rahul Tripathi", "Rahul Tewatia", "Rahul Chahar"}
    assert "pandya" in link_players("Pandya's batting", catalog).ambiguous


def test_nicknames_and_typos(catalog):
    assert names("SKY vs Bhuvi", catalog) == ["Suryakumar Yadav", "Bhuvneshwar Kumar"]
    linked = link_players("kholi against bumrha", catalog)
    assert linked.players == ["Virat Kohli", "Jasprit Bumrah"]
    assert linked.corrected == {"kholi": "kohli", "bumrha": "bumrah"}


def test_common_word_surnames_need_capitals(catalog):
    assert names("the ball went over his head", catalog) == []
    assert names("How did Head bat in the final?", catalog) == ["Travis Head"]


def test_two_word_surname(catalog):
    assert names("de villiers 360", catalog) == ["AB de Villiers"]


def test_years_never_counts(catalog):
    assert link_years("fastest to 2000 runs", catalog) == []
    assert link_years("from 2016 to 2017", catalog) == [2016, 2017]
    assert link_years("the 2020 final", catalog) == [2020]


def test_era_aware_cities(catalog):
    assert link_teams("Hyderabad in 2009", catalog, [2009]) == ["DCH"]
    assert link_teams("Hyderabad in 2016", catalog, [2016]) == ["SRH"]
    assert link_teams("Kings XI Punjab vs Sunrisers", catalog, []) == ["PBKS", "SRH"]


def test_frame_filters(catalog):
    f = build_frame("How did RCB win the 2025 final in the last over while chasing?", catalog)
    assert (f.seasons, f.teams, f.stage, f.over, f.inning) == (["2025"], ["RCB"], "final", 20, 2)
    assert build_frame("death overs in 2024", catalog).phase == "death"


def test_follow_up_carries_entities(catalog):
    hist = [
        {"role": "user", "content": "How did Kohli bat in the 2016 final?"},
        {"role": "assistant", "content": "..."},
    ]
    f = build_frame("And in 2017?", catalog, hist)
    assert f.players == ["Virat Kohli"] and f.seasons == ["2017"]
