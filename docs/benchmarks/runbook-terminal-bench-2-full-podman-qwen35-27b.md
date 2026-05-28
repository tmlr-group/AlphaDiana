# Runbook — TerminalBench2 full run on local Qwen3.5-27B + Podman

Self-contained operator runbook for completing a **full TerminalBench2** sweep
(89 tasks across OpenClaw / OpenCode / ZeroClaw) against a local
`Qwen/Qwen3.5-27B` vLLM endpoint over Podman task containers.

> This is the "I want to run the benchmark" runbook. It deliberately
> duplicates content from the SWE-bench Verified Mini runbook so each can be
> followed top-to-bottom without cross-references. For the broader Podman
> support matrix and the framework-bug audit, see
> [`docs/benchmarks/podman.md`](podman.md).

Wall-clock estimate: **30+ hours per agent at `max_concurrent=1`** (89 tasks ×
3 agents). Plan a `tmux` session.

---

## 0. What "full run" means here

- Task source: every `task.toml` under your TerminalBench2 checkout (the
  loader skips `.git` and other non-task siblings automatically). The April
  2026 checkout has 89 real task directories; current upstream has 90. The
  override `benchmark.config.max_tasks=500` in §8 is intentionally larger
  than the real count so no task is silently dropped if the upstream
  task list grows.
- Agents: `terminal_bench2_openclaw`, `terminal_bench2_opencode`,
  `terminal_bench2_zeroclaw`.
- Each agent run produces 89 task JSON rows under
  `results/<run_id>/.../tasks/`.
- Scoring is binary per task (`reward.txt == "1"`).

---

## 1. Host prerequisites (one-time)

### 1.1 Podman 3.4.4+ and the user socket

```bash
podman --version              # expect ≥ 3.4.4
systemctl --user enable --now podman.socket
export ALPHADIANA_PODMAN_SOCKET="/run/user/$(id -u)/podman/podman.sock"
ls "$ALPHADIANA_PODMAN_SOCKET"  # must exist
```

### 1.2 Kernel keyring quota (root, once)

Rootless Podman allocates a session keyring per container. The default
`maxkeys=200` is exhausted under TB2 task churn and surfaces as the misleading
"unable to join session keyring: disk quota exceeded".

```bash
sysctl kernel.keys.maxkeys kernel.keys.maxbytes
# If less than 2000 / 200000:
echo -e "kernel.keys.maxkeys=2000\nkernel.keys.maxbytes=200000" \
  | sudo tee /etc/sysctl.d/99-podman-keyring.conf
sudo sysctl --system
sysctl kernel.keys.maxkeys kernel.keys.maxbytes  # must show 2000 / 200000
```

### 1.3 Podman storage off `/home`

A full TB2 sweep can pull dozens of task images. Make sure `graphroot`
points at a large disk (≥ 200 GB free recommended):

```bash
mkdir -p ~/.config/containers
cat > ~/.config/containers/storage.conf <<EOF
[storage]
driver = "overlay"
graphroot = "/path/to/$USER/podman-storage"  # adjust to a large mount
EOF
podman info --format '{{.Store.GraphRoot}}'  # confirm location
```

### 1.4 Output / log directory on a `/data*` mount (NOT `/home`)

```bash
export TB2_FULL_OUTPUT_ROOT=/path/to/$USER/alphadiana/tb2-full
mkdir -p "$TB2_FULL_OUTPUT_ROOT"
df -h "$TB2_FULL_OUTPUT_ROOT"   # confirm ≥ 200 GB free (more if logprobs are on)
```

`/home` typically fills up: a single TB2 full sweep can produce tens of GB of
task logs, and turning on logprob dual-write multiplies that.

---

## 2. vLLM endpoint

Launch (adjust GPU indices and port to your setup):

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3.5-27B \
  --host 0.0.0.0 --port 8011 \
  --trust-remote-code \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.9 \
  --max-model-len 200000 \
  --generation-config vllm \
  --override-generation-config '{"presence_penalty": 1.5}' \
  --reasoning-parser qwen3 \
  --served-model-name Qwen/Qwen3.5-27B \
  2>&1 | tee /path/to/$USER/vllm_qwen3.5_27b.log
