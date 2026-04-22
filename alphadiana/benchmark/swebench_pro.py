"""SWE-bench Pro benchmark loader."""
from __future__ import annotations

import logging
import os
from typing import Any

from alphadiana.benchmark.base import Benchmark, BenchmarkTask, load_dataset_with_retry
from alphadiana.benchmark.registry import BenchmarkRegistry

logger = logging.getLogger(__name__)

DEFAULT_SMOKE_INSTANCE_IDS = [
    "instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan",
    "instance_qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111-v059c6fdc75567943479b23ebca7c07b5e9a7f34c",
    "instance_ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5",
    "instance_internetarchive__openlibrary-4a5d2a7d24c9e4c11d3069220c0685b736d5ecde-v13642507b4fc1f8d234172bf8129942da2c2ca26",
    "instance_gravitational__teleport-3fa6904377c006497169945428e8197158667910-v626ec2a48416b10a88641359a169d99e935ff037",
]

REQUIRED_ROW_KEYS = ("repo", "instance_id", "base_commit", "problem_statement")


def _normalize_str_list(value: Any) -> list[str]:
    """Normalize a config value into a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                normalized.append(text)
        return normalized
    text = str(value).strip()
    return [text] if text else []


def _select_rows_by_ids(rows: list[dict[str, Any]], wanted_ids: list[str]) -> list[dict[str, Any]]:
    """Return rows in the exact order of wanted_ids, skipping missing IDs."""
    if not wanted_ids:
        return rows
    by_id = {str(row.get("instance_id", "")): row for row in rows}
    return [by_id[instance_id] for instance_id in wanted_ids if instance_id in by_id]


def _select_requested_rows_by_ids(
    rows: list[dict[str, Any]], wanted_ids: list[str], *, label: str
) -> list[dict[str, Any]]:
    """Return requested rows in order and fail if explicit IDs are absent."""
    if not wanted_ids:
        return rows
    by_id = {str(row.get("instance_id", "")): row for row in rows}
    missing = set(wanted_ids) - set(by_id)
    if missing:
        preview = sorted(missing)[:10]
        suffix = "..." if len(missing) > 10 else ""
        raise LookupError(
            f"SWE-bench Pro: requested {label} instance_ids not in dataset: {preview}{suffix}"
        )
    return [by_id[instance_id] for instance_id in wanted_ids]


class SWEBenchProBenchmark(Benchmark):
    """Loads SWE-bench Pro issue instances from Hugging Face.

    Config keys:
        dataset: HuggingFace dataset path (default: "ScaleAI/SWE-bench_Pro")
        split: Dataset split (default: "test")
        repos: Optional repo name or list of repo names to keep
        instance_ids: Optional instance_id or list of instance_ids to keep
        max_tasks: Optional cap applied after all filtering
        subset: "smoke" or "all" (default: "smoke")
        smoke_instance_ids: Optional override list for the smoke subset
    """

    name = "swebench_pro_os"

    def default_scorer(self) -> str:
        return "swebench_pro"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        try:
            from datasets import load_dataset
        except ImportError:
            raise RuntimeError(
                "The 'datasets' library is required for swebench_pro_os benchmark. "
                "Install with: pip install datasets"
            )

        dataset_path = str(config.get("dataset", "ScaleAI/SWE-bench_Pro")).strip()
        split = str(config.get("split", "test")).strip() or "test"
        subset = str(config.get("subset", "smoke")).strip().lower() or "smoke"
        if subset not in {"smoke", "all"}:
            raise ValueError(
                "swebench_pro_os benchmark requires subset to be exactly "
                "'smoke' or 'all'."
            )

        repos = set(_normalize_str_list(config.get("repos")))
        instance_ids = _normalize_str_list(config.get("instance_ids"))
        smoke_instance_ids = _normalize_str_list(config.get("smoke_instance_ids"))
        max_tasks = config.get("max_tasks")
        if max_tasks == 0:
            return []

        try:
            dataset = load_dataset_with_retry(dataset_path, split=split)
        except Exception as exc:
            hf_endpoint = os.environ.get("HF_ENDPOINT", "").strip()
            raise RuntimeError(
                "Failed to load SWE-bench Pro dataset from Hugging Face. "
                "If direct access is unavailable, source `scripts/rock_env.sh` first "
                "or set `HF_ENDPOINT=https://hf-mirror.com` and retry. "
                f"Current HF_ENDPOINT={hf_endpoint or '<unset>'}. "
                f"Original error: {exc}"
            ) from exc

        if len(dataset) == 0:
            return []

        rows = [dict(row) for row in dataset]
        sample = rows[0]
        missing_sample_keys = [key for key in REQUIRED_ROW_KEYS if key not in sample]
        if missing_sample_keys:
            available = ", ".join(sorted(sample.keys()))
            raise KeyError(
                "SWE-bench Pro dataset missing required fields "
                f"{missing_sample_keys}. Available fields: {available}"
            )

        if repos:
            rows = [row for row in rows if str(row.get("repo", "")) in repos]

        if instance_ids:
            rows = _select_requested_rows_by_ids(rows, instance_ids, label="explicit")

        if subset == "smoke":
            active_smoke_ids = smoke_instance_ids or DEFAULT_SMOKE_INSTANCE_IDS
            if smoke_instance_ids:
                rows = _select_requested_rows_by_ids(rows, active_smoke_ids, label="smoke")
            else:
                rows = _select_rows_by_ids(rows, active_smoke_ids)

        if max_tasks is not None:
            rows = rows[: int(max_tasks)]

        tasks: list[BenchmarkTask] = []
        for row in rows:
            missing_row_keys = [key for key in REQUIRED_ROW_KEYS if not row.get(key)]
            if missing_row_keys:
                row_id = row.get("instance_id", "<missing-instance-id>")
                raise KeyError(
                    f"SWE-bench Pro row {row_id!r} missing required values for "
                    f"{missing_row_keys}"
                )

            instance_id = str(row["instance_id"])
            repo = str(row["repo"])
            base_commit = str(row["base_commit"])
            problem_statement = str(row["problem_statement"])

            tasks.append(BenchmarkTask(
                task_id=row["instance_id"],
                problem=row["problem_statement"],
                ground_truth={
                    "instance_id": instance_id,
                    "repo": repo,
                    "base_commit": base_commit,
                },
                metadata={
                    "source": dataset_path,
                    "split": split,
                    "repo": repo,
                    "instance_id": instance_id,
                    "base_commit": base_commit,
                    "repo_language": row.get("repo_language", ""),
                    "requirements": row.get("requirements", ""),
                    "interface": row.get("interface", ""),
                    "fail_to_pass": row.get("fail_to_pass", []),
                    "pass_to_pass": row.get("pass_to_pass", []),
                    "before_repo_set_cmd": row.get("before_repo_set_cmd", ""),
                    "selected_test_files_to_run": row.get("selected_test_files_to_run", []),
                    "dockerhub_tag": row.get("dockerhub_tag", ""),
                },
            ))

        logger.info(
            "Loaded %d swebench_pro_os tasks (subset=%s, repos=%s, instance_ids=%s, max_tasks=%s)",
            len(tasks),
            subset,
            sorted(repos),
            instance_ids,
            max_tasks,
        )
        return tasks


BenchmarkRegistry.register("swebench_pro_os", SWEBenchProBenchmark)
