# AlphaDiana Benchmark Data Inventory

Last updated: 2026-05-07

## Legend

- LP = logprobs available (per-token entropy)
- ART = artifacts available (trajectory text)
- TSK = task JSONs available (scoring data)
- acc = pass@1 accuracy (or sample_accuracy for AIME k>1)
- HF = synced from HuggingFace

---

## GPQA-Diamond (198 tasks)

| Model | Harness | N | Acc | LP | Location |
|-------|---------|---|-----|----|----------|
| Qwen3.5-27B | DirectLLM | 198 | 79.8% (phase9) / 81.3% (HF) | YES | `alphadiana_results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs` |
| Qwen3.5-27B | DirectLLM | 198 | 81.3% | YES | `hf-alphadiana-benchmark-results/full_run/20260423-gpqa-diamond-directllm-qwen35-27b-v1` |
| Qwen3.5-27B | OpenClaw | 198 | 66.2% | YES | `results/full_gpqa_v2_openclaw_qwen35_27b_logprobs` |
| Qwen3.5-27B | OpenCode | 198 | 73.6% | YES | `results/full_gpqa_v2_opencode_qwen35_27b_logprobs` |
| Qwen3.5-27B | ZeroClaw | 198 | 80.6% | YES | `results/full_gpqa_v2_zeroclaw_qwen35_27b_logprobs` |
| Gemma4-31B | DirectLLM | 198 | 83.3% (165/198) | YES | `results/422_full/results/full_gpqa_directllm_gemma4_31b_logprobs` |
| Gemma4-31B | OpenClaw | 198 | 85.4% (169/198; 86.2% excl 2 null) | YES | `results/422_full/results/full_gpqa_openclaw_gemma4_31b_logprobs` |
| Gemma4-31B | OpenCode | 198 | 87.9% (174/198) | YES | `results/422_full/results/full_gpqa_opencode_gemma4_31b_logprobs` |
| Gemma4-31B | ZeroClaw | 198 | 86.4% (171/198) | YES | `results/422_full/results/full_gpqa_zeroclaw_gemma4_31b_logprobs` |

**Note**: Gemma agent harnesses (85-88%) outperform Gemma DirectLLM (83%) on GPQA — the opposite of Qwen.
**⚠️ Also on disk**: Lower-accuracy `gemma4_31b_it` (no-reasoning) runs at `hf-alphadiana-benchmark-results/full_run/full_20260430_gemma4_31b_it_gpqa_diamond_*_or_no_reasoning_v1/` (DirectLLM 72.7%, OpenCode 75.8%). These are different runs — do not use for analysis.

---

## HLE (591 tasks for agents; DirectLLM has 323-task phase9 and 591-task full versions)

| Model | Harness | N | Acc | LP | Location |
|-------|---------|---|-----|----|----------|
| Qwen3.5-27B | DirectLLM | 323 | 23.5% (shared subset) | YES | `alphadiana_results/phase9_directllm_qwen35_27b_hle_logprobs` |
| Qwen3.5-27B | DirectLLM | 591 | 19.3% (full set) | no | `results/hf-alphadiana-benchmark-results/full_run/full_20260422_qwen35_27b_hle_mc_directllm_v1/results` |
| Qwen3.5-27B | OpenClaw | 591 | 13.4% | YES | `results/quick_260430_hle_openclaw_qwen35_27b_merged` |
| Qwen3.5-27B | OpenCode | 591 | 13.9% | YES | `${ALPHADIANA_RESULTS_DIR}/20260426-hle-opencode-qwen35_27b-v01` |
| Qwen3.5-27B | ZeroClaw | 591 | 14.9% | YES (544 lp) | `${ALPHADIANA_RESULTS_DIR}/20260426-hle-zeroclaw-qwen35_27b-v01` |
| Gemma4-31B | DirectLLM | 591 | 27.9% (165/591) | YES | `results/422_full/results/full_hle_directllm_gemma4_31b_logprobs` |
| Gemma4-31B | OpenClaw | 591 | 24.2% (143/591) | YES | `results/422_full/results/full_hle_openclaw_gemma4_31b_logprobs` |
| Gemma4-31B | OpenCode | 591 | 24.0% (142/591) | YES | `results/422_full/results/full_hle_opencode_gemma4_31b_logprobs` |
| Gemma4-31B | ZeroClaw | 591 | 29.1% (172/591) | YES (587 lp, 4 missing) | `results/422_full/results/full_hle_zeroclaw_gemma4_31b_logprobs` |

