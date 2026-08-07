# Podman Nightly Validation Smoke Matrix

Validation-only Podman campaign configs for the May 2026 migration milestone.
These configs are intentionally opt-in and do not promote Podman as a default
runtime.

Use the runner script from the repository root:

```bash
bash scripts/run_podman_nightly_validation.sh all
```

Prerequisites:

- Provider variables: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL_NAME`.
- Local Podman images:
  - `localhost/alphadiana-openclaw:latest`
  - `localhost/zeroclaw-reasoning:0.6.9`
  - `localhost/alphadiana-opencode-podman:latest`
  - `localhost/alphadiana/tb2-opencode-controller:latest`
- TerminalBench2 task root at `TERMINAL_BENCH2_DIR` or `/tmp/terminal-bench-2`.
- User Podman socket for SWE-bench Verified:
  `${XDG_RUNTIME_DIR}/podman/podman.sock`.
- Writable Hugging Face dataset cache. The runner defaults
  `HF_DATASETS_CACHE` to `/tmp/alphadiana-hf-cache` so gated/cached HLE rows do
  not fail on shared read-only cache locks.

Scope:

- Standard reasoning: OpenClaw, ZeroClaw, and OpenCode on one task each for
  AIME, GPQA-Diamond, HLE multiple-choice, and IMO-AnswerBench.
- Task-container cells: TerminalBench2 OpenCode Podman with up to three
  official tasks, including `db-wal-recovery`; SWE-bench Verified OpenClaw
  Podman with two first Verified instances when the local harness can build
  them.
- MMMU-Pro is intentionally not included until a cheap Podman-specific
  multimodal smoke config is validated.
- SWE-bench Pro remains deferred.
- Direct x SWE/TB2 remains `-`.
