# Three-task smoke test

This runbook covers the tests used in PR #112: CPU regression tests, real DirectLLM execution on five reasoning datasets with either local Qwen or HKBU, reopening saved results, statistics/figure export, and LLM action/failure analysis on GPQA and HLE. It does not certify the 28-cell native-harness matrix or reproduce the earlier full-AIME campaign. See the [observed checkpoint](smoke-20261009-results.md) for completed versus pending runs; runnable commands are not evidence that every path has passed.

Use Linux with Bash. Run the commands from the repository root. Python 3.12 was tested; the package supports 3.10+. You need GitHub access to this repository, Hugging Face access or a prepared dataset cache, and your own provider credentials. The API arm needs no GPU or container engine. The Qwen arm needs an existing compatible endpoint, matching the model-service reference below.

## 1. Select the code revision

Prepare your own environment with the package's `analysis` extra and `pytest` installed. Environment installation, provider access and GPU allocation are managed by the person running the tests. The examples assume an activated environment and Bash; they do not set up the machine.

Use a new checkout so existing work is untouched. Pin the tested code revision below, and do not pull while validation is running. Later runbook edits do not change that code.

```bash
git clone --branch refactor/shared-main-contracts --single-branch \
  https://github.com/tmlr-group/AlphaDiana.git AlphaDiana-smoke
cd AlphaDiana-smoke
git switch --detach 90ec6de6a98ce35d030418f6126d69aadc8dff9b
export PYTHONPATH="$PWD"
export MPLBACKEND=Agg
```

## 2. Run the CPU regression tests

These use fixtures; they do not consume provider credits or start the real model server.

```bash
python -m pytest tests -q -rs
```

The tested code revision `90ec6de6a98ce35d030418f6126d69aadc8dff9b` produced **393 passed, 15 skipped** on Linux/Python 3.12. Inspect skip reasons, rather than treating skipped native-runtime tests as passed. The suite includes the scripted HTTP API pipeline, saved-artifact reopening, independent rescoring, action/failure annotation contracts and PDF/SVG/PNG export. It does not replace real model execution.

## 3. Choose one provider per terminal

To run both providers in parallel, open two terminals, activate the same checkout's environment in each, and run this section through section 5 independently. Choose `hkbu` in one and `qwen` in the other. Each must create its own output directory. Keep long runs inside your usual persistent terminal session, such as tmux.

Choose a persistent directory on a disk with enough space. The following defaults to your home directory; set `SMOKE_BASE` to your own data-disk directory if available. Do not put generated outputs inside the Git checkout.

```bash
export SMOKE_BASE="${SMOKE_BASE:-$HOME/alphadiana-validation}"
mkdir -p "$SMOKE_BASE"
export SMOKE_ROOT="$(mktemp -d "$SMOKE_BASE/run-XXXXXXXX")"
export AD_ACCEPTANCE_RESULTS="$SMOKE_ROOT/results"
export AD_ACCEPTANCE_WORK="$SMOKE_ROOT/runtime"
export MPLCONFIGDIR="$SMOKE_ROOT/matplotlib"
git rev-parse HEAD > "$SMOKE_ROOT/source-commit.txt"
python -m pip freeze > "$SMOKE_ROOT/client-requirements.txt"
printf 'Retain this run directory: %s\n' "$SMOKE_ROOT"
```

Choose **one** of these provider blocks. API keys are entered privately and must not be committed or pasted into shared logs.

HKBU:

```bash
export PROVIDER=hkbu
export AD_ACCEPTANCE_API_BASE=https://genai.hkbu.edu.hk/api/v1
read -rsp 'HKBU API key: ' AD_ACCEPTANCE_API_KEY; echo
export AD_ACCEPTANCE_API_KEY
```

Local Qwen, from the machine where the service listens (change the URL for your own reachable endpoint or SSH tunnel):

