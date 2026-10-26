"""GPQA trajectory report renderers and artifact writers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def render_latex_appendix(
    inventory: Mapping[str, Any],
    metrics: Mapping[str, Any],
    case_studies: Sequence[Mapping[str, Any]],
    *,
    output_files: Mapping[str, Path | str],
) -> str:
    """Render the English trajectory appendix."""
    corpus_rows = _inventory_corpora(inventory)
    lines = [
        r"\section{Harness-Induced Action Ecology}",
        "",
        "This appendix analyzes GPQA-Diamond Qwen3.5 trajectories across DirectLLM, OpenClaw, OpenCode, and ZeroClaw using persisted ResultStore artifacts only.",
        "DirectLLM is a non-agentic direct baseline: it contributes reasoning and answer rows, but no tool-use, verification, or recovery claims are inferred beyond its stored text.",
        "",
        r"\subsection{Action-Intent Grammar}",
        "",
        r"The canonical action grammar is \(A = \{plan, reason, tool\_use, verify, recover, answer\}\).",
        "Harness-specific operations are projected into one non-overlapping canonical action per event; search, shell, python, and browser usage remain tool-subtype annotations.",
        "",
        r"\subsection{Extraction Protocol}",
        "",
        "Rows are extracted offline from task JSONs, JSONL records, normalized trace artifacts, trajectory fields, and raw text fallbacks.",
        "Duplicate task/sample records are normalized before extraction, preferring scorer-aware valid-scored records when available.",
        "",
        r"\subsection{Outcome-Conditioned Macro Analysis}",
        "",
        _latex_corpus_table(corpus_rows),
        "",
        _latex_metrics_summary(metrics),
        "",
        r"\subsection{Micro Trajectory Case Studies}",
        "",
        _latex_case_studies(case_studies),
        "",
        r"\subsection{Metrics and Control Motifs}",
        "",
        "The report emphasizes outcome-conditioned action allocation, answer-after-verification, error-recovery, premature-answer, motif-outcome lift, and failure-cost ratios.",
        "Pooled action distributions are diagnostics rather than headline claims.",
        "",
        r"\subsection{Analyze Tools Measurement Insights}",
        "",
        _latex_measurement_insights(metrics),
        "",
        r"\subsection{Caveats}",
        "",
        r"Backup artifacts under \texttt{results/bkp\_gpqa\_20260425/} are excluded.",
        r"\(pass^k\) is not claimed unless repeated independent samples exist.",
        "Behavioral contrasts are outcome-conditioned over valid-scored records; operational failures are reported separately as readiness and failure-cost evidence.",
        "",
        r"\subsection{Generated Artifacts}",
        "",
        _latex_output_files(output_files),
        "",
    ]
    return "\n".join(lines)


def render_trajectory_discussion(
    inventory: Mapping[str, Any],
    metrics: Mapping[str, Any],
    case_studies: Sequence[Mapping[str, Any]],
    *,
    output_files: Mapping[str, Path | str],
) -> str:
    """Render the English trajectory discussion."""
    lines = [
        "# GPQA-Diamond Qwen3.5 Cross-Harness Trajectory Discussion",
        "",
        "This note discusses the action ecology of DirectLLM, OpenClaw, OpenCode, and ZeroClaw on GPQA-Diamond. Analysis reads persisted results without rerunning models or harnesses.",
        "",
        "## Action-Intent Grammar",
        "",
        "The canonical action grammar is `A = {plan, reason, tool_use, verify, recover, answer}`. `search/shell/python/browser` are `tool_subtype` annotations; each event belongs to one canonical action.",
        "DirectLLM is a non-agentic direct-answer baseline and contributes only reason/answer trajectories to the comparison.",
        "",
        "## Macro Results",
        "",
        _markdown_corpus_table(_inventory_corpora(inventory)),
        "",
        _markdown_metrics_summary(metrics),
        "",
        "## Metric Definitions",
        "",
        _markdown_metric_definitions(),
        "",
        "## Metric Analysis",
        "",
        _markdown_metric_analysis(metrics),
        "",
        "## analyze_tools measurement insights",
        "",
        _markdown_measurement_insights(metrics),
        "",
        "## Micro Case Studies",
        "",
        _markdown_case_studies(case_studies),
        "",
        "## Caveats",
        "",
        "- Backup artifacts under `results/bkp_gpqa_20260425/` are excluded.",
        "- `pass^k` is reported only when repeated independent samples exist.",
        "- Correct/wrong behavior contrasts use `valid_scored` records; runtime errors are reported separately as failure-cost and readiness evidence.",
        "",
        "## Generated Artifacts",
        "",
        _markdown_output_files(output_files),
        "",
    ]
    return "\n".join(lines)


def write_trajectory_outputs(
    output_dir: Path,
    *,
    inventory: Mapping[str, Any],
    event_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    case_studies: Sequence[Mapping[str, Any]],
) -> dict[str, Path]:
    """Write trajectory data, metrics, case studies, and reports."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "corpus_inventory": output_dir / "corpus_inventory.json",
        "action_events": output_dir / "action_events.csv",
        "trajectory_metrics": output_dir / "trajectory_metrics.json",
        "case_studies": output_dir / "case_studies.json",
        "latex_appendix": output_dir / "gpqa_trajectory_appendix.tex",
        "discussion": output_dir / "gpqa_trajectory_discussion.md",
    }
    output_refs = {name: path for name, path in paths.items()}
    latex = render_latex_appendix(
        inventory, metrics, case_studies, output_files=output_refs
    )
    discussion = render_trajectory_discussion(
        inventory, metrics, case_studies, output_files=output_refs
    )

    _write_json(_jsonable(inventory), paths["corpus_inventory"])
    _write_csv(event_rows, paths["action_events"])
    _write_json(_jsonable(metrics), paths["trajectory_metrics"])
    _write_json({"case_studies": _jsonable(list(case_studies))}, paths["case_studies"])
    paths["latex_appendix"].write_text(latex, encoding="utf-8")
    paths["discussion"].write_text(discussion, encoding="utf-8")
    return paths


