# ZeroClaw AIME 2026 Runbook

This is the short, command-first guide for running **ZeroClaw on AIME 2026**.
Use [`tutorial_zeroclaw_aime2026.md`](tutorial_zeroclaw_aime2026.md) for the
full background and architecture notes.

## Which config to use

| Goal | Config | Notes |
| --- | --- | --- |
| Local smoke run | [`configs/examples/zeroclaw_aime2026_local_smoke.yaml`](../configs/examples/zeroclaw_aime2026_local_smoke.yaml) | No ROCK required |
| ROCK example run | [`configs/examples/zeroclaw_aime2026.yaml`](../configs/examples/zeroclaw_aime2026.yaml) | Gateway-backed auto-deploy path |
| DeepSeek full run | [`configs/full run/zeroclaw_aime2026_deepseek_chat_full.yaml`](../configs/full%20run/zeroclaw_aime2026_deepseek_chat_full.yaml) | Stable PR23 full-run path |

## Required environment

```bash
source scripts/activate.sh

export OPENAI_BASE_URL='https://api.example.com/v1/'
export OPENAI_API_KEY='sk-...'
export OPENAI_MODEL_NAME='deepseek-chat'
```

## Option 1: Local smoke run

Use this first to verify the provider and local `zeroclaw` binary work.

```bash
alphadiana validate configs/examples/zeroclaw_aime2026_local_smoke.yaml
alphadiana run configs/examples/zeroclaw_aime2026_local_smoke.yaml
```

## Option 2: ROCK example run

Use this when you want to exercise the ROCK auto-deploy integration.

```bash
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh

alphadiana validate configs/examples/zeroclaw_aime2026.yaml
alphadiana run configs/examples/zeroclaw_aime2026.yaml
```

`bash scripts/start_zeroclaw.sh` cannot export `ROCK_BASE_URL` and
`ROCK_PROXY_URL` back into your current shell. `source scripts/rock_env.sh`
before `alphadiana run`.

## Option 3: Full AIME 2026 run with DeepSeek chat

This is the shortest path to a known-good full-run setup from the PR23 work.

```bash
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh

alphadiana validate 'configs/full run/zeroclaw_aime2026_deepseek_chat_full.yaml'
alphadiana run 'configs/full run/zeroclaw_aime2026_deepseek_chat_full.yaml'
```

Key properties of this config:

- `model: deepseek-chat`
- `max_tasks: 30`
- `max_concurrent: 1`
- `use_gateway_in_sandbox: false`
- `temperature: 0.0`

That means the full run uses the ROCK sandbox, but executes ZeroClaw through
the direct in-sandbox CLI path rather than the bridge-backed gateway path.

## Monitoring

```bash
tail -f results/<run_id>/status/dashboard.txt
```

```bash
tail -f .cache/logs/<run_log>.log
```

If you launched the run in `screen`, you can also check:

```bash
screen -ls
```

## Results layout

- Task JSONs: `results/<run_id>/tasks/*.json`
- Plain-text dashboard: `results/<run_id>/status/dashboard.txt`
- Local logs: `.cache/logs/*.log`

## More detail

- Full tutorial: [`docs/tutorial_zeroclaw_aime2026.md`](tutorial_zeroclaw_aime2026.md)
- PR23 notes: [`contexts/pr23/zeroclaw/README.md`](../contexts/pr23/zeroclaw/README.md)
