# DeepSeek-V4-Flash-0731 Q8 real-world prompt scorecard

Same four tasks, 10,000-token reasoning budget, 16,384-token output limit,
32,768-token context, temperature 0, and six 0–2 rubric criteria per task.

## Scores

| Task | Score | Main strengths | Main defects |
|---|---:|---|---|
| Duplicate-charge incident | **10/12** | Separates every failure mode; transactional outbox; conditional lease; provider/log reconciliation; rejects injected customer note. | Five-minute containment reopens unsafe submit path; `job_id` provider key does not deduplicate two raced jobs; duplicate-marking SQL joins contradictory `rn` values and updates nothing. |
| Async shared-task repair | **12/12** | Correct `shield`, exact-task cleanup, success-only cache, retry behavior, exception retrieval, one-loop race reasoning, and focused tests. | No rubric defect. Extracted generated code: **6/6 pytest tests passed**. |
| PostgreSQL migration | **8/12** | Compatible dual-read path; correct concurrent index and health gate; strong operational gates and rollback. | Deviates from nullable-first pattern; `SKIP LOCKED` plus forward watermark can miss rows permanently; progress SQL is incomplete; validated check is added only after column was already made NOT NULL. |
| Quantum mechanics | **12/12** | Consistent Mach–Zehnder derivation; correct decoherence, Bell/no-signalling, delayed-choice, predictions, and caveats. | No material rubric defect. |
| **Total** | **42/48** | Best technical score in test set. | Still needs expert review for production SQL. |

## Comparison

| Model | Score | Mean generation | Natural finishes | Approximate four-task wall time |
|---|---:|---:|---:|---:|
| DeepSeek-V4-Flash-0731 UD-Q8_K_XL | **42/48** | 6.58 tok/s | 4/4 | 112.9 min |
| MiMo-V2.5 UD-Q3_K_M | 36/48 | **35.72 tok/s** | 4/4 | 21.0 min |
| MiniMax M2.7 UD-Q4_K_M | 34/48 | 28.86 tok/s | 4/4 | 25.6 min |
| Qwen3-235B-A22B Q4_K_M | 32/48 | 18.53 tok/s | 3/4 | 36.2 min |

DeepSeek is the clear prompt-quality winner, six points above MiMo. It is also
5.4 times slower than MiMo and 4.4 times slower than MiniMax. Use it for hard,
latency-insensitive work; keep MiMo as the interactive default.

## Runtime evidence

- Q8 model loaded with current upstream llama.cpp commit `74ce157`; Ollama's
  bundled build `a731805ce` failed with `unknown model architecture: 'deepseek4'`.
- Server RSS during the 32K run: 158,504,000 KiB, approximately 151.2 GiB.
- macOS reported 17% system memory free while inference was active.
- All four responses finished naturally; no output-limit truncation.
- Published artifacts exclude separate API reasoning fields.
