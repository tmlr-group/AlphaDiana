#!/usr/bin/env python3
"""
analyze_action_transitions.py — action-level pre-post behavior analysis.

Computes:
- Transition probability matrices per (harness, correct) group
- N-gram motif frequencies (2-gram, 3-gram) with correct vs wrong delta
- Verification context profile (pre/post verify action distributions)
- Action pattern entropy correlation

Reads: results/phase14_gpqa_trajectory_analysis/action_events.csv
Writes: analyze_tools/data/action_transition_data.csv
        analyze_tools/data/action_motif_data.csv
        analyze_tools/data/action_entropy_profile.csv

Run:
    python3 analyze_tools/analyze_action_transitions.py
"""

from __future__ import annotations

import csv
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean, stdev

csv.field_size_limit(sys.maxsize)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "analyze_tools" / "data"
PHASE14_DIR = ROOT / "results" / "phase14_gpqa_trajectory_analysis"

# Import load_gpqa_records from compute_trajectory_stats.py
sys.path.insert(0, str(ROOT / "analyze_tools"))
from compute_trajectory_stats import load_gpqa_records

CANONICAL_ACTIONS = ["reason", "answer", "verify", "plan", "tool_use", "recover"]
HARNESSES = ["directllm", "openclaw", "opencode", "zeroclaw"]
OUTCOME_LABELS = {
    "True": "correct",
    "False": "wrong",
}
OUTCOME_INVERSE = {"correct": "True", "wrong": "False"}

# ─── Helpers ──────────────────────────────────────────────────────────────


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {path} ({len(rows)} rows)")


def safe_int(v: str) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return 999999


# ─── 1a. Load and parse action events ─────────────────────────────────────


def load_trajectories() -> dict:
    """Load action_events.csv and group into trajectories.

    Returns dict keyed by (harness, task_id, sample_index) with:
        events: list[dict] sorted by step_id
        correct: str ("True", "False", or "")
    """
    trajectories: dict = {}
    for row in read_csv(PHASE14_DIR / "action_events.csv"):
        key = (row["harness"], row["task_id"], row["sample_index"])
        if key not in trajectories:
            trajectories[key] = {
                "events": [],
                "correct": row["correct"],
            }
        trajectories[key]["events"].append(row)

    # Sort each trajectory's events by step_id
    for key, traj in trajectories.items():
        traj["events"].sort(key=lambda r: safe_int(r["step_id"]))

    return trajectories


# ─── 1b. Transition probability matrices ──────────────────────────────────

# The correct field uses "True"/"False" string values; also there can be empty string ""
# For the plan: exclude trajectories with empty correct from outcome-split analyses
# but include in aggregate totals.

# We need to handle "all" harness group (aggregate across all harnesses).
# The aggregate is computed per correct group across ALL harnesses merged.