```

Confirm the endpoint is reachable from the host **and** from a Podman host-network
container:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B

# Host-side probe
curl -sS --max-time 30 "$OPENAI_BASE_URL/models" | head -c 200

# Podman-side probe (host network mode, same as the pilot configs use)
podman run --rm --network host docker.io/library/python:3.12-slim \
  python -c "import urllib.request,sys; print(urllib.request.urlopen('$OPENAI_BASE_URL/models', timeout=10).status)"
# expect: 200
```

Why these flags matter:

- `--max-model-len 200000` — 200K context fits Qwen3.5-27B's native window;
  leaves room for `max_tokens=122 880` plus prompt + tool defs (ZeroClaw can
  serialize 30K+ of tool defs into the prompt).
- `--override-generation-config '{"presence_penalty": 1.5}'` — suppresses
  Qwen3.5's repetition loops on long-thinking traces.
- `--generation-config vllm` — ignore the model's `generation_config.json`
  which would otherwise quietly enable non-greedy defaults
  (`temperature=0.6`, `top_p=0.95`).
- `--reasoning-parser qwen3` — emits `delta.reasoning` events. The
  OpenClaw/ZeroClaw runtime overlays already rewrite these to
  `delta.reasoning_content` so the agents capture the thinking trace.

---

## 3. AlphaDiana repo + Python env

```bash
git clone <your-fork-or-origin> alphadiana
cd alphadiana
git checkout main

source scripts/activate.sh    # creates / activates the project venv
python -c "import alphadiana; print(alphadiana.__file__)"  # sanity
```

---

## 4. Build the Podman runtime images (one-time)

The TB2 pilot YAMLs default to the thin controllers under
`docker/terminal_bench2/`. Build them with the expected tags:

```bash
podman build -f docker/terminal_bench2/Dockerfile.openclaw-controller \
  -t localhost/alphadiana-openclaw-swebench-runtime-source:latest .

podman build -f docker/terminal_bench2/Dockerfile.opencode-controller \
  -t localhost/alphadiana/tb2-opencode-controller:latest .

podman build -f docker/terminal_bench2/Dockerfile.zeroclaw-controller \
  -t localhost/zeroclaw-reasoning:0.6.9 .
```

If you've already built the fatter images per the main runbook
(`alphadiana-openclaw-fixed:latest`, `alphadiana-opencode-podman:latest`,
`zeroclaw-reasoning:0.6.9`), you can skip the build and override at runtime:

```bash
export TB2_OPENCLAW_RUNTIME_IMAGE=localhost/alphadiana-openclaw-fixed:latest
export TB2_OPENCODE_RUNTIME_IMAGE=localhost/alphadiana-opencode-podman:latest
export TB2_ZEROCLAW_RUNTIME_IMAGE=localhost/zeroclaw-reasoning:0.6.9
```

Both image shapes have passing TB2 readiness evidence under host networking.

Verify the runtime images are visible:

```bash
podman images | grep -E "openclaw|opencode|zeroclaw"
```

---

## 5. Get the TerminalBench2 task tree

```bash
git clone --depth=1 https://github.com/laude-institute/terminal-bench.git \
  /path/to/$USER/terminal-bench-2
export TERMINAL_BENCH2_DIR=/path/to/$USER/terminal-bench-2
ls "$TERMINAL_BENCH2_DIR" | head    # expect task subdirs (db-wal-recovery, etc.)
find "$TERMINAL_BENCH2_DIR" -maxdepth 2 -name task.toml | wc -l   # 89 in 2026-04 checkout, 90 in 2026-05 upstream
```

The pilot YAMLs read `${TERMINAL_BENCH2_DIR}` directly — the loader skips
`.git` and other non-task siblings.

Per-task Docker images are pulled lazily on first use. To pre-pull all 89
images and avoid per-task stalls during the run:

