#!/usr/bin/env python3
"""MoltBench mini-SGLang eval harness (hidden verifier — lives in /eval/).

Implements the MoltBench `run_eval` contract for the mini-SGLang serving task:
  - modes: baseline (in-situ, pristine code) | fast (agent feedback) | official (clean, scored)
  - server lifecycle via a process GROUP (kills minisgl multiprocessing workers)
  - warmup-excluded steady-state throughput (median over repeats) — Design-5 §12
  - normalized JSON output — Design-2 §8 / Design-4 §7
  - attempt counting + budget enforcement — Design-4/5 (call them "attempts", not "rounds")
  - OG computed against an IN-SITU baseline measured on the pristine repo at bootstrap,
    never a hardcoded constant and never an agent-written file (anti-hack — Design-5 §6)

The agent only ever runs `/eval/run_eval` (-> mode=fast). Official scoring uses
mode=official from a clean server restart on the agent's final code.

Config via environment (all have container defaults):
  REPO_DIR, MODEL_PATH, MINISGL_PORT, MEMORY_RATIO, PYBIN, PYTHONPATH_REPO,
  EVAL_NUM_PROMPTS, EVAL_INPUT_LEN, EVAL_OUTPUT_LEN,
  EVAL_WARMUP, EVAL_REPEATS, FAST_WARMUP, FAST_REPEATS,
  MAX_ATTEMPTS, STATE_DIR, ARTIFACTS_DIR, BENCH, SERVER_READY_TIMEOUT
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

# Verifier component versions — pinned into manifest.json (ConfigLock, Design-5 §3)
# and into every MoltEvent, so a leaderboard number is traceable to the exact code
# that produced it. Bump when the scoring/measurement contract changes.
RUN_EVAL_VERSION = "1.1.0"
SCORING_VERSION = "1.1.0"
VERIFIER_VERSION = "1.1.0"

E = os.environ.get
REPO_DIR = E("REPO_DIR", "/workspace/repo")
MODEL_PATH = E("MODEL_PATH", "")  # injected by run_minisgl.sh (from MODEL_PATH or MODEL alias); never hardcoded
PORT = int(E("MINISGL_PORT", "31000"))
MEMORY_RATIO = E("MEMORY_RATIO", "0.8")
# Optional KV-pool / scheduler knobs — pluggable so the SAME task can fairly A/B
# different models on different GPUs. NUM_PAGES pins the KV pool to an identical
# size across variants (so shared-GPU free-mem drift can't bias the comparison);
# MAX_RUNNING caps the concurrent decode batch. Empty -> use the engine default.
NUM_PAGES = E("NUM_PAGES", "").strip()
MAX_RUNNING = E("MAX_RUNNING_REQUESTS", "").strip()
PYBIN = E("PYBIN", sys.executable)
NUM_PROMPTS = E("EVAL_NUM_PROMPTS", "64")
INPUT_LEN = E("EVAL_INPUT_LEN", "128")
OUTPUT_LEN = E("EVAL_OUTPUT_LEN", "128")
OFFICIAL_WARMUP = int(E("EVAL_WARMUP", "1"))
OFFICIAL_REPEATS = int(E("EVAL_REPEATS", "3"))
FAST_WARMUP = int(E("FAST_WARMUP", "1"))
FAST_REPEATS = int(E("FAST_REPEATS", "1"))
MAX_ATTEMPTS = int(E("MAX_ATTEMPTS", "3"))
STATE_DIR = E("STATE_DIR", "/eval/state")
ARTIFACTS_DIR = E("ARTIFACTS_DIR", "/workspace/artifacts")
BENCH = E("BENCH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_serving.py"))
READY_TIMEOUT = int(E("SERVER_READY_TIMEOUT", "360"))
BENCH_TIMEOUT = int(E("BENCH_TIMEOUT", "900"))  # cap a single bench so GPU contention can't hang it forever (-> orphan server)

BASELINE_FILE = os.path.join(STATE_DIR, "baseline.json")
ATTEMPT_FILE = os.path.join(STATE_DIR, "attempt_count")
SERVER_LOG = os.path.join(STATE_DIR, "server.log")
SEED_COMMIT_FILE = os.path.join(STATE_DIR, "seed_commit.txt")
MODEL_COST_FILE = os.path.join(STATE_DIR, "model_cost.json")  # written by collect_artifacts.py
# Trajectory logs (append-only, on the mounted artifacts volume — Design-1 §11, §7).
EVENTS_FILE = os.path.join(ARTIFACTS_DIR, "events.jsonl")
FILE_DIFFS_FILE = os.path.join(ARTIFACTS_DIR, "file_diffs.jsonl")
RUN_ID = E("MOLTBENCH_RUN_ID", "") or E("RUN_ID", "")
AGENT_ID = E("MOLTBENCH_AGENT", "none")


def _log(msg: str) -> None:
    print(f"[eval_runner] {msg}", file=sys.stderr, flush=True)


def _event(event_type: str, payload: dict, *, stage_id: str = "", attempt_id: int = 0,
           visibility: str = "verifier") -> None:
    """Append one MoltEvent (Design-1 §11) to events.jsonl. Best-effort, append-only,
    written to the mounted volume immediately so a crash/timeout never loses it."""
    try:
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        ts = time.time()
        body = json.dumps(payload, sort_keys=True, default=str)
        evt = {
            "event_id": hashlib.sha1(f"{event_type}{ts}{body}".encode()).hexdigest()[:16],
            "run_id": RUN_ID,
            "stage_id": stage_id,
            "attempt_id": attempt_id,
            "agent_id": AGENT_ID,
            "event_type": event_type,
            "timestamp": ts,
            "verifier_version": VERIFIER_VERSION,
            "payload": payload,
            "content_hash": hashlib.sha256(body.encode()).hexdigest()[:16],
            "visibility": visibility,
        }
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(evt) + "\n")
    except Exception:
        pass  # logging must never break the eval


def _seed_commit() -> str:
    try:
        with open(SEED_COMMIT_FILE) as f:
            return f.read().strip()
    except Exception:
        return "HEAD"


def _record_file_diff(attempt: int, stage: str) -> None:
    """Append a per-attempt diff of the evaluated code vs the pristine seed
    (Design-5 Risk #11: per-attempt diffs, not only a final cumulative patch)."""
    try:
        seed = _seed_commit()
        # Exclude editable-install bytecode (regenerated after the seed commit) so the
        # per-attempt diff shows only real source edits, not __pycache__/*.pyc churn.
        pathspec = [".", ":(exclude)**/__pycache__/**", ":(exclude)**/*.pyc"]
        diff = subprocess.check_output(
            ["git", "-C", REPO_DIR, "diff", seed, "--", *pathspec],
            text=True, stderr=subprocess.DEVNULL, timeout=30)
        files = subprocess.check_output(
            ["git", "-C", REPO_DIR, "diff", "--name-only", seed, "--", *pathspec],
            text=True, stderr=subprocess.DEVNULL, timeout=30).split()
        rec = {"attempt": attempt, "stage_id": stage, "timestamp": time.time(),
               "seed_commit": seed, "files_changed": files,
               "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
               "diff": diff}
        with open(FILE_DIFFS_FILE, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as exc:
        _log(f"file_diff capture failed: {exc}")


def _cost_vector(attempt: int, wall: float, og: float, correct: bool) -> dict:
    """K (cost) vector — Design-4 §9. The verifier owns attempts + wall time; token /
    model-call cost is aggregated from the harness trajectory (model_cost.json, produced
    by collect_artifacts.py from the agent logs) so K is a complete vector, not partial."""
    cost = {
        "attempts_used": attempt,
        "max_attempts": MAX_ATTEMPTS,
        "wall_time_sec": round(wall, 1),
    }
    try:
        with open(MODEL_COST_FILE) as f:
            mc = json.load(f)
        cost["model_calls"] = int(mc.get("total_model_calls", 0))
        cost["input_tokens"] = int(mc.get("total_input_tokens", 0))
        cost["output_tokens"] = int(mc.get("total_output_tokens", 0))
        cost["total_tokens"] = cost["input_tokens"] + cost["output_tokens"]
        cost["token_source"] = mc.get("source", "agent_trajectory")
    except Exception:
        # Agent logs not yet collected (e.g. baseline/fast mode) — verifier-owned keys only.
        cost["model_calls"] = None
        cost["total_tokens"] = None
    # Cost per unit of improvement (Design-1 §13: combine K with G).
    gain = max(0.0, og - 1.0) if correct else 0.0
    cost["cost_per_improvement"] = (round(cost["wall_time_sec"] / gain, 1)
                                    if gain > 0 else None)
    return cost


def _server_env() -> dict:
    env = dict(os.environ)
    repo_py = E("PYTHONPATH_REPO", os.path.join(REPO_DIR, "python"))
    env["PYTHONPATH"] = repo_py + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _server_ready() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/v1/models", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def start_server():
    """Start the minisgl server in a new process group; return the Popen."""
    os.makedirs(STATE_DIR, exist_ok=True)
    logf = open(SERVER_LOG, "wb")
    cmd = [PYBIN, "-m", "minisgl.server.launch",
           "--model-path", MODEL_PATH, "--host", "0.0.0.0",
           "--port", str(PORT), "--memory-ratio", MEMORY_RATIO]
    if MAX_RUNNING:
        cmd += ["--max-running-requests", MAX_RUNNING]
    if NUM_PAGES:
        cmd += ["--num-pages", NUM_PAGES]  # pin KV pool -> identical pressure across variants
    proc = subprocess.Popen(
        cmd,
        cwd=REPO_DIR, env=_server_env(),
        stdout=logf, stderr=subprocess.STDOUT,
        start_new_session=True,  # own process group -> kill the whole tree later
    )
    _log(f"server pid={proc.pid} pgid={os.getpgid(proc.pid)} (warming up)")
    t0 = time.monotonic()
    while time.monotonic() - t0 < READY_TIMEOUT:
        if _server_ready():
            _log(f"server ready after {time.monotonic()-t0:.0f}s")
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early (code {proc.returncode}); see {SERVER_LOG}")
        time.sleep(1)
    stop_server(proc)
    raise RuntimeError(f"server not ready in {READY_TIMEOUT}s; see {SERVER_LOG}")


def stop_server(proc) -> None:
    if proc is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        time.sleep(3 if sig == signal.SIGTERM else 1)
        if proc.poll() is not None:
            return


def measure(warmup: int, repeats: int) -> dict:
    """Restart server on current REPO_DIR code and measure steady-state throughput."""
    out_json = os.path.join(STATE_DIR, "last_bench.json")
    proc = start_server()
    try:
        subprocess.run(
            [PYBIN, BENCH, "--host", "127.0.0.1", "--port", str(PORT),
             "--num-prompts", NUM_PROMPTS, "--input-len", INPUT_LEN, "--output-len", OUTPUT_LEN,
             "--warmup", str(warmup), "--repeats", str(repeats), "--output-json", out_json],
            check=True, env=_server_env(), timeout=BENCH_TIMEOUT,
        )
    finally:
        stop_server(proc)  # always runs, even on TimeoutExpired -> no orphan server
    with open(out_json) as f:
        return json.load(f)


def _read_baseline() -> float:
    try:
        with open(BASELINE_FILE) as f:
            return float(json.load(f)["baseline_throughput"])
    except Exception:
        return 0.0


def _bump_attempt() -> int:
    os.makedirs(STATE_DIR, exist_ok=True)
    n = 0
    try:
        with open(ATTEMPT_FILE) as f:
            n = int(f.read().strip())
    except Exception:
        n = 0
    n += 1
    with open(ATTEMPT_FILE, "w") as f:
        f.write(str(n))
    return n


def _emit(payload: dict) -> None:
    """Normalized JSON to stdout (the only thing the agent/host parses)."""
    print(json.dumps(payload, indent=2))


def _result(attempt, stage, correct, og, bench, baseline, wall, feedback, error=None) -> dict:
    return {
        "attempt": attempt,
        "stage_id": stage,
        "correct": bool(correct),
        "score": round(og, 4) if correct else 0.0,
        "metric": {
            "baseline_throughput": round(baseline, 2),
            "candidate_throughput": bench.get("throughput_tok_per_s", 0.0),
            "optimization_gain": round(og, 4),
            "ttft_p50_ms": bench.get("p50_ttft_ms", 0.0),
            "cold_throughput": bench.get("cold_throughput_tok_per_s", bench.get("throughput_tok_per_s", 0.0)),
            "throughput_ci95": bench.get("throughput_tok_per_s_ci95", 0.0),
            "throughput_stdev": bench.get("throughput_tok_per_s_stdev", 0.0),
            "per_pass_throughput": bench.get("per_pass_throughput", []),
            "num_success": bench.get("num_success", 0),
            "num_error": bench.get("num_error", 0),
        },
        # K (cost) vector — Design-4 §9: attempts + wall time (verifier-owned) plus
        # tokens / model-calls aggregated from the harness trajectory. See _cost_vector.
        "cost": _cost_vector(attempt, wall, og, bool(correct)),
        "wall_time_sec": round(wall, 1),
        "verifier_status": "PASS" if correct else ("ERROR" if error else "FAIL"),
        "feedback": feedback,
        "error": error,
    }


def run(mode: str) -> int:
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    t0 = time.monotonic()

    if mode == "baseline":
        _event("run_eval_call", {"mode": "baseline"}, stage_id="bootstrap")
        bench = measure(OFFICIAL_WARMUP, OFFICIAL_REPEATS)
        tput = bench["throughput_tok_per_s"]
        with open(BASELINE_FILE, "w") as f:
            json.dump({"baseline_throughput": tput, "raw": bench}, f, indent=2)
        wall = time.monotonic() - t0
        _event("verifier_result", {"baseline_throughput": tput, "correct": bench["correct"]},
               stage_id="bootstrap")
        _emit(_result(0, "bootstrap", bench["correct"], 1.0, bench, tput, wall,
                      f"Baseline measured: {tput:.1f} tok/s ({bench['correct']=})."))
        return 0

    if mode == "fast":
        attempt = _bump_attempt()
        _event("run_eval_call", {"mode": "fast"}, stage_id="improvement", attempt_id=attempt)
        if attempt > MAX_ATTEMPTS:
            wall = time.monotonic() - t0
            _event("budget_event", {"reason": "attempt_budget_exhausted",
                                    "max_attempts": MAX_ATTEMPTS},
                   stage_id="improvement", attempt_id=attempt)
            _emit(_result(attempt, "improvement", False, 0.0,
                          {"num_success": 0, "num_error": 0}, _read_baseline(), wall,
                          f"Attempt budget exhausted ({MAX_ATTEMPTS} attempts used). Stop here.",
                          error="attempt_budget_exhausted"))
            return 0
        baseline = _read_baseline()
        try:
            bench = measure(FAST_WARMUP, FAST_REPEATS)
            og = (bench["throughput_tok_per_s"] / baseline) if baseline > 0 else 0.0
            fb = (f"attempt {attempt}/{MAX_ATTEMPTS}: throughput={bench['throughput_tok_per_s']:.1f} tok/s, "
                  f"baseline={baseline:.1f}, OG={og:.3f}x, correct={bench['correct']}. "
                  + ("Improved — git commit it." if og > 1.0 and bench["correct"]
                     else "No gain or incorrect — git checkout -- . to revert."))
            res = _result(attempt, "improvement", bench["correct"], og, bench, baseline, time.monotonic()-t0, fb)
            _event("verifier_result", {"og": og, "correct": bench["correct"],
                                       "throughput": bench["throughput_tok_per_s"]},
                   stage_id="improvement", attempt_id=attempt)
        except Exception as exc:
            # broken candidate (server crash / bench error) still consumed an attempt —
            # record it too, so the trajectory is complete (failed attempts are the most
            # informative: they show what the agent tried that broke things).
            res = _result(attempt, "improvement", False, 0.0,
                          {"num_success": 0, "num_error": 0}, baseline, time.monotonic()-t0,
                          "Server failed to start or benchmark errored — likely your code change broke it. Revert with: cd /workspace/repo && git checkout -- .",
                          error=str(exc))
            _event("error_event", {"where": "fast", "error": str(exc)},
                   stage_id="improvement", attempt_id=attempt)
        _record_file_diff(attempt, "improvement")  # per-attempt diff of evaluated code vs seed
        with open(os.path.join(ARTIFACTS_DIR, "attempts.jsonl"), "a") as f:
            f.write(json.dumps(res) + "\n")
        _emit(res)
        return 0

    if mode == "official":
        _event("run_eval_call", {"mode": "official"}, stage_id="improvement",
               attempt_id=_read_attempt())
        baseline = _read_baseline()
        try:
            bench = measure(OFFICIAL_WARMUP, OFFICIAL_REPEATS)
        except Exception as exc:
            wall = time.monotonic() - t0
            _event("error_event", {"where": "official", "error": str(exc)},
                   stage_id="improvement", attempt_id=_read_attempt())
            res = _result(_read_attempt(), "improvement", False, 0.0,
                          {"num_success": 0, "num_error": 0}, baseline, wall,
                          "Official eval failed to run the candidate.", error=str(exc))
            _write_official(res)
            _emit(res)
            return 0
        og = (bench["throughput_tok_per_s"] / baseline) if baseline > 0 else 0.0
        res = _result(_read_attempt(), "improvement", bench["correct"], og, bench, baseline,
                      time.monotonic()-t0,
                      f"Official: candidate={bench['throughput_tok_per_s']:.1f} tok/s, baseline={baseline:.1f}, OG={og:.3f}x.")
        _record_file_diff(_read_attempt(), "official")
        _event("verifier_result", {"mode": "official", "og": og, "correct": bench["correct"],
                                   "throughput": bench["throughput_tok_per_s"]},
               stage_id="improvement", attempt_id=_read_attempt(), visibility="official")
        _write_official(res)
        _emit(res)
        return 0

    _log(f"unknown mode: {mode}")
    return 2


def _read_attempt() -> int:
    try:
        with open(ATTEMPT_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def _write_official(res: dict) -> None:
    # MoltBench-style results.json the scorer reads (C/G mapping), plus raw score.json.
    correctness = "PASS" if res["correct"] else ("ERROR" if res["error"] else "FAIL")
    m = res["metric"]
    results = {
        "correctness": correctness,
        "speedup": res["score"],                # OG as the scored gain
        "og": m["optimization_gain"],
        "throughput_tok_per_s": m["candidate_throughput"],
        "throughput_ci95": m.get("throughput_ci95", 0.0),
        "baseline_throughput": m["baseline_throughput"],
        "correct": res["correct"],
        "attempts_used": res["attempt"],
        "cost": res.get("cost", {}),
    }
    with open(os.path.join(ARTIFACTS_DIR, "results.json"), "w") as f:
        json.dump(results, f, indent=2)
    with open(os.path.join(ARTIFACTS_DIR, "score.json"), "w") as f:
        json.dump(res, f, indent=2)
    _write_serving_artifacts(m)


def _write_serving_artifacts(m: dict) -> None:
    """Serving-task artifacts required by Design-4 §10: throughput / latency metrics
    and the warmup policy, so a leaderboard number is auditable and reproducible."""
    A = lambda name: os.path.join(ARTIFACTS_DIR, name)
    with open(A("throughput_metrics.json"), "w") as f:
        json.dump({
            "throughput_tok_per_s_median": m["candidate_throughput"],
            "throughput_tok_per_s_ci95": m.get("throughput_ci95", 0.0),
            "throughput_tok_per_s_stdev": m.get("throughput_stdev", 0.0),
            "cold_throughput_tok_per_s": m.get("cold_throughput", 0.0),
            "per_pass_throughput": m.get("per_pass_throughput", []),
            "baseline_throughput": m["baseline_throughput"],
            "optimization_gain": m["optimization_gain"],
        }, f, indent=2)
    with open(A("latency_metrics.json"), "w") as f:
        json.dump({"ttft_p50_ms": m.get("ttft_p50_ms", 0.0),
                   "num_success": m.get("num_success", 0),
                   "num_error": m.get("num_error", 0)}, f, indent=2)
    with open(A("warmup_policy.json"), "w") as f:
        json.dump({
            "warmup_passes": OFFICIAL_WARMUP,
            "measured_passes": OFFICIAL_REPEATS,
            "reported_statistic": "median over measured passes (warmup excluded)",
            "num_prompts": NUM_PROMPTS, "input_len": INPUT_LEN, "output_len": OUTPUT_LEN,
            "memory_ratio": MEMORY_RATIO,
            "num_pages": NUM_PAGES or "engine-default",
            "max_running_requests": MAX_RUNNING or "engine-default",
            "model_path": MODEL_PATH,
            # token counting is an approximation (chars/4) — relative measure only, the
            # bias cancels in the candidate/baseline OG ratio. See bench_serving.py.
            "token_count": "approx chars/4 (relative; biased across tokenizers/languages)",
        }, f, indent=2)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "fast"
    sys.exit(run(mode))
