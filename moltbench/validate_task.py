#!/usr/bin/env python3
"""MoltBench task admission validator (Design-4 §6, §8, §16; Design-5 opening note).

Codifies the admission checklist that was previously done ad-hoc by hand. Two modes:

  # Static package check (no GPU) — config integrity, hidden-verifier isolation,
  # score-parser safety, editable-scope, anti-hack design:
  python moltbench/validate_task.py static

  # Post-run artifact check — completeness, baseline reproducibility, correctness gate:
  python moltbench/validate_task.py artifacts <run_artifacts_dir>

Exit code 0 = PASS, 1 = INCOMPLETE (prints the failing reasons and a Level estimate).
This targets the mini-SGLang serving task but the structure generalizes to other seeds.
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKER = os.path.join(ROOT, "moltbench", "docker", "mini_sglang")
CONFIGS = os.path.join(ROOT, "configs", "examples")


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


def _checks_static() -> list[tuple[bool, str]]:
    """Return [(ok, message)] for the static package admission checks."""
    out: list[tuple[bool, str]] = []

    # 1. Config integrity — both difficulty configs present with required keys.
    for diff in ("easy", "hard"):
        cfg = _read(os.path.join(CONFIGS, f"moltbench_minisglang_{diff}.yaml"))
        ok = bool(cfg) and "task: mini_sglang" in cfg and f'difficulty: "{diff}"' in cfg
        out.append((ok, f"config[{diff}]: present + task/difficulty set"))
        # Attempts terminology (Design-2 §2): prefer max_attempts, not max_rounds.
        out.append(("max_attempts" in cfg,
                    f"config[{diff}]: uses 'max_attempts' (not legacy 'max_rounds')"))
        # Model is pluggable, not hardcoded to one model in code paths.
        out.append(("MODEL_PATH" in cfg or "MODEL:" in cfg,
                    f"config[{diff}]: model is config-driven (MODEL_PATH / MODEL alias)"))
        # GPU is config-driven.
        out.append(("gpus:" in cfg and "gpu_mode:" in cfg,
                    f"config[{diff}]: GPU is config-driven (gpus + gpu_mode)"))
        # Scope declaration (Design-4 §3/§9): optimization_only with S/M/drift = N/A,
        # declared explicitly (not silently omitted).
        out.append(('moltbench_task_level: "optimization_only"' in cfg,
                    f"config[{diff}]: declares moltbench_task_level: optimization_only"))
        out.append(('stability_metric: "N/A"' in cfg and 'memory_metric: "N/A"' in cfg
                    and 'drift_metric: "N/A"' in cfg,
                    f"config[{diff}]: S/M/drift metrics declared N/A (optimization-only)"))

    runner = _read(os.path.join(DOCKER, "eval_runner.py"))
    entry = _read(os.path.join(DOCKER, "run_minisgl.sh"))
    scorer = _read(os.path.join(ROOT, "alphadiana", "benchmarks", "moltbench", "scorer.py"))
    loader = _read(os.path.join(ROOT, "alphadiana", "benchmarks", "moltbench", "benchmark.py"))

    # 0. Scope metadata is emitted by the loader too (Design-4 §2/§3).
    out.append(('"moltbench_task_level": "optimization_only"' in loader,
                "loader: _load_mini_sglang_tasks emits moltbench_task_level=optimization_only"))

    # 2. Hidden-verifier isolation (Design-4 §4 / Design-5 §5): verifier lives in /eval,
    #    agent edits only /workspace/repo. The two must not overlap.
    out.append(("/eval/eval_runner.py" in entry and "/workspace/repo" in entry,
                "isolation: verifier in /eval, agent workspace in /workspace/repo (separate)"))
    out.append(("/eval/run_eval" in (runner + entry) or "run_eval" in entry,
                "isolation: agent contract is /eval/run_eval (hidden harness behind it)"))

    # 3. Anti-hack: baseline is in-situ + not agent-writable, official re-measures clean.
    out.append(("BASELINE_FILE" in runner and "baseline" in runner.lower(),
                "anti-hack: in-situ baseline.json owned by verifier (not agent-written)"))
    out.append(('mode == "official"' in runner and "measure(" in runner,
                "anti-hack: official re-measures candidate from a clean server restart"))

    # 4. Score-parser safety (Design-4 §4/§7 / Design-5 §6): correctness is a hard gate;
    #    score is 0 unless correct; scorer reads verifier results.json, not agent files.
    out.append(("correctness" in scorer and "PASS" in scorer,
                "scoring: correctness is a hard gate (PASS/FAIL/ERROR)"))
    out.append(("if is_correct else 0.0" in scorer or "if correct else 0.0" in runner,
                "scoring: gain is 0 when correctness fails (G gated by C)"))

    # 5. Serving-noise handling (Design-5 §12): warmup exclusion + CI reported.
    bench = _read(os.path.join(DOCKER, "bench_serving.py"))
    out.append(("warmup" in bench and "median" in bench,
                "serving: warmup-excluded steady-state median"))
    out.append(("ci95" in bench,
                "serving: confidence interval reported (not a single number)"))

    # 6. Failure-path logging (Design-4 §10): failed attempts are still recorded.
    out.append(("attempts.jsonl" in runner and runner.count("attempts.jsonl") >= 1,
                "trajectory: attempts.jsonl appended on BOTH success and failure paths"))

    # 7. Trajectory suitcase (Design-1 §11): MoltEvent events + per-attempt file diffs.
    out.append(("events.jsonl" in runner and "_event(" in runner,
                "trajectory: events.jsonl (MoltEvent: run_eval_call/verifier_result/error)"))
    out.append(("file_diffs.jsonl" in runner and "_record_file_diff" in runner,
                "trajectory: per-attempt file_diffs.jsonl (vs pristine seed)"))
    out.append(("collect_artifacts.py" in entry and "model_calls.jsonl" in
                _read(os.path.join(DOCKER, "collect_artifacts.py")),
                "trajectory: model_calls.jsonl collected from agent logs"))

    # 8. K (cost) is a complete vector — tokens + model calls folded in (Design-4 §9).
    out.append(("_cost_vector" in runner and "total_tokens" in runner and "model_calls" in runner,
                "cost (K): attempts + wall_time + tokens/model_calls (not partial)"))

    # 9. ConfigLock in manifest (Design-5 §3) + verifier anti-tamper (Design-5 §5/§6).
    out.append(("config_lock" in entry and "verifier_hashes" in entry and "image_digest" in entry,
                "configlock: manifest records verifier/seed/image/model versions + hashes"))
    out.append(("verifier_integrity" in entry and "TAMPERED" in entry,
                "anti-hack: verifier integrity re-checked; tamper marks run invalid"))

    # 10. Workspace + config snapshots (Design-4 §10).
    out.append(("initial_workspace.tar" in entry and "final_workspace.tar" in entry
                and "task_config.json" in entry,
                "artifacts: initial/final workspace snapshots + task config snapshot"))
    return out


# Always-required, agent-independent artifacts. NOTE: baseline.json is a HIDDEN
# verifier file (lives in STATE_DIR, not the agent-visible artifacts dir) — its
# audit copy in the artifacts dir is baseline_eval.json. attempts.jsonl only exists
# when an agent actually ran (>=1 attempt), so it is checked conditionally below.
REQUIRED_ARTIFACTS = [
    "results.json", "score.json", "manifest.json", "audit_report.md",
    "baseline_eval.json",
    "throughput_metrics.json", "warmup_policy.json", "latency_metrics.json",
    # Trajectory + reproducibility suitcase (Design-1 §11/§14, Design-4 §10).
    "events.jsonl", "file_diffs.jsonl", "model_calls.jsonl",
    "task_config.json", "initial_workspace.tar", "final_workspace.tar",
    "verifier_integrity.json",
]


def _checks_artifacts(d: str) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []
    for name in REQUIRED_ARTIFACTS:
        out.append((os.path.exists(os.path.join(d, name)), f"artifact present: {name}"))

    try:
        r = json.load(open(os.path.join(d, "results.json")))
    except Exception as e:
        out.append((False, f"results.json unreadable ({e})"))
        return out

    # Baseline reproducibility gate (Design-4 §6): the in-situ baseline was measured
    # and propagated (>0). Read it from results.json (sourced from the hidden
    # baseline.json), or fall back to the state/ copy if it was mounted out.
    bt = float(r.get("baseline_throughput", 0))
    if bt <= 0:
        try:
            bt = float(json.load(open(os.path.join(d, "state", "baseline.json")))["baseline_throughput"])
        except Exception:
            pass
    out.append((bt > 0, f"baseline reproducible: baseline_throughput={bt} (>0)"))

    # Correctness gate consistency (Design-2 §13): if not correct, gain must be 0.
    correct = bool(r.get("correct"))
    og = float(r.get("og", r.get("speedup", 0)))
    out.append((correct or og == 0.0,
                f"correctness gate: correct={correct}, og={og} (og==0 when not correct)"))
    out.append(("cost" in r, "cost (K) vector present in results.json"))
    out.append((r.get("throughput_ci95") is not None,
                "throughput CI present in results.json"))

    # K (cost) completeness: token/model-call keys folded in (Design-4 §9).
    cost = r.get("cost", {}) if isinstance(r.get("cost"), dict) else {}
    out.append(("total_tokens" in cost and "model_calls" in cost,
                "cost (K) includes token/model-call keys (not partial)"))

    # Verifier anti-tamper: integrity must be OK and the run valid (Design-5 §5/§6).
    integ = {}
    try:
        integ = json.load(open(os.path.join(d, "verifier_integrity.json")))
    except Exception:
        pass
    out.append((integ.get("status") == "OK",
                f"verifier integrity OK (no tamper); status={integ.get('status','?')}"))
    out.append((r.get("run_valid", True) is not False,
                "run not invalidated (verifier untampered)"))

    # ConfigLock present in manifest (Design-5 §3).
    try:
        man = json.load(open(os.path.join(d, "manifest.json")))
        cl = man.get("config_lock", {})
        out.append((bool(cl.get("seed_commit")) and "verifier_hashes" in cl,
                    "manifest config_lock present (seed_commit + verifier_hashes)"))
    except Exception:
        out.append((False, "manifest.json has config_lock block"))

    # attempts.jsonl is required ONLY when an agent ran (>=1 attempt); a baseline-only
    # smoke (attempts_used=0) legitimately has none.
    attempts_used = int(r.get("attempts_used", 0) or 0)
    if attempts_used > 0:
        out.append((os.path.exists(os.path.join(d, "attempts.jsonl")),
                    f"attempts.jsonl present (attempts_used={attempts_used})"))
    else:
        out.append((True, "attempts.jsonl: n/a (baseline-only smoke, 0 attempts)"))
    return out


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "static"
    if mode == "static":
        checks = _checks_static()
        level_note = ("Level 3 (optimization-only) target. Level 4 additionally needs a "
                      "drift stage + memory-gated stage + paired memory ablation.")
    elif mode == "artifacts":
        if len(sys.argv) < 3:
            print("usage: validate_task.py artifacts <run_artifacts_dir>")
            return 2
        checks = _checks_artifacts(sys.argv[2])
        level_note = "Artifact completeness for a single run."
    else:
        print(f"unknown mode: {mode}  (use 'static' or 'artifacts <dir>')")
        return 2

    failed = [m for ok, m in checks if not ok]
    print(f"# MoltBench task admission — mode={mode}\n")
    for ok, m in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {m}")
    print()
    if failed:
        print(f"INCOMPLETE — {len(failed)} check(s) failed:")
        for m in failed:
            print(f"  - {m}")
        print(f"\nNote: {level_note}")
        return 1
    print(f"PASS — all {len(checks)} checks passed.")
    print(f"Note: {level_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
