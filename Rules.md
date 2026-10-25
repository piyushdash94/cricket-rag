# CLAUDE.md: cricket-rag

Retrieval layer for Pandit (cricbot). These rules come from measured eval failures in
`cricbot-duckdb/backend/eval/README.md`. Follow them. If one seems wrong, raise it rather than
working around it.

## Hard rules

1. **Numbers come from SQL only.** Retrieved text (commentary, exemplars) never supplies a
   number, a winner, or a ranking. Narrative answers use numbers from the scorecard facts sheet only.
2. **Never tune on held-out data.** Protected, read-only for scoring:
   `qna_ipl_heldout.jsonl`, `qna_ipl_v2_heldout.jsonl`, `benchmark/v3_new_questions/*`,
   and every `split: heldout|benchmark` row. Any future exemplar index must contain dev rows only.
3. **No concrete names in prompts or exemplars.** Mask them as `<PLAYER>`, `<TEAM>`, `<YEAR>`,
   `<VENUE>`. (A prompt example "Jasprit Bumrah" was copied into an unrelated answer.)
4. **The LLM chooses; it does not extract.** Entities, years and filters come from the
   deterministic `QuestionFrame`. The planner returns tool/metric/sort via a JSON schema with enums.
5. **Answer the asked question, or name the gap.** Never substitute a nearby question.
   Every frame slot must appear in the executed plan (coverage guard).
6. **Scope before planning.** Out-of-scope requests and unknown people (the linker abstains) get a
   clear reply and run no IPL query.
7. **Ambiguity → ask.** A shared surname with no dominant candidate gets a clarifying question
   with options and no stats card.
8. **Report per `split` × `capability`.** Never quote one blended accuracy number.
9. **Every arbitration is named.** When LLM, rules, cache or exemplar plans disagree, the
   winning rule's name goes into the trace and the eval output. No anonymous `if` branches.
10. **Only validated plans enter the plan cache or exemplar index.** Every cache hit still
    passes `ground_calls` and `enforce_coverage`.
11. **Numbers are bound to entities.** A number in a sentence that names one player must come
    from that player's row, not just from somewhere in the tables.
12. **Counts live in SQL, passages live in LanceDB.** For example, fielding drop counts come from
    `fielding_events`; commentary retrieval only cites the passages behind them.

13. **Parity first.** Ported behaviour must match cricbot's recorded plans/answers on the dev sets.
    Any intended difference is listed in the PR.
14. **Fielding rule changes pass RULES.md §12**: no empty-matching pattern; confirmed drops kept
    (190/191, 48/49); gold-negative false-drop rate ≤ 0.52%; a person reads a sample of what the change adds.
15. **Claude-labelled rows are not gold.** Only human-verified labels enter metrics.
16. **CPU only.** No dependency that needs a GPU to be usable. Use ONNX/fastembed for embeddings and reranking.
17. **ESPN commentary stays local.** Show short cited snippets only; never commit or publish the corpus.

## Data access

- `cricbot.duckdb` is opened **read-only** (`read_only=True`). DuckDB allows one writer.
- LanceDB lives in `LANCE_DIR` (gitignored) and can always be rebuilt with `cricrag build-index`.
- Commentary chunk = one over. Ball-level chunks are too short.

## Code

- Python 3.12, uv, ruff (lint + format), pyright, pytest. Run `uv run pre-commit run -a` before committing.
- Tests are offline: fixture tables, mocked LLMs, no network, no real keys.
- Cache LLM calls on disk (model + prompt hash). Record provider and model per eval row.
- Small changes: one finding → one fix → one test. Put the eval delta in the PR description.

## Commands

```bash
uv sync
uv run pytest
uv run cricrag build-index && uv run cricrag build-events
uv run cricrag ask "Where did the 2025 final turn?" --trace
uv run cricrag eval-pandit --suite v3|events
uv run cricrag eval-routes --mode plan|full
uv run cricrag eval-retrieval
```