**HLE Gemma insight**: ZeroClaw (29.1%) > DirectLLM (27.9%) > OpenClaw (24.2%) ≈ OpenCode (24.0%). Unlike GPQA where agents beat DirectLLM, on HLE only ZeroClaw does; tool-based agents underperform.

**Qwen HLE note**: The 23.5% (76/323) number used in the macro analysis is from the phase9 323-task subset
where DirectLLM was evaluated. The full 591-task HLE set gives DirectLLM 19.3% (114/591).
Agent harnesses were evaluated on the full 591 tasks (13-15%).
**The correct baseline for agent comparison on the full 591-task set is 19.3%, not 23.5%.**
The paper's paired analysis correctly uses the 323-task shared subset with 23.5% as the reference.

---

## AIME 2026 (30 tasks, pass@k with k=3 or k=4)

| Model | Harness | N | Pass@1 | Pass@k | LP | Location |
|-------|---------|---|--------|--------|----|----------|
| Qwen3.5-27B | DirectLLM | 30 | 90.8% (109/120) | pass@4=93.3% (28/30) | YES | `${ALPHADIANA_RESULTS_DIR}/full_20260423_qwen35_27b_aime2026_directllm_r1_pass4` |
| Qwen3.5-27B | OpenClaw | 30 | 64.2% (77/120; 28 null) | pass@3=83.3% (25/30) | ⚠️ 62 lp | `${ALPHADIANA_RESULTS_DIR}/repair_20260502_aime2026_openclaw_qwen35_27b_pass4_t9300_from_20260428` |
| Qwen3.5-27B | OpenCode | 30 | 66.7% (80/120; 5 null) | pass@3=86.7% (26/30) | ⚠️ 5 lp | `${ALPHADIANA_RESULTS_DIR}/repair_20260502_aime2026_opencode_qwen35_27b_pass4_t9300_from_20260425` |
| Qwen3.5-27B | ZeroClaw | 30 | 69.2% (83/120) | pass@4=86.7% (26/30) | ⚠️ 16 lp | `${ALPHADIANA_RESULTS_DIR}/repair_20260502_aime2026_zeroclaw_qwen35_27b_pass4_t9300_from_20260428` |
| Gemma4-31B | DirectLLM | 30 | 92.5% (111/120) | pass@4=96.7% (29/30) | YES | `${ALPHADIANA_RESULTS_DIR}/full_aime2026_directllm_gemma4_31b_k4_logprobs` |
| Gemma4-31B | OpenClaw | 30 | 95.0% (114/120) | pass@3=**100%** (30/30) | YES | `${ALPHADIANA_RESULTS_DIR}/quick_260503_aime2026_openclaw_gemma4_31b_8012_pass4_c1` |
| Gemma4-31B | OpenCode | 30 | 96.7% (116/120) | pass@3=**100%** (30/30) | YES | `${ALPHADIANA_RESULTS_DIR}/full_20260503_aime2026_opencode_gemma4_31b_8012_pass4_c4` |
| Gemma4-31B | ZeroClaw | 30 | 96.7% (116/120) | pass@4=**100%** (30/30) | YES | `${ALPHADIANA_RESULTS_DIR}/full_20260503_aime2026_zeroclaw_gemma4_31b_8011_pass4_c4` |

**AIME Gemma pass@1 notes**: OpenClaw has 3 null-correct samples (97.4% excl null); OpenCode has 1 null-correct sample (97.5% excl null). pass@1 numbers above are null-inclusive (confirmed 2026-05-07).

**Note**: Qwen AIME agent harnesses have incomplete samples (some ran out of time at t=9300s).
Gemma AIME agents achieved **100% pass@k** — all 30 problems solved by at least one sample.

