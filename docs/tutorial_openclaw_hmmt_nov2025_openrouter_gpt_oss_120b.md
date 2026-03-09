# AlphaDiana Beginner Tutorial: OpenClaw + OpenRouter `openai/gpt-oss-120b:free` + `MathArena/hmmt_nov_2025`

This guide is a beginner-ready runbook for this repo. It shows how to run one complete AlphaDiana evaluation with:

- Agent: `openclaw`
- Model provider: OpenRouter
- Model ID: `openai/gpt-oss-120b:free`
- Dataset: `MathArena/hmmt_nov_2025`
- Benchmark loader: `aime`
- Scorer: `exact_match`

The goal is not to explain every subsystem in depth. The goal is to get you from a fresh checkout to a first successful run, with clear checkpoints when something fails.

## 0. What You Are Running

The request flow for this tutorial is:

```text
AlphaDiana Runner
       |
       v
ROCK Proxy (:9001)
       |
       v
ROCK Sandbox
       |
       v
OpenClaw Gateway (:8080 inside sandbox)
       |
       v
OpenRouter API
       |
       v
openai/gpt-oss-120b:free
```

Important:

- AlphaDiana does not call OpenRouter directly in this setup.
- AlphaDiana calls an OpenClaw gateway running inside a ROCK sandbox.
- The OpenClaw gateway forwards model requests to OpenRouter.

## 1. Files Used In This Tutorial

You only need a few repo files to follow this guide:

- Template eval config: [openclaw_hmmt_nov2025_openrouter_gpt_oss_120b.yaml](.examples/openclaw_hmmt_nov2025_openrouter_gpt_oss_120b.yaml)
- Local config generator: [run_openclaw_hmmt_openrouter.py](./dev/run_openclaw_hmmt_openrouter.py)
- OpenClaw gateway config template: [openclaw.json](./openclaw_deploy/openclaw.json)
- ROCK agent config template: [rock_agent_config.yaml](./openclaw_deploy/rock_agent_config.yaml)

You do not need to edit all of them manually. The helper script generates local copies for you.

## 2. Prerequisites

Before you start, make sure you have:

- Linux
- Python 3.10 to 3.12
- Docker
- Conda or another Python environment manager
- A working ROCK installation
- A running Redis instance for ROCK
- A running Ray head node for ROCK
- An OpenRouter API key with access to `openai/gpt-oss-120b:free`

If you are new to AlphaDiana, you should already be able to run:

```bash
pip install -e ".[all,dev]"
alphadiana list-benchmarks
```

Expected output includes `aime`.

## 3. Why This Tutorial Uses `aime` + `exact_match`

We reuse the existing `aime` benchmark loader because it already supports:

- loading a Hugging Face dataset by name
- choosing the split
- selecting custom `problem_field`
- selecting custom `answer_field`

We use `exact_match` instead of `numeric` because HMMT answers may be integers, fractions, radicals, or LaTeX-style text. In this repo, `exact_match` now applies math-aware normalization first, including common forms like:

- `\boxed{42}`
- `Final Answer: 42`
- `\frac{1}{2}` and `1/2`
- extra math wrappers like `$...$`

This is still not full symbolic equivalence. It is only a practical normalization layer.

## 4. Step 1: Create the Python Environment

From the repo root:

```bash
conda create -n alphadiana python=3.11 -y
conda activate alphadiana
cd AlphaDiana
pip install -e ".[all,dev]"
```

Quick verification:

```bash
alphadiana list-benchmarks
```

You should see:

```text
Registered benchmarks:
  - aime
  - math
  - hle
  - frontier_math
  - terminal_bench
  - osworld
```

## 5. Step 2: Create `.env`

Create a repo-local `.env` file:

```bash
cat > .env <<'EOF'
OPENROUTER_API_KEY="sk-or-v1-..."
EOF
```

You can also export it into the current shell:

```bash
set -a
source .env
set +a
```

The helper script in this tutorial reads `OPENROUTER_API_KEY` from `.env` first, then from the environment.

## 6. Step 3: Start ROCK Dependencies

This tutorial assumes OpenClaw will run inside ROCK. You need:

1. Redis
2. Ray
3. ROCK admin on port `9000`
4. ROCK proxy on port `9001`

A typical sequence looks like this:

```bash
docker run -d --name redis-stack -p 6379:6379 redis/redis-stack-server:latest
ray start --head --port=6380
python -m rock.deployments.admin
python -m rock.deployments.proxy
```

If your setup differs, that is fine. What matters is that these health checks pass:

```bash
curl http://localhost:9000/health
curl http://localhost:9001/health
```

If they do not, stop here and fix ROCK first. Do not move on to AlphaDiana yet.

## 7. Step 4: Deploy OpenClaw Into ROCK

This repo already contains deployment templates under [openclaw_deploy](./openclaw_deploy).

The simplest path is:

```bash
cd ./openclaw_deploy
python deploy.py
```

This should print a sandbox ID similar to:

```text
Sandbox ID: a37118857bee43188375c7ce2f343eb5
API base: http://127.0.0.1:9001/apis/envs/sandbox/v1/sandboxes/a37118857bee43188375c7ce2f343eb5/proxy/v1
```

Save that sandbox ID:

```bash
export OPENCLAW_SANDBOX_ID=a37118857bee43188375c7ce2f343eb5
```

If deployment fails, the problem is usually one of these:

- ROCK is not healthy
- Docker access is missing
- the OpenClaw package could not be installed inside the sandbox
- network access from the sandbox to OpenRouter is blocked

## 8. Step 5: Verify the OpenClaw Gateway Before Running a Benchmark

Do not start with the benchmark. First check that the OpenClaw gateway can answer a trivial prompt.

```bash
curl -X POST \
  "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/${OPENCLAW_SANDBOX_ID}/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openclaw",
    "messages": [
      {"role": "user", "content": "What is 2+3?"}
    ],
    "temperature": 0.0,
    "max_tokens": 128
  }'
```

If this fails, do not continue to the HMMT run. Fix the gateway first.

## 9. Step 6: Generate Local Config Files

Go back to the repo root:

```bash
cd AlphaDiana
```

Run:

```bash
python dev/run_openclaw_hmmt_openrouter.py --sandbox-id "${OPENCLAW_SANDBOX_ID}"
```

This script does three things:

1. reads `OPENROUTER_API_KEY` from `.env` or the environment
2. generates a local eval config with the real sandbox ID
3. generates a local ROCK agent config with the OpenRouter settings filled in

Generated files:

- [openclaw_hmmt_nov2025_openrouter_gpt_oss_120b.local.yaml](./dev/generated/openclaw_hmmt_nov2025_openrouter_gpt_oss_120b.local.yaml)
- [rock_agent_config.openrouter_gpt_oss_120b.local.yaml](./dev/generated/rock_agent_config.openrouter_gpt_oss_120b.local.yaml)

The generated eval config is based on [openclaw_hmmt_nov2025_openrouter_gpt_oss_120b.yaml](./configs/examples/openclaw_hmmt_nov2025_openrouter_gpt_oss_120b.yaml).

## 10. Step 7: Inspect the Generated Eval Config

The important part of the generated eval config is:

```yaml
agent:
  name: openclaw
  config:
    api_base: "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<YOUR_SANDBOX_ID>/proxy/v1"
    model: openclaw
    gateway_token: "OPENCLAW"

benchmark:
  name: aime
  config:
    dataset: "MathArena/hmmt_nov_2025"
    split: "train"
    problem_field: "problem"
    answer_field: "answer"

scorer:
  name: exact_match
```

If your dataset field names differ, edit the generated local file, not the shared example file.

## 11. Step 8: Validate the Config

Run:

```bash
alphadiana validate dev/generated/openclaw_hmmt_nov2025_openrouter_gpt_oss_120b.local.yaml
```

Expected output:

```text
Config is valid.
```

If validation fails, fix that before you run the benchmark.

## 12. Step 9: Run the Benchmark

Use either of these:

```bash
alphadiana run dev/generated/openclaw_hmmt_nov2025_openrouter_gpt_oss_120b.local.yaml
```

or:

```bash
python dev/run_openclaw_hmmt_openrouter.py --sandbox-id "${OPENCLAW_SANDBOX_ID}" --run
```

Recommended initial settings in this tutorial:

- `max_concurrent: 1`
- `temperature: 0.0`

This is intentionally conservative because free OpenRouter models are more likely to hit queueing or rate limits.

## 13. Step 10: Read the Results

Results are written under [results](./results).

The main output file is:

```text
results/<run_id>.jsonl
```

Per-task artifacts may also appear under:

```text
results/artifacts/
```

The JSONL records typically include:

- task ID
- problem text
- ground truth
- predicted answer
- correctness
- score
- rationale
- OpenClaw trajectory metadata when available

## 14. Common Beginner Failure Points

If you are stuck, check these in order:

1. `alphadiana list-benchmarks` works.
2. `.env` contains a valid `OPENROUTER_API_KEY`.
3. `curl http://localhost:9000/health` works.
4. `curl http://localhost:9001/health` works.
5. `python openclaw_deploy/deploy.py` returns a sandbox ID.
6. The `curl` chat-completions smoke test succeeds.
7. `alphadiana validate ...local.yaml` succeeds.
8. Only then run `alphadiana run ...local.yaml`.

This order matters. If step 6 fails, the benchmark run will fail too.

## 15. What To Change Later

Once the beginner path works, the most common next edits are:

- change `max_tokens`
- change `max_concurrent`
- change the benchmark dataset or split
- change `problem_field` or `answer_field`
- switch from `exact_match` to another scorer if the task format changes

Make those changes in the generated local config first. Keep the example config as a reusable template.

## 16. Summary

For a first successful run, the shortest path is:

1. install AlphaDiana
2. create `.env`
3. start ROCK
4. deploy OpenClaw
5. smoke test the gateway
6. generate the local config
7. validate it
8. run `alphadiana`

That is the intended beginner workflow for this repo for the HMMT OpenRouter setup.