def compute_transitions(trajectories: dict) -> list[dict[str, object]]:
    """Compute transition probability matrices for each (harness, correct) group.

    Returns list of dicts for action_transition_data.csv.
    """
    # Count transitions per (harness, correct, from_action, to_action)
    counts: dict[tuple[str, str, str, str], int] = Counter()  # (harness, correct, from, to) -> count
    from_counts: dict[tuple[str, str, str], int] = Counter()  # (harness, correct, from) -> total_outgoing

    # Also collect per-harness (ignoring correct) for aggregate-all-harnesses
    all_counts: dict[tuple[str, str, str], int] = Counter()  # ("all", correct, from, to) -> count
    all_from_counts: dict[tuple[str, str], int] = Counter()  # ("all", correct, from) -> total_outgoing

    for key, traj in trajectories.items():
        harness, _task_id, _si = key
        correct = traj["correct"]
        events = traj["events"]
        actions = [e["canonical_action"] for e in events]

        if not actions:
            continue

        # Add __START__ pseudo-action
        prev_action = "__START__"

        for action in actions:
            # Count this transition
            counts[(harness, correct, prev_action, action)] += 1
            from_counts[(harness, correct, prev_action)] += 1
            all_counts[("all", correct, prev_action, action)] += 1
            all_from_counts[("all", correct, prev_action)] += 1
            prev_action = action

        # Add __END__ pseudo-action after last action
        last_action = actions[-1]
        counts[(harness, correct, last_action, "__END__")] += 1
        from_counts[(harness, correct, last_action)] += 1
        all_counts[("all", correct, last_action, "__END__")] += 1
        all_from_counts[("all", correct, last_action)] += 1

    # Also compute "all" for empty-correct (include empty-correct trajectories in aggregate)
    # Already handled above since we iterate all trajectories regardless of correct value.

    rows = []
    # Generate rows for each (harness, correct) group
    group_keys = set()
    for (h, c, f, t) in counts:
        group_keys.add((h, c))
    for (h, c, f, t) in all_counts:
        group_keys.add((h, c))  # h is "all" here

    for (h, c) in sorted(group_keys):
        # Build the transition matrix
        from_actions = sorted(set(
            k[2] for k in (counts if h != "all" else all_counts)
            if k[0] == h and k[1] == c
        ) | set(
            k[2] for k in (counts if h != "all" else all_counts)
            if k[0] == h and k[1] == c
        ))

        tot_source = from_counts if h != "all" else all_from_counts
        cnt = counts if h != "all" else all_counts

        for from_action in sorted(CANONICAL_ACTIONS + ["__START__", "__END__"]):
            total_out = tot_source.get((h, c, from_action), 0)
            if total_out == 0:
                continue
            for to_action in sorted(CANONICAL_ACTIONS + ["__START__", "__END__"]):
                n = cnt.get((h, c, from_action, to_action), 0)
                if n == 0:
                    continue
                prob = n / total_out if total_out > 0 else 0.0
                rows.append({
                    "harness": h,
                    "correct": c,
                    "from_action": from_action,
                    "to_action": to_action,
                    "n_transitions": n,
                    "probability": round(prob, 4),
                    "total_from_source": total_out,
                })

    print(f"  Transitions: {len(rows)} rows across {len(group_keys)} groups")
    return rows


# ─── 1c. N-gram motif frequencies ─────────────────────────────────────────


def compute_motifs(trajectories: dict) -> list[dict[str, object]]:
    """Compute 2-gram and 3-gram motif frequencies with correct_vs_wrong_delta.

    Returns list of dicts for action_motif_data.csv.
    """
    motif_counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    # motif_counts[harness][motif][correct_label] = count
    traj_counts: dict = defaultdict(lambda: defaultdict(int))
    # traj_counts[harness][correct_label] = total_trajectories

    for key, traj in trajectories.items():
        harness, _task_id, _si = key
        correct = traj["correct"]
        events = traj["events"]
        actions = [e["canonical_action"] for e in events]
        n_actions = len(actions)

        traj_counts[harness][correct] += 1

        # 2-grams (including transitions from start and to end? No — n-grams are internal only)
        if n_actions >= 2:
            for i in range(n_actions - 1):
                motif = f"{actions[i]}->{actions[i+1]}"
                motif_counts[harness][motif][correct] += 1

        # 3-grams
        if n_actions >= 3:
            for i in range(n_actions - 2):
                motif = f"{actions[i]}->{actions[i+1]}->{actions[i+2]}"
                motif_counts[harness][motif][correct] += 1

    # Build rows
    rows = []
    for harness in sorted(motif_counts):
        all_correct_vals = set()
        for motif_data in motif_counts[harness].values():
            all_correct_vals |= set(motif_data.keys())

        for motif in sorted(motif_counts[harness]):
            motif_data = motif_counts[harness][motif]

            for correct_label in sorted(motif_data):
                n = motif_data[correct_label]
                total_traj = traj_counts[harness][correct_label]
                rate = n / total_traj if total_traj > 0 else 0.0

                row = {
                    "harness": harness,
                    "correct": correct_label,
                    "motif": motif,
                    "n": n,
                    "total_trajectories": total_traj,
                    "rate_per_trajectory": round(rate, 4),
                }

                # Also include ngram length info
                n_ary = len(motif.split("->"))
                row["ngram"] = n_ary

                rows.append(row)

    # Compute correct_vs_wrong_delta for matching motifs
    # Group by (harness, motif) and add delta
    deltas: dict[tuple[str, str], float] = {}
    for row in rows:
        key = (row["harness"], row["motif"])
        if key not in deltas:
            deltas[key] = 0.0
        if row["correct"] == "True":
            # Store correct rate (we'll compute delta later)
            deltas[key] = deltas.get(key, 0) - row["rate_per_trajectory"]
        elif row["correct"] == "False":
            deltas[key] = deltas.get(key, 0) + row["rate_per_trajectory"]

    for row in rows:
        key = (row["harness"], row["motif"])
        row["correct_vs_wrong_delta"] = round(deltas.get(key, 0.0), 4)

    return rows


