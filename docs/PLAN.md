# cricket-rag: original plan (24 Oct 2026)

> Historical: the plan written before any code. The README describes what was built.

Status: plan only, no code yet. Written 24 Oct 2026 after reviewing `cricbot-duckdb`
(eval READMEs, `data/eval_qna_rules.json`, the 349-question gold set, the slice compiler,
the git history and the fielding audit sample).

**Revision 2 (same day):** merged with the 23 Oct review "Enhancing cricbot question routing"
(arbiter table, number-to-name binding, template plan cache, run-both-plans, slice-spec growth,
gated text-to-SQL, recorded plans in CI, metamorphic tests) and the `ipl_fielding` package.
Main change: **retrieval adds a third plan source, so the zero-token hardening in cricbot
(§3b) comes first, or at least alongside.** The LanceDB entity index can start right away,
because it doesn't depend on that hardening.

---

## 0. What the evidence already says

These come from measured runs, not opinion. Every design choice below traces back to one.

| # | Lesson | Evidence |
|---|---|---|
| L1 | Numbers come from SQL, never from text. The grounding checks (numbers, names, winner, top-row rank) are why the app works. | 35/38 LLM answers kept every number; the rest fell back safely |
| L2 | **Static prompt examples get copied.** | "chahal wickets" planned as *Jasprit Bumrah*, an example name from the planner prompt |
| L3 | Keyword rules overfit. Fixing on a held-out set turns it into a dev set. | v1 rules: dev 100% vs held-out 79%; v2 held-out 79% then "now effectively dev" |
| L4 | The LLM planner is nondeterministic across providers, and rate limits change the model mid-run. | "same plan" 14/20; 17/32 v3 plans came from OpenRouter after Groq's quota ran out |
| L5 | **Entity resolution is the largest failure family.** | First names, nicknames, shared surnames, batter/bowler order, typos, and the open bug: a non-IPL person ("Smriti Mandhana") gets the IPL leaderboard |
| L6 | Answering a *nearby* question is the worst failure, because it looks right. | "overs 12-20" answered as death overs → coverage guard |
| L7 | Check scope before planning. | Out-of-scope refusals went from 4/12 to 12/12 |
| L8 | Report scores per capability. A "can't" is not a "wrong". | `capability` field in the gold set |
| L9 | **`benchmark/v3_new_questions` (50 questions) has never been run.** It is the only clean held-out set left. | its README |
| L10 | The number check is a **set subset**, so swapped values pass ("Kohli 973, Gill 890" against a table of Kohli 890, Gill 973). | `_numbers_are_grounded` |
| L11 | `prefer_rules()` arbitrates LLM vs rules with unnamed heuristics, so a wrong pick can't be traced to a cause. | 23 Oct review |
| L12 | Commentary coverage is close to complete: **295,495 balls with ESPN text** in the fielding copy of the archive. | fielding project, 19 Oct |

Point L9 is the most important operational fact in this plan: **run v3-new once on the
current app before changing anything**, and never tune on it.

---

## 1. Premise check: where retrieval helps, and where it would hurt

Pandit is not a document QA system. It is a **text-to-plan system over a verified SQL
archive**, at 92% on held-out questions. Putting BM25 + reranker in front of stats
questions would swap exact `GROUP BY` answers for "plausible passages", and break L1.

The evaluation logs point to three places where retrieval does fix a measured failure:

| Retrieval target | Failure it fixes | Lesson |
|---|---|---|
| **A. Entities** (players, teams, grounds, aliases) | typos ("kholi", "bumrha"), nicknames, first names, non-IPL people | L5 |
| **B. Exemplars** (solved questions → verified plans) | copied static examples, unstable plans, new wording | L2, L3, L4 |
| **C. Commentary** (ESPN ball-by-ball text) | narrative questions the SQL can't answer: "how did the 2025 final turn", "describe Rashid's spell", dropped catches | new capability |

Stats questions stay on the SQL path. Retrieval makes the plan better (A, B) or adds a
narrative route (C). It never supplies a number.

---

## 2. Architecture (LanceDB, embedded, one folder)

```
question
  │
  ├─ scope check (existing)                      ── refuse
  │
  ├─ QuestionFrame  ← deterministic, typed (pydantic)
  │     entities  ← [A] entity linker (LanceDB FTS + phonetic + popularity prior, can abstain)
  │     years, ranges, filters ← existing parse_filters
  │     intent family: stats | match | narrative
  │
  ├─ stats ───► planner(frame + [B] top-4 masked exemplars)  → tool + metric only (JSON schema)
  │               → coverage guard (frame ⊆ plan) → SQL → grounded answer        [unchanged core]
  │
  ├─ match ───► existing match path
  │
  └─ narrative ► [C] SQL prefilter (match/season/players from frame)
                  → BM25 ∥ dense → RRF → cross-encoder rerank → top 6 overs
                  → answer with (match, over) citations; numbers only from the scorecard facts
```

