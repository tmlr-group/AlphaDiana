"""Pydantic response schemas for the dashboard API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RunSummaryResponse(BaseModel):
    run_id: str
    agent: str
    agent_version: str
    benchmark: str
    total_tasks: int
    completed: int
    failed: int
    accuracy: float
    accuracy_total: float = 0.0
    mean_score: float
    mean_wall_time_sec: float
    total_tokens: dict[str, int]
    per_category: dict[str, float]
    error_distribution: dict[str, int] = {}
    model: str = ""
    num_samples: int = 1
    pass_at_k: float = 0.0
    avg_at_k: float = 0.0
    per_category_pass_at_k: dict[str, float] = {}
    per_category_avg_at_k: dict[str, float] = {}
    timestamp: str = ""


class TaskResult(BaseModel):
    task_id: str
    sample_index: int = 0
    problem: str = ""
    ground_truth: Any = None
    predicted: Any = None
    correct: bool | None = None
    score: float | None = None
    rationale: str = ""
    wall_time_sec: float = 0.0
    token_usage: dict[str, int] = {}
    trajectory: list[dict[str, Any]] = []
    raw_output: str = ""
    timestamp: str = ""
    error: dict[str, Any] | None = None
    task_metadata: dict[str, Any] = {}
    finish_reason: str = ""


class RunDetailResponse(BaseModel):
    summary: RunSummaryResponse
    config: dict[str, Any] | None = None
    results: list[TaskResult]


class CompareRunEntry(BaseModel):
    run_id: str
    summary: RunSummaryResponse
    results_by_task: dict[str, TaskResult]


# --- Job management models ---


class CreateJobRequest(BaseModel):
    """Request body for creating a new evaluation job."""

    run_id: str = ""
    agent_name: str  # "direct_llm" or "openclaw"
    agent_version: str = ""
    agent_config: dict[str, Any] = {}
    benchmark_name: str  # "aime", "math", "frontier_math", etc.
    benchmark_config: dict[str, Any] = {}
    scorer_name: str = ""  # defaults to benchmark's default scorer
    scorer_config: dict[str, Any] = {}
    sandbox_name: str | None = None
    sandbox_config: dict[str, Any] = {}
    max_concurrent: int = 1
    redo_all: bool = False
    num_samples: int = 1
    metadata: dict[str, Any] = {}


class JobStatus(BaseModel):
    """Status of a running or completed evaluation job."""

    job_id: str
    run_id: str
    status: str  # "pending", "running", "completed", "failed"
    agent: str = ""
    benchmark: str = ""
    model: str = ""
    progress: int = 0  # completed tasks
    total_tasks: int = 0
    accuracy: float | None = None
    error: str | None = None
    created_at: str = ""
    # Original request config (persisted for resume/continue)
    original_request: dict[str, Any] | None = None


class DeployAndRunRequest(BaseModel):
    """Request that deploys a sandbox first, then starts an evaluation."""

    # Deploy config
    model_api_base: str = ""
    model_api_key: str = ""
    model_name: str = ""
    auto_clear_seconds: int = 28800
    num_sandboxes: int = 1  # number of sandboxes to deploy in parallel

    # Eval config (same as CreateJobRequest but without api_base)
    run_id: str = ""
    agent_version: str = ""
    agent_config: dict[str, Any] = {}
    benchmark_name: str = "aime"
    benchmark_config: dict[str, Any] = {}
    scorer_name: str = ""
    scorer_config: dict[str, Any] = {}
    max_concurrent: int = 1
    redo_all: bool = False
    num_samples: int = 1
    metadata: dict[str, Any] = {}