# ─── 1d. Verification context profile ─────────────────────────────────────


def compute_verify_context(trajectories: dict) -> list[dict[str, object]]:
    """Extract verification context profile.

    Returns list of dicts with type=verify_context for action_motif_data.csv.
    """
    rows = []
    pre_verify: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    # pre_verify[harness][correct][preceding_action] = count
    post_verify: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    # post_verify[harness][correct][following_action] = count
    verify_counts: dict = defaultdict(lambda: defaultdict(int))
    # verify_counts[harness][correct] = total verify events

    terminal_verify: dict = defaultdict(lambda: defaultdict(int))
    # terminal_verify[harness][correct] = count where verify is last real action before __END__

    follow_recover: dict = defaultdict(lambda: defaultdict(int))  # verify->recover
    follow_tool_use: dict = defaultdict(lambda: defaultdict(int))  # verify->tool_use
    follow_answer: dict = defaultdict(lambda: defaultdict(int))  # verify->answer

    for key, traj in trajectories.items():
        harness, _task_id, _si = key
        correct = traj["correct"]
        events = traj["events"]
        actions = [e["canonical_action"] for e in events]
        n = len(actions)

        for i, action in enumerate(actions):
            if action != "verify":
                continue

            verify_counts[harness][correct] += 1

            # Preceding action (or __START__ if first)
            if i == 0:
                pre_verify[harness][correct]["__START__"] += 1
            else:
                pre_verify[harness][correct][actions[i - 1]] += 1

            # Following action (or __END__ if last)
            if i == n - 1:
                post_verify[harness][correct]["__END__"] += 1
            else:
                post_verify[harness][correct][actions[i + 1]] += 1

            # Terminal verify (last real action before __END__)
            if i == n - 1:
                terminal_verify[harness][correct] += 1

            # Verify -> specific transitions
            if i < n - 1:
                next_action = actions[i + 1]
                if next_action == "recover":
                    follow_recover[harness][correct] += 1
                if next_action == "tool_use":
                    follow_tool_use[harness][correct] += 1
                if next_action == "answer":
                    follow_answer[harness][correct] += 1

    # Write rows
    for harness in sorted(verify_counts):
        for correct in sorted(verify_counts[harness]):
            total = verify_counts[harness][correct]
            if total == 0:
                continue

            # Pre-verify distribution
            for pre_action in sorted(pre_verify[harness][correct]):
                count = pre_verify[harness][correct][pre_action]
                rows.append({
                    "harness": harness,
                    "correct": correct,
                    "type": "verify_context",
                    "context": "pre_verify",
                    "action": pre_action,
                    "n": count,
                    "fraction": round(count / total, 4),
                    "total_verify_events": total,
                })

            # Post-verify distribution
            for post_action in sorted(post_verify[harness][correct]):
                count = post_verify[harness][correct][post_action]
                rows.append({
                    "harness": harness,
                    "correct": correct,
                    "type": "verify_context",
                    "context": "post_verify",
                    "action": post_action,
                    "n": count,
                    "fraction": round(count / total, 4),
                    "total_verify_events": total,
                })

            # Terminal verify rate
            term_count = terminal_verify[harness][correct]
            rows.append({
                "harness": harness,
                "correct": correct,
                "type": "verify_context",
                "context": "terminal_verify",
                "action": "__END__",
                "n": term_count,
                "fraction": round(term_count / total, 4) if total > 0 else 0.0,
                "total_verify_events": total,
            })

            # Verify->recover rate
            rec_count = follow_recover[harness][correct]
            rows.append({
                "harness": harness,
                "correct": correct,
                "type": "verify_context",
                "context": "verify_to_recover",
                "action": "recover",
                "n": rec_count,
                "fraction": round(rec_count / total, 4) if total > 0 else 0.0,
                "total_verify_events": total,
            })

            # Verify->tool_use rate
            tu_count = follow_tool_use[harness][correct]
            rows.append({
                "harness": harness,
                "correct": correct,
                "type": "verify_context",
                "context": "verify_to_tool_use",
                "action": "tool_use",
                "n": tu_count,
                "fraction": round(tu_count / total, 4) if total > 0 else 0.0,
                "total_verify_events": total,
            })

            # Verify->answer rate
            ans_count = follow_answer[harness][correct]
            rows.append({
                "harness": harness,
                "correct": correct,
                "type": "verify_context",
                "context": "verify_to_answer",
                "action": "answer",
                "n": ans_count,
                "fraction": round(ans_count / total, 4) if total > 0 else 0.0,
                "total_verify_events": total,
            })

    return rows