### A. `entities` table
One row per player / team / ground / alias: `id, kind, canonical, alias_text,
innings (prior), first_season, last_season, teams`.

- Retrieval: FTS on `alias_text` (verify whether LanceDB's native FTS has an n-gram tokenizer
  in the current release; otherwise index character trigrams as a text column) + a Double
  Metaphone key for sound-alike typos. **No dense embeddings here.** Names have no semantics
  for an embedding model to use.
- Scoring: lexical score × log(innings) prior. This is the existing "3× innings dominance"
  rule written as one score.
- **Abstain**: a person-like span with no candidate above the threshold returns
  *"not in the IPL archive"*. That closes the open Smriti Mandhana bug without adding keywords.
- Shared surname with no dominant candidate → clarify (existing behaviour, now one code path).

### B. `exemplars` table
Dev-split questions only (v1-dev, v2, traps, v3-slices, v4-round), each stored with its
**masked** text and its verified plan:

```
"most runs for <TEAM> in <YEAR>"  →  batting_leaders(sort=runs, team=<TEAM>, year=<YEAR>)
```

- Masking prevents L2: the planner can't copy a name it never sees.
- **Three tiers, cheapest first:**
  1. **Exact template hit** (masked text is a key) → reuse the cached plan, **zero LLM tokens**, deterministic.
  2. **Near hit**: LanceDB hybrid (BM25 + bge-small-en-v1.5, RRF) → top 4 masked exemplars as few-shots.
  3. **Miss**: LLM with the generic prompt.

  Every tier still goes through `ground_calls` and `enforce_coverage`, so a cached plan can't skip the checks.
- **Only validated plans enter the table** (from eval gold plans, and later from live answers that passed every check).
- Each row also stores the **compiled SQL** from the slice compiler. Those rows become the
  verified few-shots for the gated text-to-SQL fallback (P4).
- Leakage guard: a build-time test fails if any held-out or benchmark id appears in the table.

### C. `commentary` table
**One chunk = one over** (6 balls, about 60–100 words). Ball-level text is about 10 words, too
short for useful embeddings and too short for BM25's length normalisation.

Metadata columns for prefiltering: `match_id, season, inning, over, phase, batting_team,
bowling_team, batter_ids[], bowler_id, venue, runs, wickets, event_tags[]`.
`event_tags` come from the existing **`ipl_fielding` package** (drops at 93.1% precision, fielder
attribution 95.7%; misfields, overthrows), plus simple rules for reviews, injuries and milestones.
Don't write a new detector.

**Split by data type:** fielding *counts* ("most drops in 2024") are numbers, so they go into DuckDB
as a `fielding_events` table queried by a new `slice_stats` subject. Commentary retrieval only
supplies the **passages** behind them ("show me Kartik's drop of Sooryavanshi").

Retrieval: SQL/metadata prefilter (`prefilter=True`) → BM25 ∥ dense → RRF → cross-encoder
(`ms-marco-MiniLM-L-6-v2` as baseline) → top 6.

**Grounding rule for narrative answers:** numbers may come only from the scorecard facts
sheet, never from commentary. Commentary text contains mid-innings scores ("120 for 3")
that would pass a naive number check and contradict the card.

---

## 3. Better feature extraction (the root fix, more important than the store)

The planner LLM now does two jobs at once: **extracting** entities/years/filters and
**choosing** a tool. Most logged failures are extraction errors (L2, L5, a dropped year, an
invented player). Split the jobs:

1. **`QuestionFrame`**: one typed pydantic object built deterministically. Linked entity ids
   with confidence, years and ranges (2008..now only, never a run count), filters from
   `parse_filters`, scope verdict, intent family.
2. **The LLM only chooses** `tool` + `metric` + `sort`, constrained by a JSON schema with
   enums. It can no longer name a player, so a whole class of errors can't happen.