### AIME pass@4 sample completeness

| Model | Harness | Tasks | Scored/120 | Pass@1 | Complete tasks (4/4) | Partial | Empty |
|-------|---------|-------|------------|--------|---------------------|---------|-------|
| Qwen3.5-27B | DirectLLM | 30 | 120/120 | 90.8% | 30 | 0 | 0 |
| Qwen3.5-27B | OpenClaw | 30 | 92/120 | 64.2% | 17 | **10** (aime_23,4,24,17,16,14,18,13,15,5) | **3** (aime_26,21,19) |
| Qwen3.5-27B | OpenCode | 30 | 115/120 | 66.7% | 27 | **3** (aime_24,15,5) | 0 |
| Qwen3.5-27B | ZeroClaw | 30 | 120/120 | 69.2% | 30 | 0 | 0 |
| Gemma4-31B | DirectLLM | 30 | 120/120 | 92.5% | 30 | 0 | 0 |
| Gemma4-31B | OpenClaw | 30 | 117/120 | 95.0% | 27 | **3** (aime_20,18,2) | 0 |
| Gemma4-31B | OpenCode | 30 | 119/120 | 96.7% | 29 | **1** (aime_5) | 0 |
| Gemma4-31B | ZeroClaw | 30 | 120/120 | 96.7% | 30 | 0 | 0 |

### AIME logprob structure (pass@4)

Multi-sample logprobs use a two-level structure:
```
logprobs/aime_X.jsonl          ← sample 0
logprobs/aime_X/sample_1.jsonl ← sample 1
logprobs/aime_X/sample_2.jsonl ← sample 2
logprobs/aime_X/sample_3.jsonl ← sample 3
```

Qwen OpenClaw and ZeroClaw have mixed structures (some tasks use nested dirs, others use flat jsonl).
Gemma pass@4 all use the nested structure consistently.

---

## IMO-AnswerBench (400 tasks full; 134 tasks phase9 subset)

| Model | Harness | N | Acc | LP | Location |
|-------|---------|---|-----|----|----------|
| Qwen3.5-27B | DirectLLM | 134 | 45.5% | YES | `alphadiana_results/phase9_directllm_qwen35_27b_imo_answerbench_logprobs` |
| Qwen3.5-27B | DirectLLM | 400 | 50.5% | no | `alphadiana_results/full_20260423_imo_answerbench_direct_llm_qwen35_27b_localvllm_t10_r1` |
| Qwen3.5-27B | OpenClaw | — | NEED HF SYNC | — | — |
| Qwen3.5-27B | OpenCode | — | NEED HF SYNC | — | — |
| Qwen3.5-27B | ZeroClaw | — | NEED HF SYNC | — | — |
| Gemma4-31B | DirectLLM | 400 | 59.0% (236/400) | no (tasks only) | `results/hf-alphadiana-benchmark-results/full_run/20260501-imo-answerbench-directllm-gemma4-31b-v01/results/tasks` |
| Gemma4-31B | OpenClaw | 400 | DONE (external result store) | NEED SYNC | tracker: `full_run/20260505-imo-answerbench-openclaw-gemma4-31b-v01` |
| Gemma4-31B | OpenCode | 400 | DONE (external result store) | NEED SYNC | tracker: `full_run/20260502-imo-answerbench-opencode-gemma4-31b-v01` |
| Gemma4-31B | ZeroClaw | 400 | DONE (external result store) | NEED SYNC | tracker: `full_run/20260505-imo-answerbench-zeroclaw-gemma4-31b-v01` |

---

## Unsynced / Off-Machine Data

The following result directories exist per the experiment tracker but are **not accessible from this machine**:

### Gemma4-31B
- IMO-AnswerBench: OpenClaw, OpenCode, ZeroClaw (3 dirs — tracker says DONE at HF `full_run/` paths)

### Qwen3.5-27B
- IMO-AnswerBench: OpenCode (tracker says DONE at `/hd1/models/siatmri_alphadiana_results/`)
- IMO-AnswerBench: OpenClaw, ZeroClaw now in `results/422_full/results/merged_imo_*`