# ─── 1e. Action pattern entropy correlation ───────────────────────────────


def classify_trajectory_patterns(actions: list[str]) -> dict[str, bool]:
    """Classify a trajectory into action-pattern categories."""
    n = len(actions)
    action_set = set(actions)

    patterns = {
        "has_verify": "verify" in action_set,
        "verify_before_answer": False,
        "verify_after_answer": False,
        "has_plan": "plan" in action_set,
        "has_recover": "recover" in action_set,
        "has_tool_use": "tool_use" in action_set,
        "recover_cycle": False,
        "plan_tool_use": False,
        "sustained_reason": False,
    }

    # Check verify timing relative to answer
    verify_positions = [i for i, a in enumerate(actions) if a == "verify"]
    answer_positions = [i for i, a in enumerate(actions) if a == "answer"]

    if verify_positions and answer_positions:
        first_verify = verify_positions[0]
        first_answer = answer_positions[0]
        # If there is an answer before any verify
        if any(ap < first_verify for ap in answer_positions):
            patterns["verify_after_answer"] = True
        else:
            patterns["verify_before_answer"] = True

    # Recover cycle: recover appears 2+ times consecutively or in close succession
    if "recover" in action_set:
        prev_was_recover = False
        for a in actions:
            if a == "recover":
                if prev_was_recover:
                    patterns["recover_cycle"] = True
                    break
                prev_was_recover = True
            else:
                prev_was_recover = False

    # plan immediately before tool_use
    for i in range(n - 1):
        if actions[i] == "plan" and actions[i + 1] == "tool_use":
            patterns["plan_tool_use"] = True
            break

    # 3+ consecutive reason actions
    consecutive = 0
    for a in actions:
        if a == "reason":
            consecutive += 1
            if consecutive >= 3:
                patterns["sustained_reason"] = True
                break
        else:
            consecutive = 0

    return patterns