```bash
export PROVIDER=qwen
export AD_ACCEPTANCE_API_BASE=http://127.0.0.1:19975/v1
read -rsp 'Local service API key: ' AD_ACCEPTANCE_API_KEY; echo
export AD_ACCEPTANCE_API_KEY
```

`localhost` on your laptop is not the GPU server. Request the endpoint/key from its owner, or use an SSH tunnel to that server. The expected served model name is `Qwen/Qwen3.5-27B`; HKBU uses `gpt-6-luna`. Do not substitute the new A800's differently named cached model.

The first dataset load may download files. For an existing offline installation, set `HF_DATASETS_CACHE` to the prepared cache and set `HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1`. Do not enable offline mode on an empty cache. The loader must reproduce the pinned task sequence and input hashes in `configs/acceptance/api/fixtures.json`; do not change the pins to bypass a mismatch.

## 4. Run all five datasets, or a first GPQA/HLE check

Generate the provider-specific settings once:

```bash
python scripts/validation/prepare_smoke_matrix.py --provider "$PROVIDER" --output "$SMOKE_ROOT/configs"
```

For all five datasets:

```bash
python scripts/validation/validate_api_matrix.py "$SMOKE_ROOT/configs/manifest.json" \
  --output "$SMOKE_ROOT/receipts"
```

For a shorter first check, use the following command **instead of** the all-five command:

```bash
python scripts/validation/validate_api_matrix.py "$SMOKE_ROOT/configs/manifest.json" \
  --only gpqa_directllm hle_directllm --output "$SMOKE_ROOT/receipts"
```

The five IDs are `aime2026_directllm`, `gpqa_directllm`, `hle_directllm`, `imo_answerbench_directllm` and `mmmu_pro_directllm`. Each selects three pinned tasks. AIME uses four independent samples per task, so one full provider arm produces **24 scored records across 15 tasks**; the two-cell first check produces six. Two full provider arms produce 48 records. This is functional acceptance, not full benchmark evaluation.

A nonzero exit means acceptance failed. Read `receipts/receipts.jsonl`: all five expected IDs (or both selected IDs) must have `status: passed`, with 12 valid records for AIME and three for each other dataset. Missing cells, empty receipts and failed records do not count as completion. The receipt includes source revision, input/config hashes, task/sample coverage and failure details.

Always use a fresh run directory for a rerun and retain the failed attempt separately. Do not rerun the first two cells into the same directory when expanding to all five. Do not retry only incorrect answers to improve scores. Concurrent completion order may differ, but each pinned `(task_id, sample_index)` must occur exactly once.

## 5. Reopen results and export analysis

Once generation finishes, export statistics for every saved dataset without further model calls:

```bash
python scripts/validation/analyze_smoke.py "$AD_ACCEPTANCE_RESULTS"/smoke_"${PROVIDER}"_*.jsonl \
  --provider "$PROVIDER" --output "$SMOKE_ROOT/analysis-statistics"
```

To exercise the complete LLM analysis path on the six GPQA/HLE traces:

```bash
python scripts/validation/analyze_smoke.py \
  "$AD_ACCEPTANCE_RESULTS/smoke_${PROVIDER}_gpqa_directllm.jsonl" \
  "$AD_ACCEPTANCE_RESULTS/smoke_${PROVIDER}_hle_directllm.jsonl" \
  --provider "$PROVIDER" --judge --output "$SMOKE_ROOT/analysis-with-llm"
```

`--judge` sends the saved task/trace evidence to the selected provider and consumes additional model requests. GPQA/HLE analysis was actually verified with HKBU; the Qwen judge configuration is available but is not yet a verified live result. The default is `llm-trajectory-actions-v1`: the LLM selects action boundaries and labels using the main protocol's category rules. Historical character/keyword splitters remain opt-in methods and are not used here. Failure labels require quotes from visible evidence; hidden provider reasoning is not reconstructed. Annotation is a separate call from solving: Qwen annotation disables thinking and uses temperature 0; HKBU omits temperature. Action output budget is 16384 tokens and failure annotation uses 2048, with a 180-second request timeout and no automatic retries.

