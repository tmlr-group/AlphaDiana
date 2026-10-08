# API acceptance matrix

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

- `AD_ACCEPTANCE_RESULTS`: a fresh output directory for this validation campaign.
- `AD_ACCEPTANCE_WORK`: owned runtime logs, native jobs and verifier workspace directory.
- `AD_ACCEPTANCE_API_BASE`, `AD_ACCEPTANCE_API_KEY`: OpenAI-compatible model endpoint and a
  nonempty key. Some native adapters treat the literal `EMPTY` as an unset value;
  use an explicit local-service key for the acceptance server.
- `AD_ACCEPTANCE_GATEWAY_TOKEN`: OpenClaw gateway token.
- `AD_ACCEPTANCE_OPENCLAW_IMAGE`, `AD_ACCEPTANCE_OPENCODE_IMAGE`, `AD_ACCEPTANCE_ZEROCLAW_IMAGE`:
  immutable `sha256:` identities of local runtime images containing the actual
  native harness executables. Verify their identities in each selected engine.
- `AD_ACCEPTANCE_OPENCLAW_TB2_IMAGE`: immutable identity of the OpenClaw payload image
  with `/app` and `/usr/local` layout required by the TB2 overlay builder.
- `AD_ACCEPTANCE_TB2_DIR`: checkout containing the three pinned native task directories.
- `AD_ACCEPTANCE_SWEAGENT_BIN`, `AD_ACCEPTANCE_SWEAGENT_CHECKOUT`: native SWE-Agent executable
  and checkout with its default configuration.
- `AD_ACCEPTANCE_SWE_IMAGE_TAG`: local SWE instance-image tag; each image must
  match the pinned dataset base commit.
- `AD_ACCEPTANCE_HARBOR_BIN`: native Harbor executable.
- `AD_ACCEPTANCE_HARBOR_PYTHON`: Python executable from that Harbor installation, used
  for its native pre-verification snapshot callback.

SWE and TB2 acceptance use Docker. Reasoning harness images use their explicitly
configured Podman runtimes. SWE instance images use the explicit `AD_ACCEPTANCE_SWE_IMAGE_TAG` and
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
python scripts/validate_api_matrix.py configs/acceptance/api/manifest.json \
  --output "$AD_ACCEPTANCE_WORK/acceptance"
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

Run-specific logs, reports and machine configuration belong in external output
directories and versioned validation artifacts, not in this source tree.

## Separate execution and analysis

Run one configured experiment with the existing CLI:

```bash
python -m alphadiana run configs/acceptance/api/aime2026_directllm.yaml
```

Or validate one configuration through the public Python API with the test driver:

```bash
python scripts/validate_api_matrix.py configs/acceptance/api/manifest.json \
  --only aime2026_directllm --output "$AD_ACCEPTANCE_WORK/receipts"
```

Export saved evidence in a separate Python session, without model execution:

```python
import alphadiana as ad

ad.analyzer("results/api_accept_aime2026_directllm.jsonl").export("figures/aime")
```

For direct typed composition, see the [Python API guide](../../../docs/getting-started/python-api.md).