def compute_entropy_correlation(trajectories: dict) -> list[dict[str, object]]:
    """Cross-reference action patterns with per-task entropy.

    Returns list of dicts for action_entropy_profile.csv.
    """
    # Load per-trajectory entropy from task records
    harness_entropy: dict[str, dict[str, dict]] = {}  # harness -> {task_id: {mean, correct}}
    for harness in HARNESSES:
        try:
            records = load_gpqa_records(harness)
            ent_data: dict = {}
            for tid, rec in records.items():
                tes = rec.get("token_entropy_stats")
                if tes and tes.get("mean") is not None:
                    ent_data[tid] = {
                        "mean_entropy": float(tes["mean"]),
                        "n_tokens": int(tes.get("n_tokens", 0)),
                        "correct": rec.get("correct", False),
                    }
            harness_entropy[harness] = ent_data
            print(f"    Loaded entropy for {harness}: {len(ent_data)} records")
        except Exception as e:
            print(f"    Warning: entropy load failed for {harness}: {e}")
            harness_entropy[harness] = {}

    # Build action pattern -> trajectory_list mapping
    # For each (harness, correct, pattern), collect mean_entropy values
    pattern_entropies: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    # pattern_entropies[harness][correct][pattern] = [entropy_values]

    # Also per-action-type entropy: for each harness x outcome, mean entropy of
    # trajectories that ever contain each action type
    action_type_entropies: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    # action_type_entropies[harness][correct][action_type] = [entropy_values]

    for key, traj in trajectories.items():
        harness, task_id, _si = key
        correct = traj["correct"]
        events = traj["events"]
        actions = [e["canonical_action"] for e in events]

        # Look up entropy for this task
        ent_info = harness_entropy.get(harness, {}).get(task_id)
        if ent_info is None:
            continue

        mean_entropy = ent_info["mean_entropy"]

        # Classify patterns
        patterns = classify_trajectory_patterns(actions)

        for pattern_name, has_pattern in patterns.items():
            if has_pattern:
                pattern_entropies[harness][correct][pattern_name].append(mean_entropy)

        # Per-action-type entropy: which action types appear in this trajectory
        action_set = set(actions)
        for action_type in CANONICAL_ACTIONS:
            if action_type in action_set:
                action_type_entropies[harness][correct][action_type].append(mean_entropy)

    # Write rows for patterns
    rows = []
    for harness in sorted(pattern_entropies):
        for correct in sorted(pattern_entropies[harness]):
            for pattern_name in sorted(pattern_entropies[harness][correct]):
                values = pattern_entropies[harness][correct][pattern_name]
                if len(values) < 1:
                    continue
                mean_h = fmean(values)
                std_h = stdev(values) if len(values) >= 2 else 0.0
                rows.append({
                    "harness": harness,
                    "correct": correct,
                    "action_pattern": pattern_name,
                    "mean_entropy": round(mean_h, 4),
                    "std_entropy": round(std_h, 4),
                    "n_trajectories": len(values),
                    "min_entropy": round(min(values), 4),
                    "max_entropy": round(max(values), 4),
                })

    # Write rows for per-action-type entropy
    for harness in sorted(action_type_entropies):
        for correct in sorted(action_type_entropies[harness]):
            for action_type in sorted(action_type_entropies[harness][correct]):
                values = action_type_entropies[harness][correct][action_type]
                if len(values) < 1:
                    continue
                mean_h = fmean(values)
                std_h = stdev(values) if len(values) >= 2 else 0.0
                rows.append({
                    "harness": harness,
                    "correct": correct,
                    "action_pattern": f"contains_{action_type}",
                    "mean_entropy": round(mean_h, 4),
                    "std_entropy": round(std_h, 4),
                    "n_trajectories": len(values),
                    "min_entropy": round(min(values), 4),
                    "max_entropy": round(max(values), 4),
                })

    return rows


