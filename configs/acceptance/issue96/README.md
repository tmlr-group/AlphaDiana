# Issue 96 acceptance matrix

These 28 explicit configurations cover five reasoning benchmarks on four harnesses,
SWE-bench Verified Mini on four harnesses, and Terminal-Bench 2 on four harnesses.
Every cell selects three tasks, one sample per task, no task retries, and the same
Qwen/Qwen3.5-27B model. A valid incorrect answer counts as an executed/scored task;
an infrastructure failure does not. These are functional integration checks, not
statistically meaningful benchmark comparisons.

`manifest.json` is the coverage authority. `fixtures.json` pins the semantic input
content of all 21 selected tasks, including attachment bytes, SWE reference/test
metadata, and the complete TB2 task asset tree. Paths are excluded from content
fingerprints; symlinks in pinned task assets are rejected. The public benchmark
loader must reproduce both the selected sequence and fingerprints before any
model call. Never regenerate these pins merely to make a changed input pass.

The matrix driver calls the public composition API, which uses the ordinary
ExperimentRunner. Native SWE-Agent and Harbor own their respective control loops;
their adapters do not emulate those loops in the composition layer. The native
executables remain optional, separately installed dependencies.

## Runtime inputs

Set these environment variables explicitly before loading YAML:

- `ISSUE96_RESULTS`: a fresh output directory for this validation campaign.
- `ISSUE96_WORK`: owned runtime logs, native jobs and verifier workspace directory.
- `ISSUE96_API_BASE`, `ISSUE96_API_KEY`: OpenAI-compatible model endpoint and a
  nonempty key. Some native adapters treat the literal `EMPTY` as an unset value;
  use an explicit local-service key for the acceptance server.
- `ISSUE96_GATEWAY_TOKEN`: OpenClaw gateway token.
- `ISSUE96_OPENCLAW_IMAGE`, `ISSUE96_OPENCODE_IMAGE`, `ISSUE96_ZEROCLAW_IMAGE`:
  immutable `sha256:` identities of local runtime images containing the actual
  native harness executables. Verify their identities in each selected engine.
- `ISSUE96_OPENCLAW_TB2_IMAGE`: immutable identity of the OpenClaw payload image
  with `/app` and `/usr/local` layout required by the TB2 overlay builder.
- `ISSUE96_TB2_DIR`: checkout containing the three pinned native task directories.
- `ISSUE96_SWEAGENT_BIN`, `ISSUE96_SWEAGENT_CHECKOUT`: native SWE-Agent executable
  and checkout with its default configuration.
- `ISSUE96_HARBOR_BIN`: native Harbor executable.
- `ISSUE96_HARBOR_PYTHON`: Python executable from that Harbor installation, used
  for its native pre-verification snapshot callback.

SWE and TB2 acceptance use Docker. Reasoning harness images use their explicitly
configured Podman runtimes. SWE instance images use the `issue96-base` tag and
must contain the exact selected dataset base commit under `/testbed`; native
SWE-Agent uses explicit per-instance image overrides. Preserve the official task
and verifier inputs. Check immutable identities before execution; a tag alone is
not provenance. Harbor jobs preserve shared base images during task cleanup.

The preliminary environment uses SWE-Agent 1.1.0 at
`0f4f3bba990e01ca8460b9963abdcd89e38042f2`, SWE-ReX 1.4.0, Harbor 0.6.1,
OpenClaw 2026.3.2, OpenCode 1.3.2 and ZeroClaw 0.6.9. Actual executable/image
identity and model weights/server configuration must accompany final acceptance
receipts; a YAML version string alone does not verify an installation.

The model server must support image inputs for MMMU and top logprobs for entropy
capture. Missing instrumentation is reported, never filled with synthetic values.
Generation uses an explicit 8192-token output budget with thinking disabled.
OpenCode declares the true 32768-token context plus a 24576-token input limit.
Its [pinned 1.3.2 compaction implementation](https://github.com/anomalyco/opencode/blob/v1.3.2/packages/opencode/src/session/compaction.ts)
subtracts the output reserve from the input limit, triggering compaction earlier
so new tool output and the compaction request have headroom. This retains native
compaction and the same model/output budget; it does not truncate task inputs.
Do not silently substitute a smaller model, task subset or modified verifier.
For offline execution, configure an accessible dataset cache separately. Existing
shared caches and container daemons must not be reconfigured for this campaign.

## Execution and evidence

Run from a clean checkout, with output outside that checkout:

```bash
python scripts/validate_api_matrix.py configs/acceptance/issue96/manifest.json \
  --output "$ISSUE96_WORK/acceptance"
```

`--only CELL_ID ...` is for diagnosis or a documented rerun; it does not satisfy
full-matrix acceptance. Each receipt records source revision/state, config hash,
input fingerprints, actual task IDs, scored count and artifact hash. Source changes
during a cell invalidate it. Failed receipts are preserved when rerunning; keep a
fresh campaign output directory and distinguish failed attempts from final evidence.

All 28 cells must pass on the final clean revision. Final review must also inspect
native runtime provenance, scoring/verifier logs, retained replay artifacts,
analysis coverage and exported figures. Successful matrix execution does not
replace the independent extensibility audit or human review.

Current status: fixtures are pinned and unit checked; interactive runtime pilots
and final-commit acceptance remain in progress. See
[the implementation ledger](../../../docs/implementation/issue96-plan.md).
