"""Falsifier-backed mechanism claims for offline Phase 15 insight analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

from alphadiana.analysis.trajectory_metrics import summarize_analyze_tool_measurements

INSIGHT_MECHANISMS = (
    "Model-Harness Composite",
    "Scaffold Dominance",
    "State-Update Yield",
    "Verification Conversion Gap",
    "Low-Entropy Long Collapse",
    "Operational Tax",
    "Paired Rescue Regression",
)


@dataclass(frozen=True)
class InsightClaim:
    """One auditable mechanism claim with its falsifier and support caveat."""

    claim_id: str
    mechanism_name: str
    old_story: str
    narrative_replacement: str
    falsifier_question: str
    measurement: str
    denominator_filter: str
    support_scope: str
    result: dict[str, Any]
    case_anchor_ids: tuple[str, ...]
    interpretation: str
    failure_to_establish: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable claim dictionary."""
        row = asdict(self)
        row["case_anchor_ids"] = list(self.case_anchor_ids)
        return row


def build_insight_claims(
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    measurement_summary: Mapping[str, Any] | None = None,
    case_anchors: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Build deterministic InsightClaim dictionaries from offline artifacts.

    Denominator rows are expected to come from build_denominator_ledger; metrics
    should be produced by compute_outcome_conditioned_metrics or equivalent
    persisted analyze_tools summaries.
    """
    anchor_ids = _case_anchor_ids_by_type(case_anchors)
    claims = [
        claim_model_harness_composite(
            denominator_rows=denominator_rows,
            metrics=metrics,
            measurement_summary=measurement_summary,
            case_anchor_ids=anchor_ids.get("scaffold_dominance", ()),
        ),
        claim_scaffold_dominance(
            denominator_rows=denominator_rows,
            metrics=metrics,
            measurement_summary=measurement_summary,
            case_anchor_ids=anchor_ids.get("scaffold_dominance", ()),
        ),
        claim_state_update_yield(
            denominator_rows=denominator_rows,
            metrics=metrics,
            measurement_summary=measurement_summary,
            case_anchor_ids=anchor_ids.get("tool_without_state_shift", ()),
        ),
        claim_verification_conversion_gap(
            denominator_rows=denominator_rows,
            metrics=metrics,
            measurement_summary=measurement_summary,
            case_anchor_ids=anchor_ids.get("verify_without_conversion", ()),
        ),
        claim_low_entropy_long_collapse(
            denominator_rows=denominator_rows,
            metrics=metrics,
            measurement_summary=measurement_summary,
            case_anchor_ids=anchor_ids.get("low_entropy_long_wrong", ()),
        ),
        claim_operational_tax(
            denominator_rows=denominator_rows,
            metrics=metrics,
            measurement_summary=measurement_summary,
            case_anchor_ids=anchor_ids.get("operational_error", ()),
        ),
        claim_paired_rescue_regression(
            denominator_rows=denominator_rows,
            metrics=metrics,
            measurement_summary=measurement_summary,
            case_anchor_ids=anchor_ids.get("paired_rescue", ()) + anchor_ids.get("paired_regression", ()),
        ),
    ]
    return [claim.to_dict() for claim in claims]


def support_scope_from_denominators(rows: Sequence[Mapping[str, Any]]) -> str:
    """Return the strongest benchmark support scope from validated rows."""
    validated_benchmarks = {
        str(row.get("benchmark") or "").lower()
        for row in rows
        if _has_validated_records(row)
    }
    if "gpqa" in validated_benchmarks and len(validated_benchmarks) > 1:
        return "cross-benchmark-supported"
    if validated_benchmarks == {"gpqa"}:
        return "GPQA-supported"
    if "hle" in validated_benchmarks:
        return "HLE-pilot-supported"
    return "needs-collab-validation"


def validate_claim_denominators(
    claim: InsightClaim,
    denominator_rows: Sequence[Mapping[str, Any]],
) -> InsightClaim:
    """Attach a failure caveat when the claim lacks validated denominator rows."""
    required = _required_benchmarks(claim.denominator_filter, claim.support_scope)
    missing = [
        benchmark
        for benchmark in required
        if not any(
            str(row.get("benchmark") or "").lower() == benchmark and _has_validated_records(row)
            for row in denominator_rows
        )
    ]
    if not missing:
        return claim
    caveat = (
        "No validated_records denominator row with valid_scored samples for "
        f"{', '.join(sorted(missing))}; treat this as a measurement template, not established evidence."
    )
    return replace(claim, failure_to_establish=_append_caveat(claim.failure_to_establish, caveat))


def claim_model_harness_composite(
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    measurement_summary: Mapping[str, Any] | None = None,
    case_anchor_ids: Sequence[str] = (),
) -> InsightClaim:
    """Claim that same-model harness scores need scaffold and tax fields."""
    insights = _measurement_insights(metrics, measurement_summary)
    scaffold_rows = _rows(insights.get("scaffold_dominance"))
    tax_rows = _rows(insights.get("operational_tax_adjusted_accuracy"))
    failure = ""
    if not scaffold_rows:
        failure = _append_caveat(failure, "Cross-harness score differences lack action-space distance evidence.")
    if not _tax_rows_complete(tax_rows):
        failure = _append_caveat(
            failure,
            "Raw accuracy is not interpretable as base-model capability without behavioral accuracy, operational tax, and deployable accuracy.",
        )
    interpretation = (
        "Cross-harness outcomes are interpreted as model-harness composite behavior only after action-space "
        "distance and deployment tax fields are present."
        if not failure
        else "Insufficient scaffold/tax evidence to reinterpret raw score differences."
    )
    claim = InsightClaim(
        claim_id="model_harness_composite",
        mechanism_name="Model-Harness Composite",
        old_story="Same-model benchmark scores compare the base model in isolation.",
        narrative_replacement="Scores measure a coupled model, scaffold, verifier, and runtime system.",
        falsifier_question=(
            "If scores are model-only, do same-model harnesses preserve similar action-space support and deployment tax?"
        ),
        measurement="ScaffoldDominance + OperationalTaxAdjustedAccuracy",
        denominator_filter="GPQA validated_records rows with action-space distance and operational tax fields",
        support_scope=support_scope_from_denominators(denominator_rows),
        result={"action_space_distance": scaffold_rows, "operational_tax": tax_rows},
        case_anchor_ids=tuple(case_anchor_ids),
        interpretation=interpretation,
        failure_to_establish=failure,
    )
    return validate_claim_denominators(claim, denominator_rows)


def claim_scaffold_dominance(
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    measurement_summary: Mapping[str, Any] | None = None,
    case_anchor_ids: Sequence[str] = (),
) -> InsightClaim:
    """Claim that scaffold action distributions can dominate outcome interpretation."""
    insights = _measurement_insights(metrics, measurement_summary)
    rows = _rows(insights.get("scaffold_dominance"))
    claim = InsightClaim(
        claim_id="scaffold_dominance",
        mechanism_name="Scaffold Dominance",
        old_story="Harnesses are neutral wrappers around a fixed model policy.",
        narrative_replacement="Harnesses reshape the observable action space before final-answer scoring.",
        falsifier_question=(
            "Do same-model harnesses have large canonical-action divergence or low support overlap?"
        ),
        measurement="ScaffoldDominance",
        denominator_filter="GPQA validated_records rows with canonical action distributions",
        support_scope=support_scope_from_denominators(denominator_rows),
        result={"action_space_distance": rows},
        case_anchor_ids=tuple(case_anchor_ids),
        interpretation=(
            "Action-space distance is reported before comparing outcome deltas."
            if rows
            else "Scaffold dominance is not established without action-space distance rows."
        ),
        failure_to_establish="" if rows else "No action-space distance rows are available.",
    )
    return validate_claim_denominators(claim, denominator_rows)


def claim_state_update_yield(
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    measurement_summary: Mapping[str, Any] | None = None,
    case_anchor_ids: Sequence[str] = (),
) -> InsightClaim:
    """Claim that useful tool use requires post-tool state separation."""
    insights = _measurement_insights(metrics, measurement_summary)
    posttool = dict(insights.get("posttool_entropy_separation") or {})
    strongest = dict(posttool.get("strongest_turn") or {})
    failure = ""
    if not strongest:
        failure = "No post-tool entropy/state-shift separation rows are available."
    claim = InsightClaim(
        claim_id="state_update_yield",
        mechanism_name="State-Update Yield",
        old_story="More tool calls mean more useful agent interaction.",
        narrative_replacement="Tool use matters when it creates measurable post-tool state separation.",
        falsifier_question=(
            "After a tool result, do correct and wrong trajectories separate in entropy or state-shift fields?"
        ),
        measurement="PostToolEntropySeparation",
        denominator_filter="GPQA validated_records rows with post-tool entropy/state-shift fields",
        support_scope=support_scope_from_denominators(denominator_rows),
        result={
            "strongest_turn": strongest,
            "boundary_shock_integral_0_15": posttool.get("boundary_shock_integral_0_15", 0.0),
        },
        case_anchor_ids=tuple(case_anchor_ids),
        interpretation=(
            "Tool value is measured by post-tool state separation, not raw tool-call count."
            if strongest
            else "Raw tool-call count alone cannot establish state-update yield."
        ),
        failure_to_establish=failure,
    )
    return validate_claim_denominators(claim, denominator_rows)


def claim_verification_conversion_gap(
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    measurement_summary: Mapping[str, Any] | None = None,
    case_anchor_ids: Sequence[str] = (),
) -> InsightClaim:
    """Claim that verification must convert into pre-answer or corrective action."""
    insights = _measurement_insights(metrics, measurement_summary)
    lifts = _rows(dict(insights.get("verification_conversion") or {}).get("lifts"))
    best = max(
        lifts,
        key=lambda row: abs(_float(row.get("post_verify_action_change_lift")))
        + abs(_float(row.get("verify_before_answer_lift"))),
        default={},
    )
    result = {
        "harness": best.get("harness"),
        "verify_before_answer_lift": _float(best.get("verify_before_answer_lift")),
        "post_verify_action_change_lift": _float(best.get("post_verify_action_change_lift")),
        "correct_n": _int(best.get("correct_n")),
        "wrong_n": _int(best.get("wrong_n")),
    } if best else {}
    failure = "Use conversion fields; raw verify rate alone is not sufficient evidence."
    if not best:
        failure = _append_caveat(failure, "No verification conversion lift rows are available.")
    claim = InsightClaim(
        claim_id="verification_conversion_gap",
        mechanism_name="Verification Conversion Gap",
        old_story="Verification improves reliability when it appears in the trace.",
        narrative_replacement="Verification helps only when it occurs before answering or changes later action.",
        falsifier_question=(
            "Does verification convert into answer-before/after ordering or post-verify non-answer action changes?"
        ),
        measurement="VerificationConversionRate",
        denominator_filter="GPQA validated_records rows with verify-before-answer and post-verify action-change fields",
        support_scope=support_scope_from_denominators(denominator_rows),
        result=result,
        case_anchor_ids=tuple(case_anchor_ids),
        interpretation="Verification is evaluated through conversion fields, not raw verify rate.",
        failure_to_establish=failure,
    )
    return validate_claim_denominators(claim, denominator_rows)


def claim_low_entropy_long_collapse(
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    measurement_summary: Mapping[str, Any] | None = None,
    case_anchor_ids: Sequence[str] = (),
) -> InsightClaim:
    """Claim that low entropy becomes risky specifically in long-output strata."""
    insights = _measurement_insights(metrics, measurement_summary)
    result = dict(insights.get("low_entropy_long_collapse") or {})
    required = {"n", "wrong_rate", "median_tokens", "median_entropy", "token_threshold_q75", "entropy_threshold_q25"}
    failure = "" if required <= set(result) else "Low-entropy long collapse requires both token-count and entropy strata."
    claim = InsightClaim(
        claim_id="low_entropy_long_collapse",
        mechanism_name="Low-Entropy Long Collapse",
        old_story="Lower entropy is a monotone confidence proxy.",
        narrative_replacement="The dangerous region is low entropy combined with long output budget consumption.",
        falsifier_question=(
            "Is the lowest-entropy long-output stratum higher risk than higher-entropy or shorter strata?"
        ),
        measurement="LowEntropyLongCollapseRate",
        denominator_filter="GPQA validated_records rows with token-count and entropy strata",
        support_scope=support_scope_from_denominators(denominator_rows),
        result=result,
        case_anchor_ids=tuple(case_anchor_ids),
        interpretation=(
            "Entropy is interpreted jointly with length; low entropy alone is not treated as confidence."
            if not failure
            else "The collapse claim is not established without token and entropy thresholds."
        ),
        failure_to_establish=failure,
    )
    return validate_claim_denominators(claim, denominator_rows)


def claim_operational_tax(
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    measurement_summary: Mapping[str, Any] | None = None,
    case_anchor_ids: Sequence[str] = (),
) -> InsightClaim:
    """Claim that deployable accuracy needs behavioral and operational denominators."""
    insights = _measurement_insights(metrics, measurement_summary)
    rows = _rows(insights.get("operational_tax_adjusted_accuracy"))
    failure = "" if _tax_rows_complete(rows) else (
        "Operational tax requires behavioral_accuracy, operational_tax, and deployable_accuracy fields."
    )
    claim = InsightClaim(
        claim_id="operational_tax",
        mechanism_name="Operational Tax",
        old_story="Valid-scored behavioral accuracy is the deployed score.",
        narrative_replacement="Agent usefulness must subtract missing, provider, runtime, and harness failures.",
        falsifier_question=(
            "Does deployable accuracy differ from behavioral accuracy once non-valid samples enter the denominator?"
        ),
        measurement="OperationalTaxAdjustedAccuracy",
        denominator_filter="GPQA validated_records rows with expected, valid_scored, and error denominators",
        support_scope=support_scope_from_denominators(denominator_rows),
        result={"by_harness": rows},
        case_anchor_ids=tuple(case_anchor_ids),
        interpretation=(
            "Behavioral accuracy, operational tax, and deployable accuracy are reported together."
            if not failure
            else "Behavioral accuracy alone is not reported as deployable performance."
        ),
        failure_to_establish=failure,
    )
    return validate_claim_denominators(claim, denominator_rows)


def claim_paired_rescue_regression(
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    measurement_summary: Mapping[str, Any] | None = None,
    case_anchor_ids: Sequence[str] = (),
) -> InsightClaim:
    """Claim that scaffolds can rescue and regress paired DirectLLM outcomes."""
    insights = _measurement_insights(metrics, measurement_summary)
    rows = _rows(insights.get("paired_net_gain"))
    complete = [
        row for row in rows if {"rescue", "regression", "net_gain"} <= set(row)
    ]
    claim = InsightClaim(
        claim_id="paired_rescue_regression",
        mechanism_name="Paired Rescue Regression",
        old_story="Agent scaffolds monotonically improve a model's answers.",
        narrative_replacement="Scaffolds can rescue DirectLLM misses while regressing DirectLLM hits.",
        falsifier_question=(
            "On paired valid tasks, do agent harnesses show both rescues and regressions relative to DirectLLM?"
        ),
        measurement="PairedNetGain",
        denominator_filter="GPQA paired DirectLLM and agent validated_records rows",
        support_scope=support_scope_from_denominators(denominator_rows),
        result={"by_harness": complete},
        case_anchor_ids=tuple(case_anchor_ids),
        interpretation=(
            "Paired rescue, regression, and net gain are reported together."
            if complete
            else "No paired rescue/regression claim is established without all three fields."
        ),
        failure_to_establish="" if complete else "Paired net gain requires rescue, regression, and net_gain fields.",
    )
    return validate_claim_denominators(claim, denominator_rows)


def _measurement_insights(
    metrics: Mapping[str, Any],
    measurement_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(metrics.get("measurement_insights"), Mapping):
        return dict(metrics["measurement_insights"])
    if measurement_summary is not None:
        return summarize_analyze_tool_measurements(measurement_summary)
    return {}


def _case_anchor_ids_by_type(case_anchors: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for anchor in case_anchors:
        anchor_type = str(anchor.get("anchor_type") or "")
        anchor_id = str(anchor.get("anchor_id") or "")
        if anchor_type and anchor_id:
            grouped.setdefault(anchor_type, []).append(anchor_id)
    return {key: tuple(sorted(values)) for key, values in grouped.items()}


def _has_validated_records(row: Mapping[str, Any]) -> bool:
    return str(row.get("status") or "") == "validated_records" and _int(row.get("valid_scored")) > 0


def _required_benchmarks(denominator_filter: str, support_scope: str) -> tuple[str, ...]:
    text = f"{denominator_filter} {support_scope}".lower()
    required: list[str] = []
    if "gpqa" in text:
        required.append("gpqa")
    if "hle" in text:
        required.append("hle")
    if "cross-benchmark" in text and not required:
        required.extend(["gpqa", "hle"])
    return tuple(dict.fromkeys(required))


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _tax_rows_complete(rows: Sequence[Mapping[str, Any]]) -> bool:
    required = {"behavioral_accuracy", "operational_tax", "deployable_accuracy"}
    return bool(rows) and all(required <= set(row) for row in rows)


def _append_caveat(existing: str, caveat: str) -> str:
    if not existing:
        return caveat
    if caveat in existing:
        return existing
    return f"{existing} {caveat}"


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