Check `report.json` and `export.json`, not just whether the command printed a directory. The report's `analyses` entries retain each measurement's coverage and missing-data details. For the judge run, inspect `annotated_actions` and `failure_annotations`; six successfully annotated traces with no missing traces establish action coverage. Failure coverage depends on which runs are unsuccessful, not a fixed required number of failure labels. PDF/SVG/PNG and their plotted data are emitted where observations support a figure; an inapplicable entropy plot is not a failed execution.

Keep the **entire** run directory, including results, referenced trace/usage sidecars, receipts, annotation caches and reports. A copied JSONL alone may lose the evidence required for analysis. To reanalyze later, restore `SMOKE_ROOT`, `AD_ACCEPTANCE_RESULTS` and `PROVIDER`; credentials are required only when making new judge calls. Use a fresh export directory. This reruns analysis, not generation.

## Generation settings

| Setting | Local Qwen | HKBU gpt-6-luna |
| --- | --- | --- |
| Tasks / independent samples | Three per dataset; AIME k=4, others k=1 | Same |
| Output limit | `max_tokens=131072` | `max_completion_tokens=131072` |
| Temperature / top-p | AIME 0.6, others 0; top-p 0.95 | Provider defaults; these controls omitted |
| Presence / repetition penalty | 1.5 / 1.0 | Omitted; presence penalty rejected |
| Top-k / min-p | -1 / 0 | Omitted |
| Thinking / context / precision | Enabled; 262144; BF16 without quantization | Provider-controlled; no equivalence claimed |
| Logprobs / entropy | Top 20 captured when supplied | API rejects logprobs; entropy unavailable |
| Client concurrency / timeout | Four task slots; 10800 seconds per request and stream | Same |
| Task retries | 0 | 0 |

Incorrect but valid answers count as executed tasks; provider/runtime failures do not count as successful acceptance. MMMU currently has a known answer-format limitation: exact-match scoring can reject a semantically matching choice inside `\text{...}`. See the checkpoint; do not silently normalize answers or claim its raw score establishes model accuracy.

## Local model service reference

Provide your own running Qwen3.5-27B endpoint with image support for MMMU. The tested inference environment was vLLM 0.23.0, PyTorch 2.11.0 and Transformers 5.12.1; the test client is separate. The observed server settings were BF16, no quantization, TP=2, automatic KV-cache dtype, context 262144, `max_num_seqs=8`, `max_num_batched_tokens=4096`, GPU memory utilization 0.90, `qwen3_coder` tool parser, `qwen3` reasoning parser, max logprobs 20, vLLM generation defaults with presence penalty 1.5, and eager execution. These are settings to match, not a machine installation procedure.

The inspected deployment used two A100 40 GB cards. Record your own server configuration, package versions and weight source/revision alongside the client receipts. The current deployment does not provide a verified weight revision hash, so bit-identical weight provenance is not claimed. Do not silently substitute a different model, quantization, context or output limit.

## Native harnesses and container benchmarks

The [28-cell matrix](../../configs/acceptance/api/README.md) additionally covers OpenClaw, OpenCode and ZeroClaw, plus SWE-Agent and Terminus2 on their corresponding benchmarks. It requires pinned native executables/images and SWE/TB2 task assets. Its checked-in defaults are the **older 8192-token, thinking-disabled, k=1 protocol**, not this runbook's settings. Do not use that manifest unchanged to claim a reproduction of this campaign.

This campaign has not completed those native cells. A100-1 lacks Docker access. The old A800 was unreachable; the new A800's rootless Podman setup is blocked on administrator installation of `uidmap`, and SWE/TB2 compatibility through Podman's Docker API has not been validated. Installing Podman alone is not evidence that those tests work. The five-dataset entry above therefore covers all currently documented DirectLLM test procedures, not all seven benchmarks and four harnesses.
