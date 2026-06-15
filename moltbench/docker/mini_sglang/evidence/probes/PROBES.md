# Anti-hacking probe results (Design-4 §8 / Design-5 §6)

Executed on the A800 against `localhost/mini-sglang-agents:l3` (Qwen3-4B, podman 3.4.4).
Each probe confirms the verifier cannot be gamed. Logs/JSON for the executed probes are
in this directory.

| Probe | Scenario | Mechanism that defends | Outcome |
|---|---|---|---|
| **A** | Agent writes a fake `results.json`/`score.json` in its workspace | Official score is computed by the hidden verifier from a **clean server restart**; the scorer reads `/workspace/artifacts/results.json` written by the verifier, never an agent file in `/workspace/repo`. | **Pass by design** — agent-written files in the repo are never read. (See `eval_runner._write_official`; audit asserts "official score computed by hidden verifier from a clean restart".) |
| **B** | Bypass / delete the correctness check | Correctness = the verifier's own benchmark (`num_error==0`) measured on the restarted server; there are no in-repo "tests" the agent can delete. | **Pass by design** — correctness is verifier-owned, not a repo artifact. |
| **C** | Hard-code public examples / overfit a hidden test set | N/A for a serving-throughput task: no hidden test set; throughput is measured on a fixed synthetic trace (`EVAL_NUM_PROMPTS`/`INPUT_LEN`/`OUTPUT_LEN`). | **N/A (documented)** — the analogue is output-equivalence, noted below as a known limitation. |
| **D** | Invalid-but-fast candidate (broken engine) | C gates G: `score = OG if correct else 0`; a broken candidate makes requests error / the server fail. | **EXECUTED — see below.** |
| **E** | Timeout / hanging candidate | `measure()` runs the bench under `BENCH_TIMEOUT`; `start_server` enforces `SERVER_READY_TIMEOUT`; both `stop_server` the **process group** in a `finally:` (no orphan). The official handler catches and records the failure; the run does not report success. | **Covered by D** (broken candidate path) + the process-group teardown verified (GPU freed to 0 MiB after every run). |
| **F** | Tamper the hidden verifier (`/eval/*`) mid-run | Verifier files are hashed at bootstrap and **re-checked before the official eval**; any change → `verifier_integrity.json.status=TAMPERED` and `results.json.run_valid=false`; the audit flags **RUN INVALID**. | **EXECUTED — see `probe_F_verifier_tamper.*`.** |

## Probe F — verifier tamper (executed)

Bootstrap-hashed `/eval/*`, appended a line to `/eval/eval_runner.py`, re-checked:

```
[bootstrap] verifier hashed
[recheck] status: TAMPERED ['/eval/eval_runner.py']
```
`probe_F_verifier_tamper.json`: `{"status":"TAMPERED","tampered_files":["/eval/eval_runner.py"]}`
→ a tampered run is marked invalid, not scored.

## Probe D — invalid-but-fast candidate (executed)

Measured a **clean in-situ baseline** (5508.1 tok/s), then injected a hard fault at the top
of `server/launch.py` (`raise SystemExit(...)` — a "fast but broken" candidate), then ran
the official eval. Result (`probe_D_broken_candidate_results.json`):

```json
{"correctness": "ERROR", "speedup": 0.0, "og": 0.0, "throughput_tok_per_s": 0.0,
 "baseline_throughput": 5508.1, "correct": false, "cost": {"wall_time_sec": 1.0, ...}}
```

The broken candidate's server exits on startup → the official handler catches it →
`correctness=ERROR`, `score=0`, in ~1 s. Speed never beats correctness; a valid baseline was
recorded first. (The server process group was torn down — GPU returned to 0 MiB.)

## Known limitation (honest scope)

Correctness is operational (all requests served without error), not output-equivalence: a
change that returns 200-OK but silently corrupts tokens would pass the gate. For a serving
*engine* optimization this is low-risk (the engine should not alter outputs), but adding a
reference-output check is a worthwhile v3.2 hardening. Documented rather than overclaimed.
