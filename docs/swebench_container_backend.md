# SWE-bench Container Backend

AlphaDiana now supports running SWE-bench tasks inside official SWE-bench instance containers through the `swebench_container` sandbox backend.

Key points:

- SWE-bench runs on this path do **not** use ROCK.
- One official SWE-bench instance container is created per task.
- OpenClaw is started inside that task container and exposed through a published localhost port.
- The benchmark loader remains `SWE-bench/SWE-bench_Verified`.
- The scorer still uses the official `swebench` harness.

Example config:

- [`configs/examples/openclaw_swe_bench.yaml`](../configs/examples/openclaw_swe_bench.yaml)

Runtime config:

- [`configs/examples/openclaw_swe_bench.runtime.json`](../configs/examples/openclaw_swe_bench.runtime.json)

Required local dependencies:

- `pip install .[agents,benchmarks,swebench]`
- Docker available to the current user

Required upstream model settings:

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL_NAME`

Typical flow:

1. `alphadiana validate configs/examples/openclaw_swe_bench.yaml`
2. `alphadiana run configs/examples/openclaw_swe_bench.yaml`

During a run, AlphaDiana will:

1. Resolve the official SWE-bench task image from task metadata with the `swebench` package.
2. Launch one task container through the `swebench_container` backend.
3. Upload the OpenClaw runtime config into that container.
4. Install or reuse `openclaw` in the container and start the gateway there.
5. Send the task to the in-container gateway and store results in the normal AlphaDiana result format.
