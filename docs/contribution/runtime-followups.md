---
sidebar_position: 5
---

# Podman Runtime Safeguards

AlphaDiana checks the Podman runtime before and during a pilot so configuration
errors are reported early and result evidence stays reviewable. These checks
apply to the opt-in OpenClaw, ZeroClaw, and OpenCode Podman paths documented in
the [Podman runbook](../benchmarks/podman).

## What is checked

| Safeguard | User-visible behavior |
|---|---|
| ZeroClaw port consistency | `agent.config.bridge_port` controls the bridge listener, container exposure, health probe, and published API URL. Invalid or unavailable ports fail before evaluation begins. |
| Local and remote proxy routing | Podman host aliases, loopback names, and the selected provider are added consistently to `NO_PROXY` and `no_proxy`. Remote providers can continue to use the operator's outbound proxy. |
| Image compatibility | The runtime overrides task-specific image entrypoints, checks the required shell, interpreter, modules, and agent binary, and records the resolved image identity and preflight output. |
| Readiness gate | `scripts/run_podman_scale_readiness.sh gate` runs every pilot cell and then audits results, logs, provenance, and artifacts. A scored answer may be correct or incorrect; missing or invalid execution evidence fails the gate. |
| OpenClaw version contract | Podman configs declare the version installed by the repository image. Startup verifies `openclaw --version` against that declaration and records the expected version in runtime metadata. |

OpenClaw runtime-managed paths generate a strong gateway token when a standalone
run does not provide one. The repeatable matrix command requires
`OPENCLAW_GATEWAY_TOKEN` explicitly, so operators can keep one token across the
pilot without committing it.

## Contributor checks

After changing one of these paths, run the focused regression suite and config
validation before a real smoke:

```bash
python -m pytest -q tests/test_podman_runtime_readiness.py
bash -n scripts/run_podman_scale_readiness.sh

export OPENCLAW_GATEWAY_TOKEN="$(python3 -c \
  'import secrets; print(secrets.token_urlsafe(32))')"
bash scripts/run_podman_scale_readiness.sh validate
```

For release evidence, use `gate`, keep its raw logs and audit report, and inspect
the first record in each `results/<run_id>/.../tasks/*.json` sample list. A
runtime-ready row has `score_status: valid_scored`; both `score: 0` and
`score: 1` are valid execution outcomes.