### Sync status (verified 2026-05-07)
| Model | Benchmark | On disk | Missing | Total |
|-------|-----------|---------|---------|-------|
| Gemma4-31B | GPQA | 4/4 ✅ | 0 | 4 |
| Gemma4-31B | HLE | 4/4 ✅ | 0 | 4 |
| Gemma4-31B | AIME | 4/4 ✅ | 0 | 4 |
| Gemma4-31B | IMO | 1/4 (DirectLLM) | 3 (OC/OCo/ZC) | 4 |
| Qwen3.5-27B | GPQA | 4/4 ✅ | 0 | 4 |
| Qwen3.5-27B | HLE | 4/4 ✅ | 0 | 4 |
| Qwen3.5-27B | AIME | 4/4 ✅ | 0 | 4 |
| Qwen3.5-27B | IMO | 3/4 (DL, OC, ZC) | 1 (OCo) | 4 |

---

## Data Integrity Summary

### By status

| Status | Count | Items |
|--------|-------|-------|
| ✅ Complete | 18 | Qwen GPQA DirectLLM/OpenClaw, Qwen HLE DirectLLM/OpenCode/ZeroClaw, Qwen IMO DirectLLM, Gemma GPQA 4/4, Gemma HLE 4/4, Gemma AIME 4/4, Gemma IMO DirectLLM |
| ⚠️ Minor issues | 7 | HLE Qwen OpenClaw (3 null), GPQA Qwen OpenCode (1 null + lp delta), GPQA Qwen ZeroClaw (7 null), GPQA Gemma OpenClaw (2 null), AIME Qwen OpenCode (5 null, 5 lp), AIME Qwen ZeroClaw (16 lp), HLE Gemma ZeroClaw (587/591 lp) |
| 🔴 Major issues | 2 | AIME Qwen OpenClaw (28/120 null, 62 lp), AIME Qwen ZeroClaw (lp structure mismatch) |
| 🟡 Off-machine | 4 | Gemma IMO OpenClaw/OpenCode/ZeroClaw (3), Qwen IMO OpenCode (1) |

### Null scores (task JSON has `correct: null`)

| Data | Null count | Notes |
|------|-----------|-------|
| GPQA Qwen ZeroClaw | 7/198 | Needs re-scoring or was run without oracle |
| AIME Qwen OpenClaw | 28/120 | t=9300s timeout, 10 partial + 3 empty tasks |
| AIME Qwen OpenCode | 5/120 | 3 partial tasks (aime_24, aime_15, aime_5) |
| AIME Gemma OpenClaw | 3/120 | Minor timeout |
| AIME Gemma OpenCode | 1/120 | Minor |
| HLE Qwen OpenClaw | 3/591 | Minor |
| GPQA Gemma OpenClaw | 2/198 | Verified 2026-05-07 |
| HLE Qwen OpenCode | 1/591 | Minor |
| GPQA Qwen OpenCode | 1/198 | Minor |

### Logprob vs token_entropy_stats.n_tokens mismatch

| Data | lp lines | n_tokens | delta | Cause |
|------|----------|----------|-------|-------|
| AIME Gemma DirectLLM | 1967 | 1671 | -296 | **Thinking tokens** (~15% of completion) |
| AIME Qwen OpenClaw | 22006* | varies | varies | Multi-sample: top-level jsonl = sample 0 only |
| AIME Qwen ZeroClaw | 4951* | 1473 | +3478 | Mixed flat+nested structure |
| GPQA Qwen OpenCode | 528 | 717 | -189 | Tool call formatting tokens |
| GPQA Qwen DirectLLM | 7114 | 7114 | 0 | No thinking mode, perfect match |

*Sample 0 only; other samples in nested subdirectories.

---

## Where Are Reasoning Tokens Stored?

### Summary by harness

