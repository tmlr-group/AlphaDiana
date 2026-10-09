# Three-task smoke test

This entry runs the public dataset, agent, runner, scorer, artifact and analyzer APIs on real model responses. Start with GPQA; omit `--only gpqa_directllm` to run all five reasoning datasets. Each dataset selects the three content-pinned tasks in `configs/acceptance/api/fixtures.json`. AIME uses four independent samples per task; the other datasets use one. It does not run the full datasets or certify the entire 28-cell native-harness matrix.

## Install and select the provider

Use Python 3.10 or newer, from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[analysis]'
export PYTHONPATH="$PWD"
export SMOKE_ROOT="$(mktemp -d)"
export AD_ACCEPTANCE_RESULTS="$SMOKE_ROOT/results"
export AD_ACCEPTANCE_WORK="$SMOKE_ROOT/runtime"
```

For the HKBU API:

```bash
export PROVIDER=hkbu
export AD_ACCEPTANCE_API_BASE=https://genai.hkbu.edu.hk/api/v1
read -rsp 'API key: ' AD_ACCEPTANCE_API_KEY; echo
export AD_ACCEPTANCE_API_KEY
```

For a running local Qwen endpoint, set `PROVIDER=qwen`, set `AD_ACCEPTANCE_API_BASE` to its `/v1` URL and enter that service's key instead. The tested server uses Qwen/Qwen3.5-27B, BF16 without quantization, tensor parallelism 2, a 262144-token context, and automatic KV-cache dtype. No model weights are downloaded by these scripts.

## Run and analyze

```bash
python scripts/prepare_smoke_matrix.py --provider "$PROVIDER" --output "$SMOKE_ROOT/configs"
python scripts/validate_api_matrix.py "$SMOKE_ROOT/configs/manifest.json" \
  --only gpqa_directllm --output "$SMOKE_ROOT/receipts"
python scripts/analyze_smoke.py "$AD_ACCEPTANCE_RESULTS/smoke_${PROVIDER}_gpqa_directllm.jsonl" \
  --provider "$PROVIDER" --judge --output "$SMOKE_ROOT/analysis"
```

`--judge` makes additional model calls for LLM-selected action boundaries/labels and evidence-backed failure labels. Omit it for deterministic execution, resource and entropy statistics only. The historical character and keyword splitters remain available in the package but are not used here. The annotation model does not receive scoring ground truth as task input. Provider-hidden reasoning is not reconstructed.

The first call to a dataset may download its files and require Hugging Face access. On an existing offline installation, set `HF_DATASETS_CACHE` to the prepared cache and set `HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1`. The loader must reproduce the pinned input hashes; do not change the pins to bypass a mismatch.

## Settings and evidence

| Setting | Local Qwen | HKBU gpt-6-luna |
| --- | --- | --- |
| Tasks / independent samples | Three per dataset; AIME k=4, others k=1 | Same |
| Output limit | `max_tokens=131072` | `max_completion_tokens=131072` |
| Temperature / top-p | AIME 0.6, others 0; top-p 0.95 | Provider defaults; these controls are omitted |
| Presence penalty | 1.5 | API rejects this field |
| Thinking / context / precision | Thinking enabled; 262144; BF16 | Provider-controlled; no equivalence claimed |
| Logprobs / entropy | Top 20 captured when supplied | API rejects logprobs; entropy is unavailable |
| Concurrency / timeout | Four task slots; 10800 seconds per request | Same |

Read `receipts/receipts.jsonl` for source revision, input/config hashes, task/sample coverage and validation errors. Results retain answers, scores, normalized traces, usage and artifact paths. Analysis exports `report.json`, figure data, PDF, SVG and PNG where observations are available. Missing measurements are recorded as missing. Incorrect but valid answers still count as executed tasks; provider/runtime failures do not count as successful acceptance.

Always use a fresh output directory. Retain failed attempts separately and state why a rerun was necessary; never retry only incorrect answers to improve reported scores. Concurrent completion order need not match input order, but every pinned `(task_id, sample_index)` must appear exactly once.

## Remaining matrix coverage

The full `configs/acceptance/api` matrix also covers OpenClaw, OpenCode and ZeroClaw, plus SWE-Agent and Terminus2 on their corresponding benchmarks. Those require the native runtimes and pinned container/task assets documented there. On the inspected A100-1 account, Docker access is unavailable; A800 was unreachable. SWE/Terminal-Bench are not certified by the five-dataset entry above. Native provider configuration needs the same explicit compatibility check before expanding the smoke run.
