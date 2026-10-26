# Evaluation results

All runs on 24–25 Oct 2026 against the archive through IPL 2026. Gold answers are independent SQL on the
archive (not the system's own queries); a source's stated answer is also noted where it is out of date.

**Clean** = new questions, run once on frozen code. **Practice** = re-scored after fixes informed by those questions.

## Public-question tests (scripts in `scripts/eval/`)

| Run | Router | Sources | Score | Misses (cause) |
|---|---|---|---|---|
| Forum/FAQ 20 | rules | Infinity Learn, ESPN, cricket.com, Sportskeeda | 11/20 clean | event counts missing; wrong cricbot table accepted; no fallback for unusual queries |
| Fresh set A | rules | ETV Bharat, Gulf News, Howstat, Wikipedia | 16/20 clean | 5-wicket hauls counted as 4+; runner-up count miscounted by the writer; fastest-century SQL; final margin mis-routed |
| Fresh set B | rules | Scroll, Business Standard, CricTracker, WION | 16/20 clean | season ranking answered with players; per-team table for a total; chase mis-routed; partnership lookup bug |
| Fresh 40, batch 1 | intent | Outlook, cricket.com, RCB, CricTracker | 19/20 clean | "most POTM awards in finals": cricbot dropped the finals filter |
| Fresh 40, batch 2 | intent | Sportskeeda ×3, Scroll | 19/20 clean (15 exact) | best death strike rate: finder ignored "minimum 10 innings" and counted run-outs |

Fresh 40 in total: **38/40 right answer, 34/40 exact** (every number and name right); route 39/40, the one miss
a labelling error. The four inexact answers: Wankhede highest chase (number right, teams not named); most death
wickets since 2016 (Bumrah right, 97 vs 98); best death economy (Narine right, 7.48 vs 7.33 because byes were
counted); most fours in a season (Warner 88 right, but tied with Sai Sudharsan 88, not mentioned). Batch 2 ran in
three sittings because of Groq's daily cap; two answers came from the extractive fallback when the quota ran out.

Every miss above was followed by a general fix (no per-question rules); later scores on those same questions
are practice scores.

## Benchmarks

| Suite | Score | Notes |
|---|---|---|
| v3 (50) | cricbot rules 27, cricbot app 29, Pandit first run **29** (clean) | later 36–38 (practice); varies ±2 run to run with cricbot's planner |
| Events suite (20) | 20/20 | wicket, drop, fastest fifty, top partnership; SQL gold by construction |
| Routing, rules router | 123/135 reach an acceptable program | lenient: a wrong cricbot table still counts; 6 misses were quota outages |

## Momentum audit (`scripts/eval/momentum_audit.py`, no LLM)

| | Before | After |
|---|---|---|
| Decisive shift favours the winner (1,218 matches) | 75.3% | **99.7%** |
| Detector types firing | collapses, chase swings, dropped catches | + scoring bursts, squeezes |

Fixes: the decisive shift is the strongest in the winner's favour; over numbers are now passed to the
detector (bursts and squeezes never fired before). Four matches have no winner-favouring shift and show none.

## Commentary retrieval (96 held-out queries)

| Setup | hit@1 | hit@5 | recall@10 | p50 latency |
|---|---|---|---|---|
| BM25 + frame filters (default) | 0.60 | 0.92 | 0.84 | 14 ms |
| Hybrid (BM25 + bge-small, RRF) + filters | 0.60 | 0.82 | 0.80 | 35 ms |
| Dense only + filters | 0.48 | 0.73 | 0.70 | 24 ms |
| Hybrid + MiniLM reranker | 0.59 | 0.81 | 0.81 | 475 ms |
| BM25, no filters | 0.52 | 0.78 | 0.78 | 11 ms |

## Labels

`routing_labels.jsonl`: 135 questions with the intent form and acceptable programs. Drafted by an AI;
rows count as gold only once `verified` is set to true by a person.
