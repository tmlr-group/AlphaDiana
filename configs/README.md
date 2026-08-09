# Configuration map

Release configs are organized by experiment scale. Ordinary YAML files use the
same `ExperimentConfig` schema and can be passed to `alphadiana validate`,
`alphadiana run`, or `alphadiana batch`.

| Path | Purpose |
| --- | --- |
| `macro_runs/` | End-to-end benchmark × harness experiments |
| `micro_runs/` | Controlled Tool, Skill, and Memory ablations |
| `schema.yaml` | Annotated `ExperimentConfig` reference |
| `PROMPTS.md` | Canonical benchmark prompts |

Start with a one-task DirectLLM run:

```bash
source scripts/activate.sh
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=sk-EMPTY

alphadiana validate configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o benchmark.config.max_tasks=1
alphadiana run configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o run_id=smoke_aime_directllm \
  -o benchmark.config.max_tasks=1 --redo-all
```

The filename contract is `<benchmark>_<harness>_<model>.yaml`. Configs default
to one sample and one worker so they are safe to validate and smoke before
scaling. Provider settings use `OPENAI_MODEL_NAME`, `OPENAI_BASE_URL`, and
`OPENAI_API_KEY`; never commit a real key.

The sole `_campaign.yaml` file is intentionally different: it is a SWE-agent
rollout manifest consumed by `python -m alphadiana.benchmark_rollout_cli`, not
by `alphadiana run`. The macro README documents that exception and all external
runtime prerequisites.