| Harness | Token data location | Fields available | Precision |
|---------|-------------------|-------------------|-----------|
| **DirectLLM** | `tasks/*.json` → `token_usage` or `response_json.usage` | `prompt_tokens`, `completion_tokens`, `total_tokens` | Exact |
| **OpenCode** | `artifacts/*/agent/normalized_trace.json` → `steps[].part.tokens` (step-finish events) | `input`, `output`, `reasoning`, `total` per step | Exact |
| **OpenClaw** | `artifacts/*/agent/normalized_trace.json` → `steps[]` with `tool_calls`/`tool_results` fields + content text length | Step-level text length proxy (~3 chars/token); tool output chars NOT model tokens | Estimated |
| **ZeroClaw** | `artifacts/*/agent/normalized_trace.json` (no tools) + `token_entropy_stats.n_tokens` | All output = reasoning | Exact via logprobs |

### Detailed breakdown

#### DirectLLM — `tasks/*.json`

```json
{
  "token_usage": {"prompt_tokens": 196, "completion_tokens": 7114, "total_tokens": 7310},
  "response_json": {"usage": {"prompt_tokens": 196, "completion_tokens": 7114, "total_tokens": 7310}}
}
```
- `completion_tokens` = all reasoning tokens (no tools)
- Also available: `token_entropy_stats.n_tokens` (same value)
- Logprobs: `logprobs/*.jsonl` (line count = `completion_tokens`)

#### OpenCode — `artifacts/*/agent/normalized_trace.json`

Each `step-finish` event has a **`tokens` dict** with per-step breakdown:
```json
{
  "role": "assistant",
  "type": "message",
  "content": "{\"part\": {\"type\": \"step-finish\", \"tokens\": {\"input\": 10581, \"output\": 800, \"reasoning\": 0, \"total\": 11381}, ...}}"
}
```

**Extraction logic**:
```
For each step where content parses as JSON with part.type == "step-finish":
  reasoning_tokens += part.tokens.output     (model-generated text in this step)
  input_tokens     += part.tokens.input      (context consumed)
  tool_overhead    += part.tokens.total - output - input  (negligible)

Tool outputs are in role=tool / type=tool_result steps — these are NOT model-generated
and NOT counted in output tokens.
```

#### OpenClaw — `artifacts/*/agent/normalized_trace.json`

Steps alternate between `role=assistant, type=tool_use` (model reasoning + tool call request)
and `role=tool, type=tool_result` (tool execution output):

```json
{"role": "assistant", "type": "tool_use", "content": "Let me compute...", "tool_calls": [...]}
{"role": "tool", "type": "tool_result", "content": "Traceback...ModuleNotFoundError", "tool_results": [...]}
```

**Extraction logic**:
```
reasoning_chars = sum(len(step.content) for step in steps if step.role == "assistant")
tool_output_chars = sum(len(step.content) for step in steps if step.role == "tool")
estimated_reasoning_tokens = reasoning_chars // 3
estimated_tool_output_tokens = tool_output_chars // 3  (NOT counted in model output!)
```

Note: OpenClaw does NOT have per-step token counts in the normalized trace.
Token counts must be estimated from text length (~3 chars per token) or obtained from `token_entropy_stats.n_tokens` (total output, excludes tool results).

#### ZeroClaw — `artifacts/*/agent/normalized_trace.json`

No tools. All model output is reasoning.
- `token_entropy_stats.n_tokens` = exact output token count
- `logprobs/*.jsonl` line count = same value
- Normalized trace steps are all reasoning messages

### Artifact directory contents and tool call/return locations

Each harness stores artifacts differently:

| Harness | Key files | Token data | Tool call | Tool return |
|---------|-----------|------------|-----------|-------------|
| **OpenCode** | `workspace/opencode_output.jsonl` | `step-finish.part.tokens` = `{input, output, reasoning, total}` | `tool_use` event with `part.tool` + `part.input` | NOT in output.jsonl (API-level) |
| **OpenClaw** | `workspace/openclaw_session.jsonl` | text_block length ÷ 3 (usage=0) | `message[].toolCall` blocks | `message[].toolResult` blocks + `sandbox_meta.json` |
| **ZeroClaw** | `agent/response.json` | All = reasoning | N/A | N/A |
| **DirectLLM** | `agent/response.json` | `response_json.usage` | N/A | N/A |

