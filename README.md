# Mac Studio local LLM benchmarks

Local inference measurements from an Apple M2 Ultra Mac Studio with 192 GB
unified memory. Tests cover long-context prefill/decode throughput and blinded
real-world prompt quality.

## Results

### Real-world prompts, 10K reasoning budget

Four tasks: payment incident response, asyncio concurrency repair, zero-downtime
PostgreSQL migration, and operational quantum-mechanics explanation. Each task
has six criteria scored 0–2.

| Model | Quant | Score | Mean generation | Natural finishes |
|---|---|---:|---:|---:|
| DeepSeek-V4-Flash-0731 | UD-Q8_K_XL | **42/48** | 6.58 tok/s | 4/4 |
| MiMo-V2.5 | UD-Q3_K_M | 36/48 | **35.72 tok/s** | 4/4 |
| MiniMax M2.7 | UD-Q4_K_M | 34/48 | 28.86 tok/s | 4/4 |
| Qwen3-235B-A22B | Q4_K_M | 32/48 | 18.53 tok/s | 3/4 |

DeepSeek won prompt quality. MiMo remained best for interactive latency.
DeepSeek's generated asyncio patch passed all six supplied tests.

Full prompts, rubrics, delivered answers, and scorecards live under
[`results/agentic-10k`](results/agentic-10k/). Separate API reasoning fields are
excluded; reasoning that a model exposed as its delivered answer is retained.

### 50K prefill and decode

| Model | Quant | Prefill | Decode at 50K |
|---|---|---:|---:|
| MiMo-V2.5 | UD-Q3_K_M | **293.35 tok/s** | **23.18 tok/s** |
| DeepSeek-V4-Flash-0731 | UD-Q8_K_XL | 170.04 tok/s | 3.16 tok/s |
| MiniMax M2.7 | UD-Q4_K_M | 119.73 tok/s | 8.77 tok/s |
| Qwen3-235B-A22B | Q4_K_M | 59.39 tok/s | 5.11 tok/s |

DeepSeek prefill is strong, but its 50K decode is slowest in this set.

Cross-model caveat: Qwen, MiniMax, and MiMo used Ollama's llama-server build
`a731805ce` with a deterministic text prompt. DeepSeek required upstream
llama.cpp commit `74ce157` and was measured with native `llama-bench` synthetic
tokens. Results are directional until every model is rerun with one binary and
harness.

Machine-readable summaries live under
[`results/prefix-decode`](results/prefix-decode/).

## Reproduce

Agentic runner:

```bash
export LLAMA_SERVER=/path/to/llama-server
export DEEPSEEK_V4_MODEL=/path/to/DeepSeek-V4-Flash-0731-UD-Q8_K_XL-00001-of-00005.gguf

uv run --script scripts/agentic_prompt_ab.py \
  --models deepseek-v4-flash \
  --tasks incident,concurrency,migration,quantum \
  --ctx-size 32768 \
  --max-tokens 16384 \
  --reasoning-budget 10000
```

DeepSeek prefill:

```bash
llama-bench -m "$DEEPSEEK_V4_MODEL" \
  -p 50000 -n 0 -r 1 --no-warmup \
  -b 2048 -ub 2048 -ctk q8_0 -ctv q8_0 -ngl 999 -fa on
```

DeepSeek decode at 50K depth:

```bash
llama-bench -m "$DEEPSEEK_V4_MODEL" \
  -p 0 -n 64 -d 50000 -r 3 --no-warmup \
  -b 2048 -ub 2048 -ctk q8_0 -ctv q8_0 -ngl 999 -fa on
```
