"""Phase 15 behavior insight report renderers and artifact writers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from alphadiana.analysis.action_events import ActionEvent
from alphadiana.analysis.insight_corpus import sanitize_path_text

PHASE15_OUTPUT_FILENAMES = {
    "corpus_inventory_json": "corpus_inventory.json",
    "corpus_inventory_csv": "corpus_inventory.csv",
    "action_events_csv": "action_events.csv",
    "insight_claims": "insight_claims.json",
    "case_anchors": "case_anchors.json",
    "markdown": "behavior_insights.md",
    "markdown_zh": "behavior_insights_zh.md",
}

HLE_DENOMINATOR_CAVEAT = (
    "HLE/collaborator roots that are unavailable or manifest-only are denominator evidence only, not behavioral evidence."
)
HF_DATASET_CAVEAT = (
    "The dataset source is T-MARS/alphadiana-benchmark-results; local cache snapshot paths are not durable evidence paths."
)


def render_behavior_insights_markdown(
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    insight_claims: Sequence[Mapping[str, Any]],
    case_anchors: Sequence[Mapping[str, Any]],
    output_files: Mapping[str, Path | str],
) -> str:
    """Render the English Phase 15 mechanism insight report."""
    lines = [
        "# Model-Harness Behavior Insights",
        "",
        "This report mines persisted AlphaDiana result artifacts only. It does not launch live model, benchmark, provider, or agent runtimes.",
        "",
        "## Denominator Ledger",
        "",
        _denominator_table(_sort_denominators(denominator_rows)),
        "",
        "## Falsifier-Backed Mechanism Claims",
        "",
        _claims_section(_sort_claims(insight_claims)),
        "",
        "## Deterministic Case Anchors",
        "",
        _case_anchor_section(_sort_cases(case_anchors)),
        "",
        "## Corpus Availability And Caveats",
        "",
        f"- {HLE_DENOMINATOR_CAVEAT}",
        f"- {HF_DATASET_CAVEAT}",
        "- Claim support is denominator-scoped; unavailable, manifest-only, missing, error-only, or unsynced corpora are not treated as behavior evidence.",
        "",
        "## Generated Artifacts",
        "",
        _output_files_section(output_files),
        "",
    ]
    return _sanitize_rendered("\n".join(lines))


def render_behavior_insights_zh(
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    insight_claims: Sequence[Mapping[str, Any]],
    case_anchors: Sequence[Mapping[str, Any]],
    output_files: Mapping[str, Path | str],
) -> str:
    """Render the Chinese Phase 15 mechanism insight report."""
    lines = [
        "# 模型-框架行为洞察",
        "",
        "本报告只读取已经持久化的 AlphaDiana 结果产物，不启动实时模型、benchmark、provider 或 agent runtime。",
        "",
        "## 分母账本",
        "",
        _denominator_table(_sort_denominators(denominator_rows)),
        "",
        "## 可证伪机制主张",
        "",
        _claims_section(_sort_claims(insight_claims)),
        "",
        "## 确定性案例锚点",
        "",
        _case_anchor_section(_sort_cases(case_anchors)),
        "",
        "## 语料可用性与注意事项",
        "",
        f"- {HLE_DENOMINATOR_CAVEAT}",
        f"- {HF_DATASET_CAVEAT}",
        "- 主张支持范围由分母限定；unavailable、manifest-only、missing、error-only 或 unsynced 语料不作为行为证据。",
        "",
        "## 输出文件",
        "",
        _output_files_section(output_files),
        "",
    ]
    return _sanitize_rendered("\n".join(lines))


def write_phase15_outputs(
    output_dir: Path,
    *,
    denominator_rows: Sequence[Mapping[str, Any]],
    event_rows: Sequence[Mapping[str, Any]],
    insight_claims: Sequence[Mapping[str, Any]],
    case_anchors: Sequence[Mapping[str, Any]],
) -> dict[str, Path]:
    """Write all Phase 15 machine-readable and human-facing outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {key: output_dir / filename for key, filename in PHASE15_OUTPUT_FILENAMES.items()}

    sorted_denominators = _sort_denominators(_sanitize_data(list(denominator_rows)))
    sorted_claims = _sort_claims(_sanitize_data(list(insight_claims)))
    sorted_cases = _sort_cases(_sanitize_data(list(case_anchors)))
    sanitized_events = _sanitize_data(list(event_rows))

    output_refs = {key: Path(filename) for key, filename in PHASE15_OUTPUT_FILENAMES.items()}
    markdown = render_behavior_insights_markdown(
        denominator_rows=sorted_denominators,
        insight_claims=sorted_claims,
        case_anchors=sorted_cases,
        output_files=output_refs,
    )
    markdown_zh = render_behavior_insights_zh(
        denominator_rows=sorted_denominators,
        insight_claims=sorted_claims,
        case_anchors=sorted_cases,
        output_files=output_refs,
    )

    _write_json(sorted_denominators, paths["corpus_inventory_json"])
    _write_csv(sorted_denominators, paths["corpus_inventory_csv"])
    _write_csv(sanitized_events, paths["action_events_csv"], default_fieldnames=list(ActionEvent.__dataclass_fields__))
    _write_json(sorted_claims, paths["insight_claims"])
    _write_json(sorted_cases, paths["case_anchors"])
    paths["markdown"].write_text(markdown, encoding="utf-8")
    paths["markdown_zh"].write_text(markdown_zh, encoding="utf-8")
    return paths