**OpenCode `opencode_output.jsonl` structure**:
```
{type: "step_start",  part: {type: "step_start", ...}}
{type: "text",        part: {type: "text", text: "I need to analyze..."}}
{type: "tool_use",    part: {type: "tool_use", tool: "bash", input: {...}}}
{type: "step_finish", part: {type: "step_finish", tokens: {input: 10581, output: 800, reasoning: 0, total: 11381}, reason: "..."}}
```
- `text` events = reasoning content (model-generated)
- `tool_use` events = tool invocation (name + arguments)
- `step_finish.tokens.output` = model output tokens for that step (reasoning + tool call syntax)
- `step_finish.tokens.input` = context tokens consumed at that point
- `step_finish.tokens.reasoning` = thinking/reasoning tokens (0 for Qwen3.5-27B; non-zero for models with thinking mode)

**OpenClaw `openclaw_session.jsonl` structure**:
```
{type: "message", message: {role: "user", content: [{type: "text", text: "..."}]}}
{type: "message", message: {role: "assistant", content: [
    {type: "text", text: "Let me compute..."},        ← reasoning text
    {type: "toolCall", ...}                            ← tool invocation
], usage: {input: 0, output: 0, ...}}}                 ← ALWAYS 0 (harness limitation)
{type: "message", message: {role: "toolResult", content: [
    {type: "text", text: "Traceback...ModuleNotFoundError"}  ← THE TOOL RETURN
]}}
```
- Assistant messages alternate between `text` + `toolCall` blocks
- `toolResult` messages contain the actual tool output
- `usage` is ALWAYS all-zeros — OpenClaw does not record per-message token usage
- **Sandbox commands**: `sandbox/sandbox_meta.json` → `artifact_collection_history[]` = `{command, exit_code, stdout, stderr, wall_time_sec}`

### Computing reasoning vs tool tokens

```python
def extract_token_breakdown(result_dir, harness):
    """Return {reasoning_tokens, tool_call_tokens, tool_output_chars, input_tokens}"""
    
    if harness == "directllm":
        with open(f"{result_dir}/tasks/{task_id}.json") as f:
            data = json.load(f)
        usage = data['token_usage'] or data['response_json']['usage']
        return {
            'reasoning_tokens': usage['completion_tokens'],
            'tool_call_tokens': 0,
            'tool_output_chars': 0,
            'input_tokens': usage['prompt_tokens'],
        }
    
    elif harness == "opencode":
        with open(f"{result_dir}/artifacts/{task_id}/workspace/opencode_output.jsonl") as f:
            events = [json.loads(l) for l in f if l.strip()]
        
        reasoning = tool_call = input_tok = 0
        for evt in events:
            part = evt.get('part', {})
            if part.get('type') == 'step_finish':
                tokens = part.get('tokens', {})
                reasoning += tokens.get('output', 0)  # model-generated tokens
                input_tok += tokens.get('input', 0)    # context tokens
            elif part.get('type') == 'tool_use':
                tool_call += 1  # count tool invocations
        
        return {
            'reasoning_tokens': reasoning,
            'tool_call_count': tool_call,
            'tool_output_chars': 0,  # NOT in opencode output
            'input_tokens': input_tok,
        }
    
    elif harness == "openclaw":
        with open(f"{result_dir}/artifacts/{task_id}/workspace/openclaw_session.jsonl") as f:
            events = [json.loads(l) for l in f if l.strip()]
        
        reasoning_chars = tool_output_chars = tool_calls = 0
        for evt in events:
            if evt.get('type') != 'message': continue
            msg = evt.get('message', {})
            role = msg.get('role', '')
            for block in msg.get('content', []):
                text = block.get('text', '')
                if role == 'assistant':
                    if block.get('type') == 'text':
                        reasoning_chars += len(text)
                    elif block.get('type') == 'toolCall':
                        tool_calls += 1
                elif role in ('toolResult', 'tool'):
                    tool_output_chars += len(text)
        
        return {
            'reasoning_tokens_est': reasoning_chars // 3,
            'tool_call_count': tool_calls,
            'tool_output_chars': tool_output_chars,
            'input_tokens': None,  # not recorded
        }
    
    elif harness == "zeroclaw":
        # All output = reasoning, no tools
        with open(f"{result_dir}/tasks/{task_id}.json") as f:
            data = json.load(f)
        n = data['token_entropy_stats']['n_tokens']
        return {
            'reasoning_tokens': n,
            'tool_call_tokens': 0,
            'tool_output_chars': 0,
            'input_tokens': None,
        }
```

