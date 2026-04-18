# Bug Analysis: OpenClaw AIME 2024 Smoke Run (2026-03-21)

## Experiment Command

```bash
bash dev/quickstart.sh alphadiana
conda activate alphadiana && source dev/rock_env.sh && source dev/.rock_ports.env
docker pull ghcr.io/tsrigo/openclaw-reasoning:20260320
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your-key>
export OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507
alphadiana validate configs/examples/openclaw_aime2024.yaml
alphadiana run configs/examples/openclaw_aime2024.yaml \
  -o benchmark.config.max_tasks=1 \
  -o run_id=openclaw-aime2024-smoke-log-20260321
```

---

## Bugs Found

### Bug 1 — Sandbox memory too low (primary failure cause)

**File:** `configs/examples/openclaw_aime2024.yaml`

**Symptom:** The ROCK sandbox starts but the OpenClaw Node.js gateway OOMs or becomes
unresponsive. The `runner.py` auto-creates the sandbox using `rock_memory` from the agent
config. With only 765 MB the container kernel will OOM-kill the gateway process mid-startup
or mid-task.

**Root cause:** The local working-tree version of the file was edited:

```diff
-    rock_memory: "4g"
-    rock_cpus: 1
+    rock_memory: "765m"
+    rock_cpus: 0.5
```

The committed default is `4g` / `1 CPU` — consistent with the README statement:
> "This example now defaults to a single sandbox, `4g` memory, `1` CPU, and
> `max_concurrent: 1` to maximize first-run success."

**Fix applied:** Restored `rock_memory: "4g"` and `rock_cpus: 1`.

---

### Bug 2 — Hardcoded wrong model and exposed API key in `rock_agent_config.yaml`

**File:** `openclaw_deploy/rock_agent_config.yaml`

**Symptom:** When using the manual deploy path (`python openclaw_deploy/deploy.py
--agent-config openclaw_deploy/rock_agent_config.yaml`), the sandbox starts OpenClaw
configured for `z-ai/glm-5` instead of the exported `qwen/qwen3-235b-a22b-2507`.
Additionally, the real API key is stored in a tracked file — a security risk.

**Root cause:** The file was locally edited with experiment-specific values instead of
the template placeholders that the committed version uses:

```diff
-  OPENAI_BASE_URL: ""
-  OPENAI_API_KEY: "EMPTY"
-  OPENAI_MODEL_NAME: ""
+  OPENAI_BASE_URL: "https://openrouter.ai/api/v1"
+  OPENAI_API_KEY: "<your-key>"            # leaked secret
+  OPENAI_MODEL_NAME: "z-ai/glm-5"         # wrong model
```

Note: for the `alphadiana run` path the default flow uses
`openclaw_deploy/rock_agent_config.prebuilt.yaml` (not this file), and
`openclaw_runtime._prepare_runtime_files` overwrites the env section with the locally
exported env vars anyway. So this bug only directly breaks the **manual** deploy flow.
However, leaking credentials into a tracked file is independently harmful.

**Fix applied:** Restored the empty/placeholder values.

---

### Bug 3 — `openclaw.json` env section carries stale local-vLLM defaults into the sandbox

**File:** `openclaw_deploy/openclaw.json`
**Code fix:** `alphadiana/agent/openclaw_runtime.py` → `_prepare_runtime_files`

**Symptom:** The OpenClaw gateway starts successfully but all model calls return errors
such as `connection refused`, `ECONNREFUSED`, or provider-level 401/404, because the
gateway is talking to `http://<your-vllm-host>:8000/v1` (a stale internal vLLM endpoint)
instead of `https://openrouter.ai/api/v1`.

**Root cause:** `openclaw.json` contains an `env` section with values baked in for a
local vLLM setup:

```json
"env": {
  "OPENAI_BASE_URL": "http://<your-vllm-host>:8000/v1",
  "OPENAI_API_KEY": "EMPTY",
  "OPENAI_MODEL_NAME": "/hd1/models/models--Qwen--Qwen3-8B/snapshots/..."
}
```

OpenClaw reads this file and uses its `env` section to populate `process.env` before
expanding `${OPENAI_BASE_URL}` in the `models.providers.local.baseUrl` field.  If
OpenClaw applies its own config's `env` values **before** or **instead of** the shell
environment inherited from the ROCK container, `${OPENAI_BASE_URL}` expands to the
internal address — which is unreachable from any machine other than the original author's
local server.

The ROCK agent config (`rock_agent_config.prebuilt.yaml`) correctly injects the exported
env vars into the container, but `_prepare_runtime_files` was not also patching the
`openclaw.json` env section. This created a race between the two sources of truth.

**Fix applied:** In `_prepare_runtime_files`, after calling `_build_openclaw_config`,
the rendered `openclaw.json`'s own `env` section is now overwritten with the actual
values from the local environment:

```python
oc_env = rendered_openclaw_config.setdefault("env", {})
for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL_NAME"):
    local_val = os.environ.get(key, "")
    if local_val:
        oc_env[key] = local_val
```

This makes the `openclaw.json` written into the sandbox consistent with what the ROCK
agent config passes as container env vars, so `${OPENAI_BASE_URL}` always expands to
the correct provider endpoint regardless of what is baked into the source `openclaw.json`.

---

## Data-Flow Summary

```
alphadiana run
  └── Runner.run()
        └── auto-creates ROCK sandbox (rock_memory from agent_config)   ← Bug 1
              └── OpenClawRuntimeManager._prepare_runtime_files()
                    ├── reads rock_agent_config.prebuilt.yaml
                    │     patches env section with exported OPENAI_* vars  ✓
                    ├── reads openclaw.json
                    │     (was) copies env section as-is → stale URL       ← Bug 3
                    │     (fix) patches env section with exported vars      ✓
                    └── writes both files to temp dir → sandbox
                          OpenClaw gateway expands ${OPENAI_BASE_URL}
                          → correct provider (https://openrouter.ai/api/v1)
```

---

## Files Changed

| File | Change |
|------|--------|
| `configs/examples/openclaw_aime2024.yaml` | `rock_memory: "765m"` → `"4g"`, `rock_cpus: 0.5` → `1` |
| `openclaw_deploy/rock_agent_config.yaml` | Removed hardcoded credentials; restored `""` / `"EMPTY"` placeholders |
| `alphadiana/agent/openclaw_runtime.py` | `_prepare_runtime_files`: also patch `openclaw.json`'s `env` section |

---

## Verification

After applying fixes, re-run the smoke command:

```bash
conda activate alphadiana && source dev/rock_env.sh && source dev/.rock_ports.env
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your-key>
export OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507
alphadiana validate configs/examples/openclaw_aime2024.yaml
alphadiana run configs/examples/openclaw_aime2024.yaml \
  -o benchmark.config.max_tasks=1 \
  -o run_id=openclaw-aime2024-smoke-log-20260321
```

Expected: the sandbox starts (≤600 s), the OpenClaw gateway warms up, the single AIME
task completes, and a result appears in `results/openclaw-aime2024-smoke-log-20260321/`.
