# Real-world prompt A/B — 10k reasoning budget

## Result

| Model | Incident | Concurrency | Migration | Quantum | Total | Mean generation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| MiMo-V2.5 UD-Q3_K_M | 6 | **11** | **7** | **12** | **36/48** | **35.72 tok/s** |
| MiniMax M2.7 UD-Q4_K_M | **9** | 10 | 4 | 11 | **34/48** | 28.86 tok/s |
| Qwen3-235B-A22B Q4_K_M | 5 | 10 | **7** | 10 | **32/48** | 18.53 tok/s |

MiMo is the best overall choice of the three: it won the blind total and was 24%
faster than MiniMax and 93% faster than Qwen in mean generation throughput.
MiniMax remains the strongest incident-analysis specialist. Qwen is the clear
loser under this configuration: it is slowest and its incident answer still hit
the 16,384-token limit without finishing.

The lead is not a blanket reliability result. Every model made serious technical
errors. In particular, none produced a PostgreSQL migration that should be run
without expert correction. MiniMax's polished migration was the most dangerous:
it wrapped `CREATE INDEX CONCURRENTLY` in a transaction, used a non-resumable and
incorrect watermark, attempted to validate a trigger as a constraint, and would
break old writers.

## Effect of increasing reasoning from 4k to 10k

On the three tasks shared with the first run, the blind totals changed only:

| Model | 4k reasoning | 10k reasoning | Change |
| --- | ---: | ---: | ---: |
| Qwen | 21/36 | 22/36 | +1 |
| MiniMax | 23/36 | 23/36 | 0 |
| MiMo | 24/36 | 24/36 | 0 |

The larger budget improved completion, not correctness. MiniMax and MiMo went from
one natural finish in three tasks to four in four. Qwen improved from one natural
finish in three to three in four, but still exhausted the enlarged output limit
on the incident task. Mean throughput declined slightly on the longer runs:
Qwen 19.20→18.53 tok/s, MiniMax 30.58→28.86, and MiMo 36.26→35.72.

Longer reasoning also amplified false specificity. The migration answers became
more detailed without becoming safer. A 10k ceiling is useful as headroom for hard
tasks, but it should not be treated as a quality setting; per-task budgets and
executable verification matter more.

## Artifacts

- Runner: [`scripts/agentic_prompt_ab.py`](../../../scripts/agentic_prompt_ab.py)
- Separate API reasoning fields are excluded; reasoning exposed as a delivered answer is retained.
- Blind scorecard: `review/scorecard-blind.md`

The blind map was opened only after the scorecard had been written and copied into
the run directory.
