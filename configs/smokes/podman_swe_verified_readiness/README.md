# Phase 9 SWE-bench Verified Podman Readiness

These configs are the opt-in Phase 9 SWE-bench Verified readiness matrix for
the existing AlphaDiana `swebench_container` path. They do not claim SWE-bench
Pro support, full Verified support, or Podman as a default.

Run order:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
export ALPHADIANA_PODMAN_SOCKET="${ALPHADIANA_PODMAN_SOCKET:-/run/user/$(id -u)/podman/podman.sock}"

bash scripts/run_podman_swe_verified_readiness.sh validate
bash scripts/run_podman_swe_verified_readiness.sh preflight
bash scripts/run_podman_swe_verified_readiness.sh auto
```

`auto` runs `validate -> preflight -> smoke -> audit -> pilot32 -> audit ->
long64 -> audit -> sample128 -> audit` and stops at the first failed step.
The configs use Podman host networking by default on this host because
`http://127.0.0.1:8011/v1` is reachable from `podman run --network host`, while
default Podman networking did not reach the same loopback provider. Override
`PODMAN_SWE_TASK_NETWORK_MODE` only after a fresh preflight proves the alternate
network can reach the provider from a Podman container.

Tasksets live in `context/podman-swe-verified-readiness/tasksets/` and are
generated deterministically by:

```bash
python scripts/generate_podman_swe_verified_tasksets.py
```

Readiness is not a score gate. The audit passes `score=0` rows when task JSON,
raw logs, Podman metadata, artifact pointers, and a clear failure category are
present. Missing task JSON, missing raw log, missing Podman metadata, silent
skips, and unclassified provider/runtime failures are hard audit failures.