```bash
python - <<'PY' | sort -u | xargs -r -n1 podman pull
import os, tomllib
from pathlib import Path
for tt in Path(os.environ["TERMINAL_BENCH2_DIR"]).glob("*/task.toml"):
    image = tomllib.load(tt.open("rb")).get("environment", {}).get("docker_image")
    if image:
        print(image)
PY
```

This takes 30–90 minutes depending on bandwidth; do it before kicking off the
campaign.

---

## 6. Export the runtime environment

```bash
# Provider
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B

# Podman socket
export ALPHADIANA_PODMAN_SOCKET="/run/user/$(id -u)/podman/podman.sock"

# TB2 task tree
export TERMINAL_BENCH2_DIR=/path/to/$USER/terminal-bench-2

# Runtime images (skip if you built the default tags in §4)
# export TB2_OPENCLAW_RUNTIME_IMAGE=localhost/alphadiana-openclaw-fixed:latest
# export TB2_OPENCODE_RUNTIME_IMAGE=localhost/alphadiana-opencode-podman:latest
# export TB2_ZEROCLAW_RUNTIME_IMAGE=localhost/zeroclaw-reasoning:0.6.9

# Output / logs on a large disk
export ALPHADIANA_TB2_OUTPUT_DIR=/path/to/$USER/alphadiana/tb2-full/results
export ALPHADIANA_TB2_LOGS_DIR=/path/to/$USER/alphadiana/tb2-full/task-logs
mkdir -p "$ALPHADIANA_TB2_OUTPUT_DIR" "$ALPHADIANA_TB2_LOGS_DIR"

# Run id and concurrency
export PODMAN_TB2_RUN_PREFIX=tb2_full_$(date +%Y%m%d_%H%M%S)
# Keep concurrency at 1 for the first full sweep; the runner respects this.
export PODMAN_TB2_REDO_ALL=1
```

The runner script auto-exports `PODMAN_TB2_PREFLIGHT_NETWORK=host` when
`OPENAI_BASE_URL` is `127.0.0.1` / `localhost`, matching the pilots'
`podman_network: host`. For a non-loopback provider URL, leave that variable
unset (the probe defaults to Podman bridge networking).

---

## 7. Validate + preflight (must both be green before kicking off)

```bash
# 1. Config validation (parses 3 pilot YAMLs)
bash scripts/run_podman_terminal_bench2_readiness.sh validate

# 2. Live preflight — checks Podman socket, runtime images, task images,
#    OPENAI_BASE_URL reachability from a Podman host-network container.
bash scripts/run_podman_terminal_bench2_readiness.sh preflight

# Verify ok=true in the JSON status:
PREFLIGHT="$(ls -t context/podman-terminal-bench2-readiness/preflight-${PODMAN_TB2_RUN_PREFIX}.json | head -1)"
python -c "import json; d=json.load(open('$PREFLIGHT')); \
  print('ok=', d['ok'], 'failures=', d['failures'], 'warnings=', d['warnings'])"
# expect: ok= True failures= [] warnings= []
```

If `failures` lists `provider_unreachable_from_podman`, your vLLM endpoint
is not reachable from a Podman container with host networking — fix the
endpoint or `OPENAI_BASE_URL` before continuing.

---

## 8. Scale up the pilots to the full 89-task sweep

The shipped pilot YAMLs set `max_tasks: 3` and use only three deterministic
tasks. To run the full sweep, pass override flags via the alphadiana CLI for
each agent. Run each agent in its **own** `tmux` window — they share the vLLM
endpoint but produce independent task containers.

