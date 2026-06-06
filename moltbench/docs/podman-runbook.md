# MoltBench Podman Runbook

Step-by-step guide for running MoltBench evaluation with Podman instead of Docker.

## Prerequisites

1. **Podman 4+** installed and working:
   ```bash
   podman --version
   podman info | grep -i runtime
   ```

2. **NVIDIA Container Toolkit** configured for Podman (CDI):
   ```bash
   # Generate CDI spec if not already done
   sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
   # Verify CDI devices are available
   podman run --rm --device nvidia.com/gpu=all ubuntu nvidia-smi
   ```

3. **AlphaDiana** installed:
   ```bash
   pip install -e .
   ```

## Step 1: Build Container Images

```bash
cd /path/to/AlphaDiana

# GPU base (~15 min, requires network for AutoKernel clone)
podman build --network host \
    -f moltbench/docker/Containerfile.base \
    -t tmlrgroup/alphadiana:gpu-base .

# Agent layer (pick the one you need):
podman build -f moltbench/docker/Containerfile.claude-code -t tmlrgroup/alphadiana:claude-code .
podman build -f moltbench/docker/Containerfile.openclaw    -t tmlrgroup/alphadiana:openclaw .
podman build -f moltbench/docker/Containerfile.opencode    -t tmlrgroup/alphadiana:opencode .
podman build -f moltbench/docker/Containerfile.zeroclaw    -t tmlrgroup/alphadiana:zeroclaw .
```

Verify images:
```bash
podman images | grep tmlrgroup/alphadiana
```

## Step 2: Configure API Keys

```bash
# For Claude Code via Anthropic API proxy:
export ANTHROPIC_BASE_URL="https://your-proxy/anthropic"
export ANTHROPIC_AUTH_TOKEN="your-token"

# For OpenClaw/OpenCode/ZeroClaw via OpenAI-compatible API:
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="https://your-api/v1"
export OPENAI_MODEL_NAME="your-model"
```

## Step 3: Run Smoke Test

### Claude Code (Podman)
```bash
python3 -m alphadiana.cli run configs/examples/moltbench_claude_code_podman_smoke.yaml
```

### OpenClaw (Podman)
```bash
python3 -m alphadiana.cli run configs/examples/moltbench_openclaw_podman_smoke.yaml
```

### Any agent via config override
```bash
# Use existing Docker config but force Podman engine:
python3 -m alphadiana.cli run configs/examples/moltbench_openclaw_smoke.yaml
# (change agent.name to "moltbench_podman" or set container_engine: podman)
```

## Step 4: Run Full Batch

```bash
# Same configs as Docker, just use moltbench_podman agent
python3 -m alphadiana.cli run configs/examples/moltbench_openclaw_L1_batch_v2.yaml
```

## GPU Modes

The `moltbench_podman` agent supports several GPU injection modes:

| Mode | Description | When to use |
|------|-------------|-------------|
| `auto` (default) | Try CDI first, fall back to legacy devices | Most systems |
| `cdi` | Force CDI mode | Modern Podman + NVIDIA Container Toolkit |
| `legacy_devices` | Direct `/dev/nvidia*` mounts | Older Podman or CDI not available |
| `none` | No GPU injection | CPU-only testing |

Set in config:
```yaml
agent:
  name: moltbench_podman
  config:
    podman_gpu_mode: auto  # or cdi, legacy_devices, none
```

## Troubleshooting

### CDI errors
If you see `stat nvidia.com/gpu=: no such file or directory`:
```bash
# Regenerate CDI spec
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
# Or use legacy mode
# (set podman_gpu_mode: legacy_devices in config)
```

### Image pull errors with Docker Hub
Podman requires explicit `docker.io/` prefix for Docker Hub images:
```bash
podman pull docker.io/nvidia/cuda:12.8.0-devel-ubuntu22.04
```
The `normalize_podman_image_ref()` function handles this automatically.

### Container won't start
Check Podman logs:
```bash
podman logs <container_id>
podman inspect <container_id>
```

### SELinux label issues
If containers fail with permission errors:
```yaml
agent:
  config:
    podman_disable_selinux_label: true  # default
```

## Validated Runs

| Date | Agent | Model | Config | Result |
|------|-------|-------|--------|--------|
| 2026-06-06 | Claude Code | mimo-v2.5-pro | podman smoke | PASS (score=0.861) |
| 2026-06-06 | OpenClaw | mimo-v2.5-pro | podman smoke | PASS (score=0.988) |
| 2026-06-06 | OpenCode | mimo-v2.5-pro | podman smoke | PASS (score=1.007) |
| 2026-06-06 | ZeroClaw | mimo-v2.5-pro | podman smoke | timeout (infra OK) |
