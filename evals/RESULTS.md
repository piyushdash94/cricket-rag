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

## 30 Oct: nine questions from the UI, then a regression rerun

Nine questions asked in the match centre (two of them follow-ups) were checked against SQL. Four were right.
The five misses had five different causes. Each got a general fix (no rules written for one question):

| Question | Before | Cause | After |
|---|---|---|---|
| Biggest batting collapse, 2026 | GT all out 100 (wrong) | "collapse" didn't filter to wicket clusters | DC 6 for 8 v RCB |
| "Commentary snippets from this match" | snippets from unrelated matches | follow-up lost the match | resolved from the last answer; that match opens beside the chat |
| Kohli's death-overs average, 2024 | "average not in the evidence" | table had runs and outs, no average | 48.5, computed in code |
| Fewest balls to 100 sixes | Gayle, "866.53 balls per 100" (a rate) | finder used a rate; writer's abstention ended the run | Russell, 657 balls (running total) |
| Highest spinner death economy, 2026 | the *best* economies | planner sent order=best; "top 5" sent as a chase target | Kuldeep 12.83 … Dubey 8.40 |
| Kohli vs spin in each phase since 2020 | powerplay only | one phase planned for "each phase" | 63.2 / 57.94 / 25.67 |

Kohli's middle-overs figure is 57.94 when run outs count as dismissals (cricbot) and 61.6 when only the bowler's
wickets count; both are standard, so the answer is right under cricbot's stated definition.

Rerun of fresh 40, batch 2 (same 20 questions, no LLM cache, after all fixes): **20/20 right, 17 exact**
(was 19/20 and 15). Death strike rate now honours "minimum 10 innings"; death economy now excludes byes
(7.33, was 7.48). Still inexact: highest Wankhede chase (teams not named), Bumrah 97 death wickets since 2016
(98), most fours in a season (the tie with Warner not mentioned). This is a practice score: these questions
had been seen.

Tokens on that batch: 57.1k vs 56.8k on 25 Oct (+0.5%). Fixing citation slips in code saved a retry where it
fired (one question), and long tables are capped at 12 rows for the writer, but route changes between runs
(one question moved to the finder, another away from it) outweigh both. No token saving is claimed.

## 30 Oct evening: twelve more questions from the UI

Checked against SQL: 5 of 12 were right. After the fixes below, 11 are right and one now gets a clarifying
question (re-run without the LLM cache, follow-ups with history):

| Question | Before | Cause | After |
|---|---|---|---|
| Dhoni's average v left-arm spin | a table of bowlers v left-handers | "Dhoni's" not linked; cricbot has spin/pace only | 42.87 (finder, ball-level; 45.93 counting only the bowler's wickets) |
| "how many dot balls did **he** bowl?" | 771 | "he" not resolved; cricbot re-added the earlier team | 84 for the bowler just named |
| Costliest drops | fielders with most drops | routed to fielding counts | KL Rahul +136, Abhishek Sharma +135, de Kock +127 … |
| Biggest momentum swing | a 21-run over from commentary | shifts excluded from cross-match questions | RCB v SRH 2025: 7 for 16, swing 81.3 |
| Most tosses won | "toss data not available" | toss not in the finder's views | MI 160 |
| 2017 final in detail | over-by-over commentary, no result, a fake "[HEADER]" tag | story writer rejected (a name the evidence didn't have); fell to search | the match facts, result and turning point |
| Left-arm bowler v left-arm spinners | Noor Ahmad from a spinners-v-left-handers table | the question pairs a bowler with a bowler type | asks which comparison was meant |

Right before and still right: most wides 2026 (Arshdeep 28), best finishers, most player-of-the-match awards by a
bowler (Narine 18), fastest to 100 wickets (Rabada, 64 innings / 1,438 balls). Best economy v RR in 2008 is
Maharoof 5.58 at cricbot's 60-ball minimum (Irfan Pathan 3.88 off 48 balls without one); the reply does not
always say the minimum. Dot balls depend on the definition: 84 with no runs off the ball, 90 when byes and leg
byes count as dots.

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