```bash
# Common overrides used by all three agents.
# `task_ids=` (empty value) clears the pilots' 3-task allow-list so the loader
# discovers every task.toml under $TERMINAL_BENCH2_DIR. `max_tasks=500` is a
# safety cap higher than any real task count.
common_overrides=(
  -o "benchmark.config.task_ids="
  -o "benchmark.config.max_tasks=500"
  -o "benchmark.config.tasks_dir=$TERMINAL_BENCH2_DIR"
  -o "output_dir=$ALPHADIANA_TB2_OUTPUT_DIR"
  -o "max_concurrent=1"
)

# --- OpenClaw -------------------------------------------------------------
tmux new -d -s tb2-openclaw "
  set -eux
  python -m alphadiana.cli run \
    configs/smokes/podman_terminal_bench2/terminal_bench2_openclaw_pilot.yaml \
    --redo-all \
    -o run_id=${PODMAN_TB2_RUN_PREFIX}_openclaw \
    ${common_overrides[@]} \
    2>&1 | tee logs/${PODMAN_TB2_RUN_PREFIX}_openclaw.log
"

# --- OpenCode -------------------------------------------------------------
tmux new -d -s tb2-opencode "
  set -eux
  python -m alphadiana.cli run \
    configs/smokes/podman_terminal_bench2/terminal_bench2_opencode_pilot.yaml \
    --redo-all \
    -o run_id=${PODMAN_TB2_RUN_PREFIX}_opencode \
    ${common_overrides[@]} \
    2>&1 | tee logs/${PODMAN_TB2_RUN_PREFIX}_opencode.log
"

# --- ZeroClaw ------------------------------------------------------------
tmux new -d -s tb2-zeroclaw "
  set -eux
  python -m alphadiana.cli run \
    configs/smokes/podman_terminal_bench2/terminal_bench2_zeroclaw_pilot.yaml \
    --redo-all \
    -o run_id=${PODMAN_TB2_RUN_PREFIX}_zeroclaw \
    ${common_overrides[@]} \
    2>&1 | tee logs/${PODMAN_TB2_RUN_PREFIX}_zeroclaw.log
"
```

Detach from any tmux window with `Ctrl-B D`. Re-attach with
`tmux attach -t tb2-openclaw` etc. List with `tmux ls`.

---

## 9. Monitor the run

```bash
# Live tail of one agent
tail -f logs/${PODMAN_TB2_RUN_PREFIX}_openclaw.log

# Per-agent progress: how many task JSON rows have been written
for agent in openclaw opencode zeroclaw; do
  count=$(find "$ALPHADIANA_TB2_OUTPUT_DIR/${PODMAN_TB2_RUN_PREFIX}_${agent}" \
            -name "tb2_*.json" 2>/dev/null | wc -l)
  echo "$agent: $count tasks done"
done

# Periodic vLLM health probe (don't rely on GPU-util)
while sleep 300; do
  curl -sS --max-time 30 "$OPENAI_BASE_URL/models" >/dev/null \
    && echo "$(date -Is) vllm ok" \
    || echo "$(date -Is) vllm WEDGED"
done
```

If vLLM wedges (probe times out repeatedly), restart it and the in-flight
tasks will retry. If a Podman crash leaves orphan sidecars behind:

```bash
podman ps -a --filter name=alphadiana- --format '{{.ID}}' | xargs -r podman rm -f -v
pkill -9 -u $USER -f slirp4netns
pkill -9 -u $USER -f containers-rootlessport
pkill -9 -u $USER -f conmon
```