def _inventory_corpora(inventory: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    corpora = inventory.get("corpora")
    if isinstance(corpora, list):
        return [row for row in corpora if isinstance(row, Mapping)]
    return []


def _latex_corpus_table(corpus_rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        r"\begin{tabular}{llll}",
        r"Harness & Run ID & Expected & Valid-scored \\",
        r"\hline",
    ]
    for row in corpus_rows:
        valid_scored = _status_count(row, "valid_scored")
        lines.append(
            f"{_tex(row.get('harness'))} & {_tex(row.get('run_id'))} & {int(row.get('expected_sample_count') or 0)} & {valid_scored} \\\\"
        )
    lines.append(r"\end{tabular}")
    return "\n".join(lines)


def _latex_metrics_summary(metrics: Mapping[str, Any]) -> str:
    names = ", ".join(
        f"\\texttt{{{_tex(name)}}}" for name in metrics.get("metric_names", [])
    )
    diagnostics = (
        metrics.get("diagnostics", {})
        if isinstance(metrics.get("diagnostics"), Mapping)
        else {}
    )
    return "\n".join(
        [
            f"Metric families: {names}.",
            f"Sequence count: {int(diagnostics.get('sequence_count') or 0)}; valid-scored sequence count: {int(diagnostics.get('valid_scored_sequence_count') or 0)}.",
        ]
    )


def _latex_case_studies(case_studies: Sequence[Mapping[str, Any]]) -> str:
    if not case_studies:
        return "No case-study anchors were selected."
    lines = [r"\begin{itemize}"]
    for case in case_studies[:8]:
        motifs = ", ".join(str(motif) for motif in case.get("motifs", [])) or "none"
        lines.append(
            rf"\item \texttt{{{_tex(case.get('harness'))}}} / \texttt{{{_tex(case.get('task_id'))}}}: "
            rf"score={_tex(case.get('score_status'))}, correct={_tex(case.get('correct'))}, motifs={_tex(motifs)}."
        )
    lines.append(r"\end{itemize}")
    return "\n".join(lines)


def _latex_output_files(output_files: Mapping[str, Path | str]) -> str:
    return "\n".join(
        rf"- \texttt{{{_tex(name)}}}: \texttt{{{_tex(_path_text(path))}}}"
        for name, path in sorted(output_files.items())
    )


def _markdown_corpus_table(corpus_rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| Harness | Run ID | Expected | Valid-scored |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in corpus_rows:
        lines.append(
            f"| {row.get('harness', '')} | `{row.get('run_id', '')}` | {int(row.get('expected_sample_count') or 0)} | {_status_count(row, 'valid_scored')} |"
        )
    return "\n".join(lines)


def _markdown_metrics_summary(metrics: Mapping[str, Any]) -> str:
    diagnostics = (
        metrics.get("diagnostics", {})
        if isinstance(metrics.get("diagnostics"), Mapping)
        else {}
    )
    names = ", ".join(f"`{name}`" for name in metrics.get("metric_names", []))
    return "\n".join(
        [
            f"- Metric families: {names}",
            f"- Sequences: {int(diagnostics.get('sequence_count') or 0)}; valid-scored sequences: {int(diagnostics.get('valid_scored_sequence_count') or 0)}",
        ]
    )


def _markdown_metric_definitions() -> str:
    return "\n".join(
        [
            "| Metric | Definition | Denominator / Scope | Interpretation |",
            "| --- | --- | --- | --- |",
            "| `DeltaVerifyShare` | Correct-trajectory `verify` action share minus wrong-trajectory share. | Per-harness `valid_scored` sequences with boolean `correct`; action shares use event counts. | Positive values indicate more explicit verification in correct trajectories; negative values indicate a higher verification share in wrong trajectories or verification without a correct answer. |",
            "| `DeltaToolUseShare` | Correct-trajectory `tool_use` action share minus wrong-trajectory share. | Same scope, restricted to canonical `tool_use` events. | Positive values indicate a higher tool-use share in correct trajectories; zero indicates no tool events or equal shares. |",
            "| `AnswerAfterVerificationRate` | Whether a sequence contains `verify` before `answer`. | Rates are computed separately for correct and wrong valid-scored trajectories. | Measures explicit verification before answering; the report also gives the correct/wrong rate difference. |",
            "| `ErrorRecoveryRate` | Whether `recover` follows a failed, erroneous, timed-out, or truncated observation. | Rates are computed separately for correct and wrong valid-scored trajectories. | Measures recovery after an error observation; the rate is zero when no samples trigger recovery. |",
            "| `PrematureAnswerRate` | No `verify` before the first `answer`. | Rates are computed separately for correct and wrong valid-scored trajectories. | Measures answering before verification; the direct-answer DirectLLM baseline naturally has a high rate. |",
            "| `MotifOutcomeLift` | `P(correct | motif)` minus `P(correct | no motif)`. | All valid-scored trajectories; each motif is computed independently. | Positive values indicate association with higher accuracy, rather than a causal effect. |",
            "| `FailureCostRatio` | `error_records / valid_scored`. | All sequences per harness and error-status records in the inventory. | Measures availability cost; runtime errors remain separate from correct/wrong behavior comparisons. |",
        ]
    )


def _markdown_metric_analysis(metrics: Mapping[str, Any]) -> str:
    return "\n\n".join(
        section
        for section in (
            _markdown_action_delta_table(metrics),
            _markdown_motif_rate_table(metrics),
            _markdown_motif_lift_table(metrics),
            _markdown_failure_cost_table(metrics),
            _markdown_pooled_distribution_table(metrics),
            _markdown_metric_takeaways(metrics),
        )
        if section
    )


def _markdown_action_delta_table(metrics: Mapping[str, Any]) -> str:
    rows = metrics.get("action_allocation")
    if not isinstance(rows, list):
        return ""
    selected = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("canonical_action") in {"verify", "tool_use"}
    ]
    if not selected:
        return ""
    lines = [
        "### Correct/Wrong Action Share Differences",
        "",
        "| Harness | Action | Correct share | Incorrect share | Delta |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in selected:
        lines.append(
            "| {harness} | `{action}` | {correct} | {incorrect} | {delta} |".format(
                harness=row.get("harness", ""),
                action=row.get("canonical_action", ""),
                correct=_pct(row.get("correct_share")),
                incorrect=_pct(row.get("incorrect_share")),
                delta=_signed_pct(row.get("delta_correct_minus_incorrect")),
            )
        )
    return "\n".join(lines)


def _markdown_motif_rate_table(metrics: Mapping[str, Any]) -> str:
    rows = metrics.get("motif_metrics")
    if not isinstance(rows, list) or not rows:
        return ""
    lines = [
        "### Motif Rates in Correct/Wrong Trajectories",
        "",
        "| Metric | Motif | Correct rate | Incorrect rate | Delta | N(correct/incorrect) |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        lines.append(
            "| `{metric}` | `{motif}` | {correct} | {incorrect} | {delta} | {correct_n}/{incorrect_n} |".format(
                metric=row.get("metric_name", ""),
                motif=row.get("motif", ""),
                correct=_pct(row.get("correct")),
                incorrect=_pct(row.get("incorrect")),
                delta=_signed_pct(row.get("delta_correct_minus_incorrect")),
                correct_n=int(row.get("correct_n") or 0),
                incorrect_n=int(row.get("incorrect_n") or 0),
            )
        )
    return "\n".join(lines)


def _markdown_motif_lift_table(metrics: Mapping[str, Any]) -> str:
    rows = metrics.get("motif_outcome_lift")
    if not isinstance(rows, list) or not rows:
        return ""
    lines = [
        "### Motif outcome lift",
        "",
        "| Motif | P(correct | motif) | P(correct | no motif) | Lift | N(with/without) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        lines.append(
            "| `{motif}` | {with_rate} | {without_rate} | {lift} | {with_n}/{without_n} |".format(
                motif=row.get("motif", ""),
                with_rate=_pct(row.get("p_correct_with_motif")),
                without_rate=_pct(row.get("p_correct_without_motif")),
                lift=_signed_pct(row.get("lift")),
                with_n=int(row.get("with_motif_n") or 0),
                without_n=int(row.get("without_motif_n") or 0),
            )
        )
    return "\n".join(lines)


def _markdown_failure_cost_table(metrics: Mapping[str, Any]) -> str:
    rows = metrics.get("failure_cost")
    if not isinstance(rows, list) or not rows:
        return ""
    lines = [
        "### Failure-cost / readiness",
        "",
        "| Harness | Valid-scored | Error records | FailureCostRatio | Error breakdown |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        breakdown = ", ".join(
            f"{status}={int(row.get(status) or 0)}"
            for status in (
                "agent_error",
                "provider_error",
                "runtime_error",
                "scorer_error",
            )
        )
        lines.append(
            "| {harness} | {valid} | {errors} | {ratio} | {breakdown} |".format(
                harness=row.get("harness", ""),
                valid=int(row.get("valid_scored") or 0),
                errors=int(row.get("error_records") or 0),
                ratio=_pct(row.get("failure_cost_ratio")),
                breakdown=breakdown,
            )
        )
    return "\n".join(lines)


def _markdown_pooled_distribution_table(metrics: Mapping[str, Any]) -> str:
    diagnostics = (
        metrics.get("diagnostics", {})
        if isinstance(metrics.get("diagnostics"), Mapping)
        else {}
    )
    rows = diagnostics.get("pooled_action_distribution")
    if not isinstance(rows, list) or not rows:
        return ""
    lines = [
        "### Diagnostic Pooled Action Distribution",
        "",
        "This table pools all sequences across harnesses to inspect the overall action ecology as a diagnostic.",
        "",
        "| Action | Events | Share |",
        "| --- | ---: | ---: |",
    ]
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        lines.append(
            f"| `{row.get('canonical_action', '')}` | {int(row.get('n') or 0)} | {_pct(row.get('share'))} |"
        )
    return "\n".join(lines)


def _markdown_metric_takeaways(metrics: Mapping[str, Any]) -> str:
    action_rows = metrics.get("action_allocation")
    motif_rows = metrics.get("motif_metrics")
    lift_rows = metrics.get("motif_outcome_lift")
    failure_rows = metrics.get("failure_cost")
    lines = ["### Interpretation Notes", ""]

    tool_rows = [
        row
        for row in (action_rows if isinstance(action_rows, list) else [])
        if isinstance(row, Mapping) and row.get("canonical_action") == "tool_use"
    ]
    if tool_rows:
        strongest_tool = max(
            tool_rows,
            key=lambda row: float(row.get("delta_correct_minus_incorrect") or 0.0),
        )
        lines.append(
            "- `{}` has the largest `DeltaToolUseShare` ({}), indicating a higher tool-use share in its correct trajectories.".format(
                strongest_tool.get("harness", ""),
                _signed_pct(strongest_tool.get("delta_correct_minus_incorrect")),
            )
        )

    verify_rows = [
        row
        for row in (action_rows if isinstance(action_rows, list) else [])
        if isinstance(row, Mapping) and row.get("canonical_action") == "verify"
    ]
    if verify_rows:
        strongest_verify = max(
            verify_rows,
            key=lambda row: float(row.get("delta_correct_minus_incorrect") or 0.0),
        )
        weakest_verify = min(
            verify_rows,
            key=lambda row: float(row.get("delta_correct_minus_incorrect") or 0.0),
        )
        lines.append(
            "- `DeltaVerifyShare` ranges from `{}` ({}) to `{}` ({}); negative values indicate more verification in wrong trajectories or verification without a correct answer.".format(
                weakest_verify.get("harness", ""),
                _signed_pct(weakest_verify.get("delta_correct_minus_incorrect")),
                strongest_verify.get("harness", ""),
                _signed_pct(strongest_verify.get("delta_correct_minus_incorrect")),
            )
        )

    if isinstance(motif_rows, list):
        grounded = _find_metric_row(motif_rows, "tool_grounded_reasoning")
        verified = _find_metric_row(motif_rows, "AnswerAfterVerificationRate")
        premature = _find_metric_row(motif_rows, "PrematureAnswerRate")
        if grounded:
            lines.append(
                "- `tool_grounded_reasoning` occurs in {} of correct trajectories and {} of wrong trajectories (difference {}), the largest positive motif-rate difference.".format(
                    _pct(grounded.get("correct")),
                    _pct(grounded.get("incorrect")),
                    _signed_pct(grounded.get("delta_correct_minus_incorrect")),
                )
            )
        if verified:
            lines.append(
                "- The correct/wrong `AnswerAfterVerificationRate` difference is {}, indicating a positive association between verification before answering and correct outcomes.".format(
                    _signed_pct(verified.get("delta_correct_minus_incorrect"))
                )
            )
        if premature:
            lines.append(
                "- `PrematureAnswerRate` is nearly saturated (correct {}, wrong {}), reflecting unverified first answers in the direct baseline and most trajectories. Treat it as a risk indicator alongside other accuracy evidence.".format(
                    _pct(premature.get("correct")),
                    _pct(premature.get("incorrect")),
                )
            )

    if isinstance(lift_rows, list):
        lift_candidates = [
            row
            for row in lift_rows
            if isinstance(row, Mapping)
            and int(row.get("with_motif_n") or 0) > 0
            and int(row.get("without_motif_n") or 0) > 0
        ]
        if lift_candidates:
            strongest_lift = max(
                lift_candidates, key=lambda row: float(row.get("lift") or 0.0)
            )
            lines.append(
                "- `{}` has the largest comparable `MotifOutcomeLift` ({}, with/without={}/{}), measuring association with correctness.".format(
                    strongest_lift.get("motif", ""),
                    _signed_pct(strongest_lift.get("lift")),
                    int(strongest_lift.get("with_motif_n") or 0),
                    int(strongest_lift.get("without_motif_n") or 0),
                )
            )

    if isinstance(failure_rows, list) and failure_rows:
        worst_failure = max(
            failure_rows, key=lambda row: float(row.get("failure_cost_ratio") or 0.0)
        )
        lines.append(
            "- `{}` has the highest `FailureCostRatio` ({}, {} error records) and contributes the largest readiness cost. These errors are separate from valid-scored behavior comparisons.".format(
                worst_failure.get("harness", ""),
                _pct(worst_failure.get("failure_cost_ratio")),
                int(worst_failure.get("error_records") or 0),
            )
        )

    return "\n".join(lines)


def _latex_measurement_insights(metrics: Mapping[str, Any]) -> str:
    insights = _measurement_insights(metrics)
    if not insights.get("available"):
        return "No analyze_tools measurement summary was available for this run."

    low = insights.get("low_entropy_long_collapse", {})
    inversion = insights.get("confidence_inversion", {})
    posttool = insights.get("posttool_entropy_separation", {})
    strongest_turn = (
        posttool.get("strongest_turn", {}) if isinstance(posttool, Mapping) else {}
    )
    operational_rows = _mapping_list(insights.get("operational_tax_adjusted_accuracy"))
    paired_rows = _mapping_list(insights.get("paired_net_gain"))
    dominance_rows = _mapping_list(insights.get("scaffold_dominance"))
    verification = insights.get("verification_conversion", {})
    verification_lifts = _mapping_list(
        verification.get("lifts") if isinstance(verification, Mapping) else None
    )

    lines = [
        "The analyze_tools measurement layer adds entropy, state-shift, verification-conversion, operational-tax, paired-gain, and scaffold-dominance signals.",
        rf"LowEntropyLongCollapseRate: wrong rate {_tex(_pct(low.get('wrong_rate')))} over n={int(low.get('n') or 0)} low-entropy long OpenClaw samples.",
        rf"ConfidenceInversion: maximum lift {_tex(_signed_pct(inversion.get('inversion_lift')))} at entropy threshold {_tex(_ratio(inversion.get('entropy_threshold')))}.",
    ]
    if strongest_turn:
        lines.append(
            rf"PostToolEntropySeparation is strongest at \texttt{{{_tex(strongest_turn.get('turn_label'))}}} "
            rf"with gain {_tex(_signed_ratio(strongest_turn.get('separation_gain_vs_baseline')))}."
        )
    if verification_lifts:
        strongest_verification = max(
            verification_lifts,
            key=lambda row: float(row.get("verify_before_answer_lift") or 0.0),
        )
        lines.append(
            rf"VerificationConversionRate: strongest verify-before-answer lift is {_tex(_signed_pct(strongest_verification.get('verify_before_answer_lift')))} "
            rf"for \texttt{{{_tex(strongest_verification.get('harness'))}}}."
        )
    if operational_rows:
        deployable = max(
            operational_rows,
            key=lambda row: float(row.get("deployable_accuracy") or 0.0),
        )
        taxed = max(
            operational_rows, key=lambda row: float(row.get("operational_tax") or 0.0)
        )
        lines.append(
            rf"OperationalTaxAdjustedAccuracy: best deployable accuracy is \texttt{{{_tex(deployable.get('harness'))}}} at {_tex(_pct(deployable.get('deployable_accuracy')))}, "
            rf"while highest operational tax is \texttt{{{_tex(taxed.get('harness'))}}} at {_tex(_pct(taxed.get('operational_tax')))}."
        )
    if paired_rows:
        best_gain = max(
            paired_rows, key=lambda row: int(row.get("paired_net_gain") or 0)
        )
        lines.append(
            rf"PairedNetGain: best paired net gain is \texttt{{{_tex(best_gain.get('harness'))}}} with {int(best_gain.get('paired_net_gain') or 0)}."
        )
    if dominance_rows:
        strongest_distance = max(
            dominance_rows,
            key=lambda row: float(row.get("canonical_action_jsd") or 0.0),
        )
        pair = f"{strongest_distance.get('harness_a')} vs {strongest_distance.get('harness_b')}"
        lines.append(
            rf"ScaffoldDominance: largest action-space JSD is {_tex(_ratio(strongest_distance.get('canonical_action_jsd')))} for \texttt{{{_tex(pair)}}}."
        )
    return "\n".join(lines)


def _markdown_measurement_insights(metrics: Mapping[str, Any]) -> str:
    insights = _measurement_insights(metrics)
    if not insights.get("available"):
        return "No measurement summary was supplied or available; this report contains trajectory action metrics only."

    low = insights.get("low_entropy_long_collapse", {})
    inversion = insights.get("confidence_inversion", {})
    posttool = insights.get("posttool_entropy_separation", {})
    strongest_turn = (
        posttool.get("strongest_turn", {}) if isinstance(posttool, Mapping) else {}
    )
    operational_rows = _mapping_list(insights.get("operational_tax_adjusted_accuracy"))
    paired_rows = _mapping_list(insights.get("paired_net_gain"))
    dominance_rows = _mapping_list(insights.get("scaffold_dominance"))
    verification = insights.get("verification_conversion", {})
    verification_lifts = _mapping_list(
        verification.get("lifts") if isinstance(verification, Mapping) else None
    )

    lines = [
        "Metrics from the supplied measurement summary add entropy, tool-boundary state shift, verification conversion, operational tax, paired gain, and scaffold dominance.",
        "",
        "| Insight | Metric | Key value | Interpretation |",
        "| --- | --- | ---: | --- |",
        "| Low-entropy long collapse | `LowEntropyLongCollapseRate` | {} wrong, n={} | Low entropy combined with long output signals collapse risk. |".format(
            _pct(low.get("wrong_rate")),
            int(low.get("n") or 0),
        ),
        "| Confidence inversion | `ConfidenceInversion` | {} at entropy <= {} | The lowest-entropy subset has more errors, indicating inverted confidence ranking. |".format(
            _signed_pct(inversion.get("inversion_lift")),
            _ratio(inversion.get("entropy_threshold")),
        ),
    ]
    if strongest_turn:
        lines.append(
            "| Post-tool state shift | `PostToolEntropySeparation` | {} at `{}` | Correct/wrong trajectory entropy separates after tool results. |".format(
                _signed_ratio(strongest_turn.get("separation_gain_vs_baseline")),
                strongest_turn.get("turn_label", ""),
            )
        )
    if verification_lifts:
        strongest_verification = max(
            verification_lifts,
            key=lambda row: float(row.get("verify_before_answer_lift") or 0.0),
        )
        lines.append(
            "| Verification conversion | `VerificationConversionRate` | {} for `{}` | Track whether verification precedes the answer and triggers a subsequent action change. |".format(
                _signed_pct(strongest_verification.get("verify_before_answer_lift")),
                strongest_verification.get("harness", ""),
            )
        )
    if operational_rows:
        deployable = max(
            operational_rows,
            key=lambda row: float(row.get("deployable_accuracy") or 0.0),
        )
        taxed = max(
            operational_rows, key=lambda row: float(row.get("operational_tax") or 0.0)
        )
        lines.append(
            "| Operational tax | `OperationalTaxAdjustedAccuracy` | best deployable `{}` {}; max tax `{}` {} | Scaffold gains must account for system costs from non-valid records. |".format(
                deployable.get("harness", ""),
                _pct(deployable.get("deployable_accuracy")),
                taxed.get("harness", ""),
                _pct(taxed.get("operational_tax")),
            )
        )
    if paired_rows:
        best_gain = max(
            paired_rows, key=lambda row: int(row.get("paired_net_gain") or 0)
        )
        lines.append(
            "| Paired rescue/regression | `PairedNetGain` | `{}` {:+d} | Agent harnesses produce both rescues and regressions; report paired changes alongside aggregate accuracy. |".format(
                best_gain.get("harness", ""),
                int(best_gain.get("paired_net_gain") or 0),
            )
        )
    if dominance_rows:
        strongest_distance = max(
            dominance_rows,
            key=lambda row: float(row.get("canonical_action_jsd") or 0.0),
        )
        lines.append(
            "| Scaffold dominance | `ScaffoldDominance` | JSD {} for `{}` vs `{}` | Report action-space distance before comparing accuracy across harnesses. |".format(
                _ratio(strongest_distance.get("canonical_action_jsd")),
                strongest_distance.get("harness_a", ""),
                strongest_distance.get("harness_b", ""),
            )
        )
    return "\n".join(lines)


def _measurement_insights(metrics: Mapping[str, Any]) -> Mapping[str, Any]:
    insights = metrics.get("measurement_insights")
    return insights if isinstance(insights, Mapping) else {}


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    return (
        [row for row in value if isinstance(row, Mapping)]
        if isinstance(value, list)
        else []
    )


def _find_metric_row(rows: Sequence[Any], metric_name: str) -> Mapping[str, Any] | None:
    for row in rows:
        if isinstance(row, Mapping) and row.get("metric_name") == metric_name:
            return row
    return None


def _markdown_case_studies(case_studies: Sequence[Mapping[str, Any]]) -> str:
    if not case_studies:
        return "No case studies are available."
    lines = []
    for case in case_studies[:8]:
        motifs = ", ".join(str(motif) for motif in case.get("motifs", [])) or "none"
        snippets = (
            case.get("evidence_snippets")
            if isinstance(case.get("evidence_snippets"), list)
            else []
        )
        snippet = str(snippets[0]) if snippets else ""
        lines.append(
            f"- `{case.get('harness')}` / `{case.get('task_id')}`: score={case.get('score_status')}, correct={case.get('correct')}, motifs={motifs}. {snippet}"
        )
    return "\n".join(lines)


def _markdown_output_files(output_files: Mapping[str, Path | str]) -> str:
    return "\n".join(
        f"- `{name}`: `{_path_text(path)}`"
        for name, path in sorted(output_files.items())
    )


def _status_count(row: Mapping[str, Any], status: str) -> int:
    counts = row.get("status_counts")
    if isinstance(counts, Mapping):
        return int(counts.get(status) or 0)
    return 0


def _write_json(payload: Any, path: Path) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_csv(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    fieldnames = _fieldnames(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _fieldnames(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not rows:
        return [
            "run_id",
            "harness",
            "task_id",
            "sample_index",
            "step_id",
            "canonical_action",
            "action_label_confidence",
            "source",
            "tool_subtype",
            "observation_status",
            "recovery_context",
            "text_span",
            "score_status",
            "correct",
        ]
    first = list(rows[0])
    extras = sorted({key for row in rows for key in row if key not in first})
    return first + extras


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item) for key, item in value.items() if key != "bundle"
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return _path_text(value)
    return value


def _tex(value: Any) -> str:
    text = str(value)
    replacements = {
        "\\": r"\textbackslash{}",
        "_": r"\_",
        "%": r"\%",
        "&": r"\&",
        "#": r"\#",
        "{": r"\{",
        "}": r"\}",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _path_text(path: Path | str) -> str:
    return path.as_posix() if isinstance(path, Path) else str(path)


def _pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number * 100:.1f}%"


def _signed_pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    sign = "+" if number > 0 else ""
    return f"{sign}{number * 100:.1f} pp"


def _ratio(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:.3f}"


def _signed_ratio(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.3f}"