# ─── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    print("Analyzing action transitions...")

    # 1a. Load trajectories
    print("\n1a. Loading trajectories...")
    trajectories = load_trajectories()
    total = len(trajectories)
    with_outcome = sum(1 for t in trajectories.values() if t["correct"] in ("True", "False"))
    empty_correct = total - with_outcome
    print(f"    {total} trajectories loaded ({with_outcome} with outcome, {empty_correct} empty correct)")

    # 1b. Transition matrices
    print("\n1b. Computing transition probability matrices...")
    transition_rows = compute_transitions(trajectories)
    write_csv(
        DATA_DIR / "action_transition_data.csv",
        transition_rows,
        ["harness", "correct", "from_action", "to_action", "n_transitions", "probability", "total_from_source"],
    )

    # 1c. Motif frequencies
    print("\n1c. Computing n-gram motif frequencies...")
    motif_rows = compute_motifs(trajectories)
    verify_context_rows = compute_verify_context(trajectories)

    # Combine motifs and verify context into one CSV
    all_motif_fields = ["harness", "correct", "type", "motif", "ngram", "n", "total_trajectories",
                        "rate_per_trajectory", "correct_vs_wrong_delta"]
    verify_fields = ["harness", "correct", "type", "context", "action", "n", "fraction", "total_verify_events"]

    # Write combined CSV with a field set that covers both
    combined_rows = []
    for r in motif_rows:
        combined_rows.append({
            "harness": r["harness"],
            "correct": r["correct"],
            "type": "motif",
            "motif": r["motif"],
            "ngram": r["ngram"],
            "n": r["n"],
            "total_trajectories": r["total_trajectories"],
            "rate_per_trajectory": r["rate_per_trajectory"],
            "correct_vs_wrong_delta": r["correct_vs_wrong_delta"],
            "context": "",
            "action": "",
            "fraction": "",
            "total_verify_events": "",
        })
    for r in verify_context_rows:
        combined_rows.append({
            "harness": r["harness"],
            "correct": r["correct"],
            "type": "verify_context",
            "motif": "",
            "ngram": "",
            "n": r["n"],
            "total_trajectories": "",
            "rate_per_trajectory": "",
            "correct_vs_wrong_delta": "",
            "context": r["context"],
            "action": r["action"],
            "fraction": r["fraction"],
            "total_verify_events": r["total_verify_events"],
        })

    write_csv(
        DATA_DIR / "action_motif_data.csv",
        combined_rows,
        ["harness", "correct", "type", "motif", "ngram", "n", "total_trajectories",
         "rate_per_trajectory", "correct_vs_wrong_delta", "context", "action",
         "fraction", "total_verify_events"],
    )

    # 1e. Entropy correlation
    print("\n1e. Computing action pattern entropy correlation...")
    entropy_rows = compute_entropy_correlation(trajectories)
    write_csv(
        DATA_DIR / "action_entropy_profile.csv",
        entropy_rows,
        ["harness", "correct", "action_pattern", "mean_entropy", "std_entropy",
         "n_trajectories", "min_entropy", "max_entropy"],
    )

    # Summary stats for stdout
    print("\n─── Summary Stats ───")
    # Aggregate transitions to match preliminary findings in plan
    agg_transitions = defaultdict(lambda: defaultdict(lambda: Counter()))
    agg_from = defaultdict(lambda: defaultdict(lambda: Counter()))
    for r in transition_rows:
        if r["harness"] == "all":
            h_key = "all"
        else:
            h_key = r["harness"]
        c_key = r["correct"]
        f_a = r["from_action"]
        t_a = r["to_action"]
        n = r["n_transitions"]
        agg_transitions[h_key][c_key][(f_a, t_a)] += n
        agg_from[h_key][c_key][f_a] += n

    # Show top transitions for all-harness correct vs wrong
    for h_key in ["all"]:
        for c_key in ["True", "False"]:
            total_from = agg_from[h_key][c_key]
            total_from_computed = defaultdict(int)
            for (f, t), n in agg_transitions[h_key][c_key].items():
                total_from_computed[f] += n
            print(f"\n  {h_key} / {OUTCOME_LABELS.get(c_key, c_key)}:")
            top_transitions = sorted(
                [(f, t, n, n / total_from_computed[f] if total_from_computed[f] > 0 else 0)
                 for (f, t), n in agg_transitions[h_key][c_key].items()],
                key=lambda x: -x[2]
            )[:10]
            for f, t, n, p in top_transitions:
                print(f"    {f:>10} -> {t:<10}: {n:4d} ({p:.1%})")

    # Show motif deltas
    print("\n  Top correct-vs-wrong deltas (all harness):")
    motif_deltas = defaultdict(lambda: {"correct_rate": 0.0, "wrong_rate": 0.0, "correct_n": 0, "wrong_n": 0})
    for r in motif_rows:
        if r["harness"] == "all" and r["ngram"] >= 2 and len(r["motif"].split("->")) == 3:
            key = r["motif"]
            if r["correct"] == "True":
                motif_deltas[key]["correct_rate"] = r["rate_per_trajectory"]
                motif_deltas[key]["correct_n"] = r["n"]
            elif r["correct"] == "False":
                motif_deltas[key]["wrong_rate"] = r["rate_per_trajectory"]
                motif_deltas[key]["wrong_n"] = r["n"]

    sorted_deltas = sorted(
        [(k, v["wrong_rate"] - v["correct_rate"], v["wrong_rate"], v["correct_rate"],
          v["wrong_n"], v["correct_n"])
         for k, v in motif_deltas.items()],
        key=lambda x: -abs(x[1])
    )[:10]
    for motif, delta, wrong_r, correct_r, w_n, c_n in sorted_deltas:
        print(f"    {motif:<40} delta={delta:+.4f} (correct={correct_r:.4f} n={c_n}, wrong={wrong_r:.4f} n={w_n})")

    total_entropy = sum(1 for r in entropy_rows if r["action_pattern"].startswith("contains_"))
    print(f"\n  Entropy profile rows: {len(entropy_rows)} (including {total_entropy} per-action-type)")
    print("\nDone.")


if __name__ == "__main__":
    main()