(`PodmanAgentRuntime.start()` reaps name-matched orphans automatically on the
next run, so the manual cleanup is only needed if you can't wait for that.)

---

## 10. Audit + result interpretation

After all three agents finish:

```bash
bash scripts/run_podman_terminal_bench2_readiness.sh audit
```

Look at the audit MD under
`context/podman-terminal-bench2-readiness/audit-${PODMAN_TB2_RUN_PREFIX}.md`:

- `audit_passed: true`, `audit_failure_count: 0` means every row wrote task
  JSON with the expected artifacts. **Audit pass is the infra-readiness gate,
  not a model-quality claim.**
- Per-task scoring: a task with `reward.txt == "1"` is a pass; missing /
  non-`"1"` is a fail. Scored zeros are model behaviour, not framework
  failures, as long as the audit is clean.

To compute pass rates per agent:

```bash
for agent in openclaw opencode zeroclaw; do
  python - "$agent" <<'PY'
import json, sys
from pathlib import Path
agent = sys.argv[1]
root = Path(os.environ["ALPHADIANA_TB2_OUTPUT_DIR"]) / \
  f"{os.environ['PODMAN_TB2_RUN_PREFIX']}_{agent}"
rows = []
for p in sorted(root.rglob("tb2_*.json")):
    d = json.loads(p.read_text())[0]
    rows.append((p.name, d.get("score_status"), d.get("score")))
passed = sum(1 for _,_,s in rows if s == 1.0)
print(f"{agent}: {passed}/{len(rows)} pass, {len(rows)} rows")
PY
done
```

---

## 11. Operational risks and how to defuse them

| Risk | Symptom | Mitigation |
| --- | --- | --- |
| vLLM long-thinking deadlock | every request times out, log mtime stops advancing | Restart vLLM; in-flight tasks retry. Monitor `/v1/models` (not GPU-util). |
| Kernel keyring exhaustion mid-run | "unable to join session keyring: disk quota exceeded" | Raised in §1.2 — verify with `sysctl kernel.keys.maxkeys`. |
| `/data*` mount fills up | task JSON writes start to fail mid-run | §1.4 — check `df -h` before each agent and again every ~6 hours. |
| Podman crash leaves orphan sidecars | `podman ps` shows zombie `alphadiana-*` containers | Cleanup commands in §9; next `start()` reaps name-matched orphans. |
| ZeroClaw produces empty assistant output | many `agent_empty_output` rows, but provider blank-choice count is 0 | Known behaviour on long-tail tasks; counted as scored-zero, not framework failure. The audit will still pass. |

---

## 12. Resuming a partial run

The runner writes per-task JSON as it goes. Re-running the same
`run_id` **without** `--redo-all` resumes — tasks with existing JSON rows are
skipped. To restart from scratch, pass `--redo-all` (the snippet above does;
remove it for resume mode):

```bash
python -m alphadiana.cli run \
  configs/smokes/podman_terminal_bench2/terminal_bench2_openclaw_pilot.yaml \
  -o run_id=${PODMAN_TB2_RUN_PREFIX}_openclaw \
  -o benchmark.config.max_tasks=89 \
  -o benchmark.config.tasks_dir=$TERMINAL_BENCH2_DIR \
  -o output_dir=$ALPHADIANA_TB2_OUTPUT_DIR \
  -o max_concurrent=1
```

---

## 13. Output map

```
$ALPHADIANA_TB2_OUTPUT_DIR/
└── ${PODMAN_TB2_RUN_PREFIX}_<agent>/
    └── ...tb2.../tasks/
        ├── tb2_db-wal-recovery.json
        ├── tb2_fix-git.json
        └── ... (89 files)

$ALPHADIANA_TB2_LOGS_DIR/                  # raw task container logs
logs/${PODMAN_TB2_RUN_PREFIX}_<agent>.log  # outer harness log
context/podman-terminal-bench2-readiness/
  preflight-${PODMAN_TB2_RUN_PREFIX}.json
  run-status-${PODMAN_TB2_RUN_PREFIX}.tsv
  audit-${PODMAN_TB2_RUN_PREFIX}.json|.md
```

---

## 14. Scope of this runbook

- TerminalBench2 only; **89-task full sweep** per the checked-out task tree.
- Three agents: `terminal_bench2_openclaw`, `terminal_bench2_opencode`,
  `terminal_bench2_zeroclaw`.
- Local Qwen3.5-27B via vLLM on a 200K context; `max_tokens=122 880`
  (default after the framework's 8K auto-headroom on a 200K window).
- Does not cover SWE-bench, SWE-bench Pro, external_benchmark, MMMU-Pro, GPQA,
  AIME, HLE, IMO-AnswerBench, ROCK, or Direct × TB2.
- For the SWE-bench Verified Mini campaign, use
  [`runbook-swebench-verified-mini-docker-qwen35-27b.md`](runbook-swebench-verified-mini-docker-qwen35-27b.md).
