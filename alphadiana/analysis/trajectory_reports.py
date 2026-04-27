"""Phase 14 GPQA trajectory report renderers and artifact writers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from alphadiana.analysis.action_events import CANONICAL_ACTIONS


def render_latex_appendix(
    inventory: Mapping[str, Any],
    metrics: Mapping[str, Any],
    case_studies: Sequence[Mapping[str, Any]],
    *,
    output_files: Mapping[str, Path | str],
) -> str:
    """Render the English Phase 14 appendix draft."""
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


def render_chinese_discussion(
    inventory: Mapping[str, Any],
    metrics: Mapping[str, Any],
    case_studies: Sequence[Mapping[str, Any]],
    *,
    output_files: Mapping[str, Path | str],
) -> str:
    """Render the Chinese Phase 14 discussion note."""
    lines = [
        "# GPQA-Diamond Qwen3.5 跨框架轨迹分析讨论",
        "",
        "这份记录讨论 DirectLLM、OpenClaw、OpenCode 和 ZeroClaw 在 GPQA-Diamond 上形成的动作生态。分析只读取已经持久化的结果文件，不重新调用模型或框架。",
        "",
        "## 动作意图语法",
        "",
        "本文固定使用 `A = {plan, reason, tool_use, verify, recover, answer}`。`search/shell/python/browser` 是 `tool_subtype` 注解，而不是新的规范动作；每个事件只归入一个 canonical action。",
        "DirectLLM 是非智能体的直接回答基线，因此只作为退化的 reason/answer 轨迹参与比较。",
        "",
        "## 宏观结果",
        "",
        _markdown_corpus_table(_inventory_corpora(inventory)),
        "",
        _markdown_metrics_summary(metrics),
        "",
        "## 微观案例",
        "",
        _markdown_case_studies(case_studies),
        "",
        "## 注意事项",
        "",
        "- `results/bkp_gpqa_20260425/` 下的备份产物已排除。",
        "- 除非存在重复独立样本，否则不声称 `pass^k`。",
        "- 正确/错误行为差异只在 `valid_scored` 记录上计算；运行错误单独作为 failure-cost 与 readiness 证据。",
        "",
        "## 输出文件",
        "",
        _markdown_output_files(output_files),
        "",
    ]
    return "\n".join(lines)


def write_phase14_outputs(
    output_dir: Path,
    *,
    inventory: Mapping[str, Any],
    event_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    case_studies: Sequence[Mapping[str, Any]],
) -> dict[str, Path]:
    """Write all machine-readable and human-facing Phase 14 outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "corpus_inventory": output_dir / "corpus_inventory.json",
        "action_events": output_dir / "action_events.csv",
        "trajectory_metrics": output_dir / "trajectory_metrics.json",
        "case_studies": output_dir / "case_studies.json",
        "latex_appendix": output_dir / "gpqa_trajectory_appendix.tex",
        "chinese_discussion": output_dir / "gpqa_trajectory_discussion_zh.md",
    }
    output_refs = {name: path for name, path in paths.items()}
    latex = render_latex_appendix(inventory, metrics, case_studies, output_files=output_refs)
    chinese = render_chinese_discussion(inventory, metrics, case_studies, output_files=output_refs)

    _write_json(_jsonable(inventory), paths["corpus_inventory"])
    _write_csv(event_rows, paths["action_events"])
    _write_json(_jsonable(metrics), paths["trajectory_metrics"])
    _write_json({"case_studies": _jsonable(list(case_studies))}, paths["case_studies"])
    paths["latex_appendix"].write_text(latex, encoding="utf-8")
    paths["chinese_discussion"].write_text(chinese, encoding="utf-8")
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
    names = ", ".join(f"\\texttt{{{_tex(name)}}}" for name in metrics.get("metric_names", []))
    diagnostics = metrics.get("diagnostics", {}) if isinstance(metrics.get("diagnostics"), Mapping) else {}
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
    diagnostics = metrics.get("diagnostics", {}) if isinstance(metrics.get("diagnostics"), Mapping) else {}
    names = ", ".join(f"`{name}`" for name in metrics.get("metric_names", []))
    return "\n".join(
        [
            f"- 指标族：{names}",
            f"- 轨迹数：{int(diagnostics.get('sequence_count') or 0)}；valid-scored 轨迹数：{int(diagnostics.get('valid_scored_sequence_count') or 0)}",
        ]
    )


def _markdown_case_studies(case_studies: Sequence[Mapping[str, Any]]) -> str:
    if not case_studies:
        return "暂无可选案例。"
    lines = []
    for case in case_studies[:8]:
        motifs = ", ".join(str(motif) for motif in case.get("motifs", [])) or "none"
        snippets = case.get("evidence_snippets") if isinstance(case.get("evidence_snippets"), list) else []
        snippet = str(snippets[0]) if snippets else ""
        lines.append(
            f"- `{case.get('harness')}` / `{case.get('task_id')}`: score={case.get('score_status')}, correct={case.get('correct')}, motifs={motifs}. {snippet}"
        )
    return "\n".join(lines)


def _markdown_output_files(output_files: Mapping[str, Path | str]) -> str:
    return "\n".join(f"- `{name}`: `{_path_text(path)}`" for name, path in sorted(output_files.items()))


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
        return {str(key): _jsonable(item) for key, item in value.items() if key != "bundle"}
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