def _sort_denominators(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=lambda row: str(row.get("corpus_label") or ""))


def _sort_claims(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=lambda row: str(row.get("claim_id") or ""))


def _sort_cases(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(row) for row in rows),
        key=lambda row: str(row.get("case_anchor_id") or row.get("anchor_id") or ""),
    )


def _denominator_table(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| Corpus | Benchmark | Harness | Status | Selected | Valid-scored | Errors | Missing | Public ref |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _text(row.get("corpus_label")),
                    _text(row.get("benchmark")),
                    _text(row.get("harness")),
                    _text(row.get("status")),
                    str(_int(row.get("selected_records"))),
                    str(_int(row.get("valid_scored"))),
                    str(_int(row.get("error_records"))),
                    str(_int(row.get("missing_samples"))),
                    _text(row.get("public_ref")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _claims_section(claims: Sequence[Mapping[str, Any]]) -> str:
    if not claims:
        return "No mechanism claims were generated."
    sections: list[str] = []
    for claim in claims:
        result = json.dumps(_sanitize_data(claim.get("result", {})), ensure_ascii=False, sort_keys=True)
        anchors = ", ".join(_text(anchor) for anchor in claim.get("case_anchor_ids", []) or []) or "none"
        sections.append(
            "\n".join(
                [
                    f"### {_text(claim.get('mechanism_name') or claim.get('claim_id'))}",
                    "",
                    f"- Claim ID: `{_text(claim.get('claim_id'))}`",
                    f"- Old story: {_text(claim.get('old_story'))}",
                    f"- Falsifier: {_text(claim.get('falsifier_question'))}",
                    f"- Measurement: {_text(claim.get('measurement'))}",
                    f"- Denominator: {_text(claim.get('denominator_filter'))}",
                    f"- Result: `{_text(result)}`",
                    f"- Case anchors: {anchors}",
                    f"- Interpretation: {_text(claim.get('interpretation'))}",
                    f"- Caveat: {_text(claim.get('failure_to_establish')) or 'none'}",
                ]
            )
        )
    return "\n\n".join(sections)


def _case_anchor_section(cases: Sequence[Mapping[str, Any]]) -> str:
    if not cases:
        return "No deterministic case anchors were selected."
    lines = ["| Anchor | Type | Harness | Task | Status | Snippets |", "| --- | --- | --- | --- | --- | --- |"]
    for case in cases:
        snippets = " / ".join(_text(snippet) for snippet in case.get("evidence_snippets", []) or [])
        lines.append(
            "| "
            + " | ".join(
                [
                    _text(case.get("case_anchor_id") or case.get("anchor_id")),
                    _text(case.get("anchor_type")),
                    _text(case.get("harness")),
                    _text(case.get("task_id")),
                    _text(case.get("score_status")),
                    snippets,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _output_files_section(output_files: Mapping[str, Path | str]) -> str:
    return "\n".join(
        f"- `{_text(name)}`: `{_text(_path_text(path))}`"
        for name, path in sorted(output_files.items())
    )


def _write_json(data: Any, path: Path) -> None:
    path.write_text(json.dumps(_sanitize_data(data), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _write_csv(
    rows: Sequence[Mapping[str, Any]],
    path: Path,
    *,
    default_fieldnames: Sequence[str] | None = None,
) -> None:
    fieldnames = list(default_fieldnames or [])
    for key in sorted({key for row in rows for key in row}):
        if key not in fieldnames:
            fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else value
                    for key, value in _sanitize_data(dict(row)).items()
                }
            )


def _sanitize_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _sanitize_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_data(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_data(item) for item in value]
    if isinstance(value, Path):
        return sanitize_path_text(str(value))
    if isinstance(value, str):
        return sanitize_path_text(value)
    return value


def _sanitize_rendered(text: str) -> str:
    return "\n".join(sanitize_path_text(line) for line in text.splitlines())


def _text(value: Any) -> str:
    if value is None:
        return ""
    return sanitize_path_text(str(value)).replace("\n", " ").replace("|", "\\|")


def _path_text(path: Path | str) -> str:
    return Path(path).as_posix() if isinstance(path, Path) else str(path)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
