# Single-rollout examples

Use `alphadiana.cli.single_rollout` to run these configurations. YAML header comments retain the older module name; the current commands are listed below. Configuration fields, controller options and JSON output are unchanged.

These examples use AlphaHebe benchmark plugins through `--import`. Install those plugins and configure the runtime variables declared by the selected YAML.

## deepseek_ale_bench.yaml

```bash
python -m alphadiana.cli.single_rollout configs/examples/single_rollout/deepseek_ale_bench.yaml \
  --import alphahebe.integrations.diana.benchmarks.ale_bench --output result.json
```

## deepseek_evox_problems.yaml

```bash
python -m alphadiana.cli.single_rollout configs/examples/single_rollout/deepseek_evox_problems.yaml \
  --import alphahebe.integrations.diana.benchmarks.evox_problems --output result.json
```

## deepseek_hle_verified.yaml

```bash
python -m alphadiana.cli.single_rollout configs/examples/single_rollout/deepseek_hle_verified.yaml \
  --import alphahebe.integrations.diana.benchmarks.hle_verified --output result.json
```

## pi_ale_bench.yaml

```bash
python -m alphadiana.cli.single_rollout configs/examples/single_rollout/pi_ale_bench.yaml \
  --import alphahebe.integrations.diana.benchmarks.ale_bench --output result.json
```

## pi_evox_problems.yaml

```bash
python -m alphadiana.cli.single_rollout configs/examples/single_rollout/pi_evox_problems.yaml \
  --import alphahebe.integrations.diana.benchmarks.evox_problems --output result.json
```

## pi_hle_verified.yaml

```bash
python -m alphadiana.cli.single_rollout configs/examples/single_rollout/pi_hle_verified.yaml \
  --import alphahebe.integrations.diana.benchmarks.hle_verified --output result.json
```