3. The **coverage guard** becomes a set check: every slot in the frame must appear in the plan.
4. **When plan sources disagree, run every plan and compare the results** (DuckDB is fast; zero tokens).
   Same top row and same numbers → answer. Different results → a clarify card ("Chahal's career
   wickets, or IPL 2024?"). This catches the Chahal → Bumrah class of error at runtime.
   Self-consistency sampling (k=3) is only worth it later, with a free local planner.
5. **Hinglish/typo synonyms** ("chhakke" = sixes, "wicket", "ran") go into the exemplar text and
   the stat-word lexicon. They are not a growing regex.

---

## 3b. Prerequisites inside cricbot (zero tokens, do first or alongside P1)

Retrieval makes plans better, but each item below either protects that work or gets more
out of it. They come from the 23 Oct review, and each is a separate cricbot PR.

| # | Change | Why it comes before (or with) retrieval | Effort |
|---|---|---|---|
| 1 | **Arbiter table**: `prefer_rules()` → named rules; the winning rule's name goes to the trace and eval output; disagreements logged to JSONL | A third plan source (cache/exemplars) on top of untraceable heuristics can't be debugged (L11). The disagreement log is also the best source of new eval questions. | 1 day |
| 2 | **Bind numbers to names** in validation | Closes L10. Narrative answers have more numbers in play, so this must exist before the commentary route. | hours |
| 3 | **Recorded LLM plans + replay in CI** | The full hybrid pipeline runs in CI with zero tokens; any arbitration/cache change shows up as a plan diff | ½ day |
| 4 | Split `stats.py` (1,496 lines) → `planner.py` / `validate.py` / `render.py`; one source for the `ON_SCREEN` regex | The retrieval hooks need a clean place to plug in | ½ day |
| 5 | Slice-spec fields: `subject=fielder` (+ `fielding_events`), `with_player`, `split=era` | Each new **field** combines with every existing one, so coverage grows without new tools | 1–2 days |

---

## 4. Fine-tuning: not yet, and here is the gate

**Don't fine-tune now.**
- The 349 labelled questions are your **measurement instrument**. Training on them removes the
  only way to know whether anything improved.
- Dev-set scores reach 100% with rules. The remaining gaps are coverage and entities, and
  §2 and §3 fix those directly. A fine-tuned planner wouldn't learn a tool that doesn't exist.

**Gate for a fine-tuned local planner:** after Phase 2, fine-tune if *either*
(a) LLM-planner held-out accuracy stays below rules + 5 points, or (b) Groq limits still block a
full eval run (they block it today: one `--answer` run uses about 200k tokens, a whole day's quota).

If the gate trips, here is the recipe:
1. Generate questions from templates using the existing `build_qna` machinery (archive-backed gold).
2. LLM paraphrases per template (×10–20), including typos and Hinglish.
3. **Execution-verify**: run each paraphrase's plan; keep the pair only if the executed answer
   matches gold. This gives thousands of pairs with no hand labelling.
4. LoRA-tune a small model (Qwen3 1.7B–4B class) to output the JSON plan from the frame.
5. **Split by template family, not by row.** Otherwise paraphrases of the same template leak
   between train and test.
6. Benefits: deterministic, offline, free, fast. Cost: one more model to maintain.

**Commentary event classifier** (Phase 4, optional): `audit_sample_annotated.csv` is a
stratified, weighted, hand-labelled sample. Use it as the **evaluation** set for event tags.
Train a small classifier only if the rule tiers plateau, and then label a separate training set.

**Embedding / reranker fine-tuning:** no. Measure off-the-shelf models first. With 6
candidates after prefiltering, a reranker may not add anything (see Phase 3 exit).

---

## 5. Evaluation plan

| Add | Why |
|---|---|
| **Baseline v3-new run on the current app, before any change** | the only clean held-out number (L9) |
| Entity-linking acc@1 and **abstain precision** | A is measurable on its own |
| Exemplar recall@4: does the top 4 contain an exemplar with the gold tool? | B is measurable on its own |
| Narrative gold set, ~40 questions with gold `(match_id, over)` spans, **split dev/held-out on day one** | C needs its own instrument |
| Commentary recall@50 (before rerank), nDCG@5 (after) | shows whether the reranker earns its latency |
| **LLM response cache** (disk, keyed by model + prompt hash) | reruns stop using Groq quota; results become reproducible |
| Record provider + model per question (already partly done) | L4 |
| **Recorded-plan replay in CI** (the cache above, replayed through a mock router) | zero-token regression gate on every PR |
| **Metamorphic tests**: entity swap (only the player slot changes), order ("Bumrah vs Kohli" = "Kohli against Bumrah"), typo injection ("kholi") | hundreds of tests from a few seeds; the right instrument for the entity linker |
| **Sealed held-out set**: generate a fresh one with `build_qna_v2.py`, commit only its hash, score it only before a release | stops held-out sets from becoming dev sets (L3) |
| Arbiter-rule firing counts per eval run | a rule that never fires is dead; a rule that fires on wrong answers is the one to look at |

Report everything per `split` × `capability`, as the gold-set README already says.

---

## 6. App enhancements

**Backend**
- LLM response cache and a local planner fallback (removes the rate-limit failure mode)
- Narrative answers cite `(match, over)`. A click opens the ball-by-ball tab at that over.
- Trace pane shows the linked entities with scores, the retrieved exemplars, and reranked chunks.

**Frontend:** `app/page.tsx` is 775 lines and `globals.css` 508 lines of custom CSS.
1. Look at screenshots first and run a design critique before changing anything. I haven't seen it rendered yet.
2. Split `page.tsx` into components: `ChatPane`, `MatchWorkspace`, `StatsPanel`, `cards/*`.
3. Move colours/spacing/type into Tailwind 4 `@theme` tokens; team colours from `ipl_teams.py`.
4. Use shadcn/ui primitives (Tabs, Dialog, **Command palette** for the `/` search,
   one shared `DataTable`) in place of hand-rolled ones.
5. Numbers: `tabular-nums`, right-aligned, consistent decimal places per stat.
6. Citation chips on narrative answers; one loading/empty/error pattern for every card.

---

## 7. Repo setup and practices

```
cricket-rag/
  pyproject.toml          # uv-managed, Python 3.12
  CLAUDE.md               # hard rules distilled from the evals (see file)
  PLAN.md
  .env.example            # CRICBOT_DB, LANCE_DIR, model names
  .pre-commit-config.yaml # ruff (lint+format), pyright, leakage check
  src/cricrag/
    config.py             # pydantic-settings
    frame.py              # QuestionFrame + builder
    index/  entities.py  exemplars.py  commentary.py   # build LanceDB tables from cricbot.duckdb (read-only)
    retrieve/ hybrid.py  rerank.py  linker.py
    eval/   metrics.py  run.py  narrative_gold.jsonl
    cli.py                # typer: build-index, link, retrieve, eval
  tests/                  # offline; tiny fixture tables; LLMs mocked
  .github/workflows/ci.yml
```

- **Integration path:** `cricrag` opens `cricbot.duckdb` **read-only** and exposes three
  functions (`link_entities`, `retrieve_exemplars`, `retrieve_commentary`). cricbot imports them
  behind `PANDIT_RETRIEVAL=on`. Merge only when the held-out number beats the baseline.
- Tooling: uv, ruff, pyright, pytest + coverage, pre-commit, GitHub Actions.
  VS Code: Ruff, Pylance, Even Better TOML, Jupyter.
- PR-sized changes, one finding → one fix → one test, as the cricbot history already does.

---

## 8. Phases and exit criteria

| Phase | Work | Exit criterion |
|---|---|---|
| **P0** (½ day) | Corpus check (snippet below); **v3-new baseline on the current app** (rules + hybrid); cricrag skeleton, CLAUDE.md, CI, LLM cache | baseline numbers saved |
| **P0b** (cricbot, ~3 days) | §3b items 1–4: arbiter table, number binding, recorded-plan CI, `stats.py` split | 62 stats-agent tests + recorded plans unchanged; L10 swap test passes |
| **P1** (can run alongside P0b) | `entities` table + linker + abstain + metamorphic tests | entity accuracy ≥ current on v2 + v3-new typo/Hinglish rows; Mandhana-type questions abstain; no dev-set regression |
| **P2** | `QuestionFrame`, template cache + exemplar retrieval, schema-constrained planner, run-both-plans | held-out ≥ baseline; same-plan consistency ≥ 18/20 (from 14/20); **LLM tokens per question down** (cache hit rate reported) |
| **P3** | §3b item 5 (`fielding_events` + fielder subject), `commentary` table, narrative route, narrative gold set | recall@50 ≥ 0.9; keep the reranker **only** if nDCG@5 rises by a meaningful amount |
| **P4** (gated) | Gated text-to-SQL for the "not in the archive yet" tail (exemplar SQL as few-shots; sqlglot whitelist, read-only, same grounding gate); then a LoRA planner if the §4 gate trips | fallback logs show repeat shapes → turn them into slice fields |
| **P5** (parallel) | UI refactor, checked with the Playwright desktop/mobile screenshots used before | design critique items closed |

### P0 corpus check (PowerShell, in `cricbot-duckdb` with the venv active)

The fielding copy of the archive had 295,495 balls with text. This checks the cricbot copy
and shows any thin seasons.

```powershell
@'
import duckdb
con = duckdb.connect("data/cricbot.duckdb", read_only=True)
print(con.sql("""
  SELECT m.season,
         count(DISTINCT b.match_id)                        AS matches_with_text,
         count(*)                                          AS balls_with_text,
         round(avg(length(b.commentary)))                  AS avg_chars
  FROM balls b JOIN matches m USING (match_id)
  WHERE b.commentary IS NOT NULL AND length(b.commentary) > 0
  GROUP BY 1 ORDER BY 1"""))
'@ | .venv\Scripts\python.exe -
```

If coverage is thin for some seasons, the narrative route must say so, the same way
"not yet in the archive" works today.

---

## 9. Open questions (they change the plan)

1. **GPU?** Decides `bge-reranker-v2-m3` vs MiniLM, and whether a local LoRA planner is practical.
2. ~~Commentary coverage~~: close to complete in the fielding copy (L12). Just confirm in the cricbot copy.
3. **Package vs merge:** develop `cricrag` separately and import it (recommended), or build
   straight into `cricbot/backend/retrieval/`?