---

## Thinking Tokens and Logprobs Coverage

### Do logprobs include thinking tokens? NO.

**Key finding**: `completion_tokens` = thinking tokens + visible output tokens.
Logprob files only record **visible output tokens**, NOT thinking tokens.

The delta between `completion_tokens` and `logprob_line_count` = thinking token count.

| Model | Harness | completion_tokens | logprob lines | delta (thinking) | Has reasoning_content? |
|-------|---------|-------------------|---------------|-------------------|------------------------|
| Qwen3.5-27B | DirectLLM | 7114 | 7114 | **0** | No |
| **Gemma4-31B** | DirectLLM | 2200 | 1967 | **233 (10.6%)** | **Yes (2348 chars)** |
| Qwen3.5-27B | OpenCode | 717 (n_tokens) | 528 | 189 (tool call overhead) | No |

### Where thinking tokens are recorded

| Model | Thinking tokens location | Visible output tokens location |
|-------|--------------------------|-------------------------------|
| **Qwen3.5-27B** | N/A (no thinking mode) | `completion_tokens` = `logprob_lines` |
| **Gemma4-31B** | `completion_tokens - logprob_lines` (delta) | `logprob_lines` |
| **Gemma4-31B** | `response.json` → `choices[0].message.reasoning_content` (text) | `choices[0].message.content` (text) |

### Gemma reasoning_content field

Gemma4-31B stores its thinking/reasoning text in `response.json`:

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "The visible answer text...",
      "reasoning_content": "Let me think step by step... (thinking text, NOT in logprobs)"
    }
  }],
  "usage": {"prompt_tokens": 197, "completion_tokens": 2200, "total_tokens": 2397}
}
```

- `reasoning_content` = the text of the model's internal reasoning (thinking phase)
- `content` = the visible output
- `completion_tokens` = thinking + visible tokens combined
- `logprobs/*.jsonl` = only visible tokens (thinking tokens EXCLUDED)

### OpenCode logprob vs n_tokens discrepancy

For OpenCode, `logprob_lines` < `n_tokens` even without thinking mode.
The missing tokens are **tool call formatting tokens** (function call syntax, JSON structure)
that are part of the model output but excluded from logprob recording.

```
Qwen OpenCode: 717 (n_tokens) - 528 (logprobs) = 189 tool formatting tokens
```

### Gemma thinking tokens by harness

| Harness | Has reasoning_content? | thinking_level_change | Notes |
|---------|----------------------|-----------------------|-------|
| DirectLLM | Yes (2348 chars) | N/A | Full thinking mode |
| OpenClaw | Yes (2675 chars) | `thinkingLevel: 'off'` | Still generates reasoning_content despite thinking being "off" |
| OpenCode | Check per-step | TBD | Likely same as OpenClaw |
| ZeroClaw | TBD | TBD | Likely same |

---

## Key Observations

1. **Gemma4-31B >> Qwen3.5-27B** across all benchmarks (GPQA +4pp, HLE +8pp, AIME +5pp at pass@1)
2. **GPQA harness ranking REVERSES for Gemma**: Qwen DirectLLM > agents; Gemma agents > DirectLLM
3. **AIME Gemma agents = 100% pass@k**: all 30 problems solved by at least 1 of 4 samples
4. **HLE remains hard**: even Gemma DirectLLM only 27.9%; both models <30% on knowledge-intensive benchmark
5. **Qwen AIME agent runs incomplete**: OpenClaw 92/120 samples, OpenCode 115/120 (t=9300s timeout)
