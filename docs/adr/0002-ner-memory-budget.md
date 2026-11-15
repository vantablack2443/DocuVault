# ADR 0002: Keeping spaCy NER inside the memory budget

Status: accepted (2026-11-15)

## Context

Names and places have no fixed format, so the redaction engine uses the spaCy
pipeline `de_core_news_sm`. Measured in the API container before any tuning:

| | Resident memory |
| --- | ---: |
| API without spaCy | 90 MiB |
| API with spaCy, idle | 294 MiB |
| After 8 concurrent 18,000-character requests | 678 MiB |

The budget is 400 MB (Render free tier kills the container at 512 MB).

## Decision

1. **Prune the string store at load.** The pipeline ships about 460,000
   training-vocabulary strings; NER reads none of them because its embeddings
   hash token attributes. Only strings behind cached lexemes and the entity
   labels are kept. Saves about 90 MB.
2. **One inference at a time** (a lock in `NerDetector`). Concurrent calls
   multiplied peak memory; requests now queue in the thread pool instead.
3. **Fixed-size chunks** (4,000 characters, cut at line, sentence or word
   boundaries), so peak memory does not depend on prompt size.
4. **`nlp.memory_zone()` per chunk**, so strings and lexemes created for a
   request are freed. Without it the vocabulary grows with every new word.
5. Only the `ner` component is loaded; `MALLOC_ARENA_MAX=2` limits allocator
   fragmentation across threads.

Result: 188 MiB idle, 202 MiB after 24 large requests at concurrency 8.

## Consequences

- Step 1 calls a private spaCy method (`StringStore._reset_and_load`). spaCy is
  pinned to 3.8.x. If the method disappears the code logs a warning and runs
  unpruned; if pruning ever changed model output, the benchmark gate in CI
  would fail. Scores were identical before and after pruning.
- NER throughput is one request at a time (about 0.25 s for 18,000 characters).
  Acceptable for a demo on a shared CPU; a real deployment would run several
  worker processes.
- An entity that straddles a chunk boundary can be missed. Cuts prefer line
  and sentence ends to make that unlikely.
- Tests assert the pruned size and that the vocabulary does not grow.
