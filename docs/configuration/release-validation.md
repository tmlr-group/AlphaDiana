# Release validation matrix

Last verified: 2026-08-11.

This matrix separates schema/config validation from real model execution. A
validated config is structurally loadable; it does not prove that external
datasets, container images, credentials, or model services are available on a
new machine.

## Published configuration files

All ordinary experiment configs were checked individually with
`alphadiana validate`. The SWE-agent campaign manifest was checked with its
documented `benchmark_rollout_cli summary` command.

| Configuration group | Files | Validation result |
| --- | ---: | --- |
| Macro: AIME 2026 | 4 | 4/4 passed |
| Macro: GPQA-Diamond | 4 | 4/4 passed |
| Macro: HLE | 4 | 4/4 passed |
| Macro: IMO-AnswerBench | 4 | 4/4 passed |
| Macro: MMMU-Pro | 4 | 4/4 passed |
| Macro: SWE-bench Verified (`ExperimentConfig`) | 3 | 3/3 passed |
| Macro: Terminal-Bench 2 | 3 | 3/3 passed |
| Micro: Memory | 9 | 9/9 passed |
| Micro: Skill | 16 | 16/16 passed |
| Micro: Tool | 16 | 16/16 passed |
| SWE-agent campaign manifest | 1 | 1/1 summary expansion passed |
| **Total** | **68** | **68/68 passed with the correct loader** |

The 67 ordinary files comprise 26 macro configs and 41 micro configs. The one
remaining macro YAML is a campaign manifest, not an `ExperimentConfig`, and
must not be passed to `alphadiana validate` or `alphadiana run`.

## Real single-task smoke runs

The runs below used Qwen3.5-27B through an OpenAI-compatible endpoint. They
used one task and one worker. Terminal-Bench 2 agent execution was limited to
five minutes for smoke testing; the benchmark verifier retained its normal
independent timeout.

| Benchmark | Harness | Model reached | Tools/container reached | Verifier | Score status | Sample score |
| --- | --- | --- | --- | --- | --- | ---: |
| SWE-bench Verified | OpenClaw | yes | yes | completed | valid_scored | 1 |
| Terminal-Bench 2 | OpenClaw | yes | yes | ok | valid_scored | 0 |
| Terminal-Bench 2 | OpenCode | yes | yes | ok | valid_scored | 0 |
| Terminal-Bench 2 | ZeroClaw | yes | yes | ok | valid_scored | 0 |

The Terminal-Bench 2 task was `adaptive-rejection-sampler`. A zero score means
the agent did not solve that task under the smoke limit; it is not a startup or
scorer failure. OpenCode reached its five-minute limit after producing valid
events, while ZeroClaw stopped after its loop detector rejected repeated shell
actions. Both partial workspaces were still evaluated by the official task
verifier.

The release configs default to Podman for Terminal-Bench 2. These smoke runs
used the supported Docker override because the validation host already had the
required images in Docker. Docker loopback providers are routed through a
temporary host-side proxy and `host.docker.internal:host-gateway`; no fixed
server IP is required.

## Terminal-Bench 2 full-limit check

All three harnesses were also run on the first configured task,
`db-wal-recovery`, with the release `solver_timeout_sec: 1800` rather than the
five-minute smoke override.

| Harness | Agent return code | Verifier | Score status | Sample score |
| --- | ---: | --- | --- | ---: |
| OpenClaw | 0 | ok | valid_scored | 1 |
| OpenCode | 0 | ok | valid_scored | 0 |
| ZeroClaw | 124 | ok | valid_scored | 0 |

OpenClaw and OpenCode finished before the full limit. ZeroClaw lost the live
WAL after opening the database, then remained in an internal wait until the
1800-second guard ended it; its partial workspace was still evaluated. This is
an agent/runtime outcome, not a provider, container-start, or verifier failure.

The db-wal verifier normally downloads `uv` from GitHub even though AlphaDiana
already supplies a compatible `uvx` shim. The verifier bootstrap now satisfies
that installer step locally, so scoring does not depend on GitHub availability.
