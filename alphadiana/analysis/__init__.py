"""Offline analysis API; imports load only the requested owner."""

from importlib import import_module

_EXPORTS = {
    "RunBundle": "alphadiana.results.reader",
    "load_jsonl_records": "alphadiana.results.reader",
    "load_run_bundle": "alphadiana.results.reader",
    "resolve_run_relative_path": "alphadiana.results.reader",
    "ACTION_LABEL_CONFIDENCES": "alphadiana.analysis.actions.keyword_baseline",
    "ACTION_PRIORITY": "alphadiana.analysis.actions.keyword_baseline",
    "BACKUP_EXCLUDE_PREFIX": "alphadiana.analysis.actions.keyword_baseline",
    "CANONICAL_ACTIONS": "alphadiana.analysis.actions.keyword_baseline",
    "DIRECTLLM_CANDIDATES": "alphadiana.analysis.actions.keyword_baseline",
    "EVENT_SOURCES": "alphadiana.analysis.actions.keyword_baseline",
    "OBSERVATION_STATUSES": "alphadiana.analysis.actions.keyword_baseline",
    "PRIMARY_CORPORA": "alphadiana.analysis.actions.keyword_baseline",
    "TOOL_SUBTYPES": "alphadiana.analysis.actions.keyword_baseline",
    "ActionEvent": "alphadiana.analysis.actions.keyword_baseline",
    "classify_action_intent": "alphadiana.analysis.actions.keyword_baseline",
    "classify_tool_subtype": "alphadiana.analysis.actions.keyword_baseline",
    "extract_action_event_rows": "alphadiana.analysis.actions.keyword_baseline",
    "extract_action_events": "alphadiana.analysis.actions.keyword_baseline",
    "inventory_corpus": "alphadiana.analysis.actions.keyword_baseline",
    "normalized_records": "alphadiana.analysis.actions.keyword_baseline",
    "observation_status_from_text": "alphadiana.analysis.actions.keyword_baseline",
    "select_directllm_baseline": "alphadiana.analysis.actions.keyword_baseline",
    "build_record_feature_rows": "alphadiana.analysis.actions.features",
    "CASE_STUDY_MOTIFS": "alphadiana.analysis.actions.trajectories",
    "select_case_studies": "alphadiana.analysis.actions.trajectories",
    "summarize_event_sequence": "alphadiana.analysis.actions.trajectories",
    "cluster_analysis_views": "alphadiana.analysis.actions.features",
    "cluster_rows": "alphadiana.analysis.actions.features",
    "compute_reliability_summary": "alphadiana.results.statistics",
    "render_markdown_report": "alphadiana.analysis.actions.action_space_report",
    "write_feature_csv": "alphadiana.analysis.actions.action_space_report",
    "write_json": "alphadiana.analysis.actions.action_space_report",
    "ERROR_STATUSES": "alphadiana.analysis.actions.trajectories",
    "MAIN_METRIC_NAMES": "alphadiana.analysis.actions.trajectories",
    "compute_outcome_conditioned_metrics": "alphadiana.analysis.actions.trajectories",
    "group_event_sequences": "alphadiana.analysis.actions.trajectories",
    "has_answer_after_verification": "alphadiana.analysis.actions.trajectories",
    "has_budget_collapse": "alphadiana.analysis.actions.trajectories",
    "has_error_recovery": "alphadiana.analysis.actions.trajectories",
    "has_premature_answer": "alphadiana.analysis.actions.trajectories",
    "has_tool_grounded_reasoning": "alphadiana.analysis.actions.trajectories",
    "trajectory_key": "alphadiana.analysis.actions.trajectories",
    "render_chinese_discussion": "alphadiana.analysis.actions.trajectory_report",
    "render_latex_appendix": "alphadiana.analysis.actions.trajectory_report",
    "write_phase14_outputs": "alphadiana.analysis.actions.trajectory_report",
    "CorpusInventoryRow": "alphadiana.analysis.insights.corpus",
    "CorpusSpec": "alphadiana.analysis.insights.corpus",
    "build_denominator_ledger": "alphadiana.analysis.insights.corpus",
    "inventory_corpus_spec": "alphadiana.analysis.insights.corpus",
    "load_phase15_corpus_specs": "alphadiana.analysis.insights.corpus",
    "load_selected_task_records": "alphadiana.analysis.insights.corpus",
    "sanitize_path_text": "alphadiana.analysis.insights.corpus",
    "write_denominator_ledger": "alphadiana.analysis.insights.corpus",
    "INSIGHT_MECHANISMS": "alphadiana.analysis.insights.claims",
    "InsightClaim": "alphadiana.analysis.insights.claims",
    "build_insight_claims": "alphadiana.analysis.insights.claims",
    "CASE_ANCHOR_TYPES": "alphadiana.analysis.insights.cases",
    "case_anchor_id": "alphadiana.analysis.insights.cases",
    "select_insight_case_anchors": "alphadiana.analysis.insights.cases",
    "PHASE15_OUTPUT_FILENAMES": "alphadiana.analysis.insights.reports",
    "render_behavior_insights_markdown": "alphadiana.analysis.insights.reports",
    "render_behavior_insights_zh": "alphadiana.analysis.insights.reports",
    "write_phase15_outputs": "alphadiana.analysis.insights.reports",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    owner = _EXPORTS.get(name)
    if owner is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(owner), name)
    globals()[name] = value
    return value
