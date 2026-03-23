"""FastAPI routes for the dashboard API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from alphadiana.dashboard.backend.data_loader import DataLoader
from alphadiana.dashboard.backend.job_manager import JobManager
from alphadiana.dashboard.backend.models import (
    CompareRunEntry,
    CreateJobRequest,
    DeployAndRunRequest,
    JobStatus,
    RunDetailResponse,
    RunSummaryResponse,
    TaskResult,
)
from alphadiana.dashboard.backend.sandbox_manager import SandboxManager

router = APIRouter(prefix="/api")

# Initialized by main.py
loader: DataLoader | None = None
job_manager: JobManager | None = None
sandbox_manager: SandboxManager | None = None


def init_loader(data_loader: DataLoader) -> None:
    global loader
    loader = data_loader


def init_job_manager(manager: JobManager) -> None:
    global job_manager
    job_manager = manager


def init_sandbox_manager(manager: SandboxManager) -> None:
    global sandbox_manager
    sandbox_manager = manager


def _get_loader() -> DataLoader:
    if loader is None:
        raise RuntimeError("DataLoader not initialized")
    return loader


def _get_job_manager() -> JobManager:
    if job_manager is None:
        raise RuntimeError("JobManager not initialized")
    return job_manager


def _get_sandbox_manager() -> SandboxManager:
    if sandbox_manager is None:
        raise RuntimeError("SandboxManager not initialized")
    return sandbox_manager


@router.get("/runs", response_model=list[RunSummaryResponse])
def list_runs():
    """List all evaluation runs with summary statistics."""
    return _get_loader().list_runs()


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(run_id: str):
    """Get full detail for a single evaluation run."""
    detail = _get_loader().get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return detail


@router.get("/runs/{run_id}/config")
def get_run_config(run_id: str):
    """Get the saved YAML config for a run (used by NewJobPage to auto-fill form on resume)."""
    dl = _get_loader()
    config = dl._load_config(run_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"No config found for run '{run_id}'")
    return config


@router.get("/runs/{run_id}/tasks/{task_id}", response_model=TaskResult)
def get_task(run_id: str, task_id: str):
    """Get a single task result with full trajectory."""
    result = _get_loader().get_task(run_id, task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found in run '{run_id}'")
    return result


@router.get("/compare", response_model=list[CompareRunEntry])
def compare_runs(runs: str = Query(..., description="Comma-separated run IDs")):
    """Compare multiple evaluation runs side by side."""
    run_ids = [r.strip() for r in runs.split(",") if r.strip()]
    if len(run_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 run IDs required for comparison")
    return _get_loader().compare_runs(run_ids)


# --- Environment variable helpers ---


def _load_dotenv() -> dict[str, str]:
    """Read .env file from project root and return key-value pairs."""
    from pathlib import Path
    env_path = Path(__file__).resolve().parents[3] / ".env"
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def resolve_env_ref(value: str) -> str:
    """If value starts with '$', resolve it from .env or os.environ."""
    import os
    if not value.startswith("$"):
        return value
    var_name = value[1:]
    dotenv = _load_dotenv()
    resolved = dotenv.get(var_name) or os.environ.get(var_name) or ""
    return resolved


# Known API base URL patterns -> env var name (mirrors frontend API_BASE_ENV_MAP)
_API_BASE_ENV_MAP = [
    ("openrouter.ai", "OPENROUTER_API_KEY"),
    ("api.openai.com", "OPENAI_API_KEY"),
    ("siliconflow.cn", "SILICONFLOW_API_KEY"),
    ("volces.com", "ARK_API_KEY"),
    ("volcengine.com", "ARK_API_KEY"),
    ("api.deepseek.com", "DEEPSEEK_API_KEY"),
    ("api.anthropic.com", "ANTHROPIC_API_KEY"),
    ("api.moonshot.cn", "MOONSHOT_API_KEY"),
    ("dashscope.aliyuncs.com", "DASHSCOPE_API_KEY"),
    ("api.together.xyz", "TOGETHER_API_KEY"),
]


def _resolve_key_from_api_base(api_base: str) -> str:
    """Try to resolve an API key from .env by matching the API base URL pattern."""
    import os as _os
    if not api_base:
        return ""
    dotenv = _load_dotenv()
    for pattern, env_var in _API_BASE_ENV_MAP:
        if pattern in api_base:
            val = dotenv.get(env_var) or _os.environ.get(env_var) or ""
            if val:
                return val
    return ""


def _stash_env_refs(req: CreateJobRequest) -> None:
    """Save $VAR_NAME references in metadata before they get resolved.

    This allows the resume endpoint to re-resolve env vars from .env/os.environ
    instead of storing the actual secret values.
    """
    env_refs: dict[str, str] = {}
    for key in ("api_key", "gateway_token"):
        val = req.agent_config.get(key)
        if isinstance(val, str) and val.startswith("$"):
            env_refs[key] = val
    if env_refs:
        req.metadata["_env_refs"] = env_refs


def _validate_openclaw_submission_mode(req: CreateJobRequest) -> None:
    """No-op: kept for API compatibility. OpenClaw now always uses deploy-and-run."""
    pass


@router.get("/env-keys")
def list_env_keys():
    """List environment variable names from .env that look like API keys/tokens.

    Only reads from the project .env file (not all of os.environ) to avoid
    leaking unrelated system variable names. Returns only names, never values.
    """
    dotenv = _load_dotenv()
    key_names: list[str] = []
    for name in dotenv:
        upper = name.upper()
        if any(kw in upper for kw in ("KEY", "TOKEN", "SECRET")):
            key_names.append(name)
    key_names.sort()
    return {"keys": key_names}


# --- Job management endpoints ---


@router.get("/jobs")
def list_jobs():
    """List all evaluation jobs (running and completed).

    Excludes original_request from the response to keep the payload small.
    Use GET /jobs/{job_id} to get the full job details including original_request.
    """
    mgr = _get_job_manager()
    for job in mgr.list_jobs():
        if job.status in ("running", "pending"):
            mgr.refresh_progress(job.job_id)
    jobs = mgr.list_jobs()
    return [job.model_dump(exclude={"original_request"}) for job in jobs]


@router.post("/jobs", response_model=JobStatus, status_code=201)
def create_job(req: CreateJobRequest):
    """Create and start a new evaluation job."""
    _validate_openclaw_submission_mode(req)
    # Stash original $VAR_NAME references before resolution (for resume)
    _stash_env_refs(req)
    # Resolve $ENV_VAR references in agent config
    for key in ("api_key", "gateway_token"):
        if key in req.agent_config and isinstance(req.agent_config[key], str):
            req.agent_config[key] = resolve_env_ref(req.agent_config[key])
    return _get_job_manager().create_job(req)


@router.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    """Get status of a specific job."""
    mgr = _get_job_manager()
    mgr.refresh_progress(job_id)
    job = mgr.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Cancel a running job."""
    if not _get_job_manager().cancel_job(job_id):
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")
    return {"status": "cancelled"}


@router.post("/jobs/{job_id}/resume", response_model=JobStatus, status_code=201)
def resume_job(job_id: str):
    """Resume an interrupted or failed job using its saved config.

    Creates a new job with the same config and run_id, which will
    automatically pick up where the previous run left off (checkpoint resume).
    Tries original_request first, then falls back to the saved YAML config.
    """
    mgr = _get_job_manager()
    dl = _get_loader()
    old_job = mgr.get_job(job_id)
    if old_job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if old_job.status not in ("interrupted", "failed", "completed"):
        raise HTTPException(status_code=400, detail=f"Job is {old_job.status}, cannot resume")

    req_data: dict | None = None

    if old_job.original_request:
        req_data = dict(old_job.original_request)
        # Restore $VAR_NAME references from stashed env_refs
        env_refs = req_data.get("metadata", {}).get("_env_refs", {})
        agent_cfg = req_data.get("agent_config", {})
        for key, var_ref in env_refs.items():
            agent_cfg[key] = var_ref
    else:
        # Fall back to saved YAML config
        req_data = _reconstruct_request_from_yaml(dl, old_job.run_id)

    if not req_data:
        raise HTTPException(
            status_code=400,
            detail="No saved config for this job. Please re-submit manually.",
        )

    # Force the same run_id and disable redo_all for resume
    req_data["run_id"] = old_job.run_id
    req_data["redo_all"] = False

    # If api_key is masked (contains '...'), try to auto-resolve from .env
    agent_cfg = req_data.get("agent_config", {})
    api_key = agent_cfg.get("api_key", "")
    if isinstance(api_key, str) and "..." in api_key:
        resolved = _resolve_key_from_api_base(agent_cfg.get("api_base", ""))
        if resolved:
            agent_cfg["api_key"] = resolved
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot resume: API key is masked and no matching env var found in .env. "
                    "Please add the appropriate API key to your .env file "
                    "(e.g. SILICONFLOW_API_KEY=sk-xxx) and try again."
                ),
            )

    req = CreateJobRequest(**req_data)

    # Stash env refs again for the new job, then resolve
    _stash_env_refs(req)
    for key in ("api_key", "gateway_token"):
        if key in req.agent_config and isinstance(req.agent_config[key], str):
            req.agent_config[key] = resolve_env_ref(req.agent_config[key])

    # Capture old logs before removing the job so they can be carried over
    old_logs = mgr.get_raw_log_content(job_id)

    # For OpenClaw deploy-and-run jobs, always re-deploy sandboxes.
    # The original sandbox may no longer be alive after an interruption,
    # and if interrupted during deploy (pending state), agent_config won't
    # have api_base / gateway_pool at all — running create_job directly would fail.
    meta = req.metadata if isinstance(req.metadata, dict) else {}
    if req.agent_name == "openclaw" and meta.get("deploy_and_run"):
        model_api_base = str(meta.get("model_api_base", ""))
        model_name = str(meta.get("base_model", ""))
        # 1 sandbox per task (CONCURRENCY_PER_SANDBOX = 1)
        num_sandboxes = max(1, req.max_concurrent)

        # Resolve the model API key from .env using the API base URL pattern
        model_api_key = _resolve_key_from_api_base(model_api_base)
        if not model_api_key:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot resume: sandbox model API key not found in .env. "
                    f"Add the appropriate key for '{model_api_base}' "
                    "(e.g. ARK_API_KEY=...) and try again."
                ),
            )

        # Strip stale gateway info — new sandboxes will be deployed
        req.agent_config.pop("api_base", None)
        req.agent_config.pop("gateway_pool", None)
        # Remove old deploy_id; it will be replaced by the new deploy
        req.metadata.pop("deploy_id", None)

        sandbox_mgr = _get_sandbox_manager()
        deploy_job = sandbox_mgr.deploy_sandbox(
            model_api_base=model_api_base,
            model_api_key=model_api_key,
            model_name=model_name,
            num_sandboxes=num_sandboxes,
        )
        req.metadata["deploy_id"] = deploy_job.deploy_id

        mgr.remove_job(job_id)
        new_job = mgr.create_job_after_deploy(
            req=req,
            sandbox_manager=sandbox_mgr,
            deploy_id=deploy_job.deploy_id,
        )
    else:
        # Remove the old job entry FIRST to avoid any window of duplicate run_ids
        mgr.remove_job(job_id)
        new_job = mgr.create_job(req)

    # Carry over old logs with a separator so history is preserved
    if old_logs:
        from datetime import datetime as _dt
        now_str = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        separator = (
            "\n" + "─" * 64 + "\n"
            f"  Resumed at {now_str}\n"
            + "─" * 64 + "\n\n"
        )
        mgr.set_log_prefix(new_job.job_id, old_logs + separator)

    return new_job


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    """Delete a job entry (not the run data). Only non-running jobs can be deleted."""
    mgr = _get_job_manager()
    job = mgr.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if job.status in ("running", "pending"):
        raise HTTPException(status_code=400, detail=f"Cannot delete a {job.status} job. Cancel it first.")
    mgr.remove_job(job_id)
    return {"status": "deleted", "job_id": job_id}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, confirm: bool = Query(False)):
    """Delete a run and all its data (JSONL, YAML, associated jobs, logs).

    Requires ?confirm=true to proceed.
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass ?confirm=true to confirm deletion")
    mgr = _get_job_manager()
    try:
        result = mgr.delete_run_data(run_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


def _reconstruct_request_from_yaml(dl: DataLoader, run_id: str) -> dict | None:
    """Reconstruct a CreateJobRequest dict from a saved YAML config file."""
    config = dl._load_config(run_id)
    if not config:
        return None
    agent_cfg = config.get("agent", {})
    bench_cfg = config.get("benchmark", {})
    scorer_cfg = config.get("scorer", {})
    return {
        "run_id": run_id,
        "agent_name": agent_cfg.get("name", ""),
        "agent_version": agent_cfg.get("version", ""),
        "agent_config": agent_cfg.get("config", {}),
        "benchmark_name": bench_cfg.get("name", ""),
        "benchmark_config": bench_cfg.get("config", {}),
        "scorer_name": scorer_cfg.get("name", ""),
        "scorer_config": scorer_cfg.get("config", {}),
        "max_concurrent": config.get("max_concurrent", 1),
        "num_samples": config.get("num_samples", 1),
        "redo_all": False,
        "metadata": config.get("metadata", {}),
    }


@router.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: str):
    """Get captured log output for a job."""
    logs = _get_job_manager().get_job_logs(job_id)
    if logs is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return {"job_id": job_id, "logs": logs}


# --- Sandbox management endpoints ---


@router.get("/sandboxes")
async def list_sandboxes():
    """List existing ROCK sandboxes."""
    mgr = _get_sandbox_manager()
    sandboxes = await mgr.list_sandboxes()
    return [s.to_dict() for s in sandboxes]


@router.get("/sandboxes/{sandbox_id}/probe")
async def probe_sandbox(sandbox_id: str):
    """Probe a sandbox for gateway health and model info (slow, on-demand)."""
    mgr = _get_sandbox_manager()
    return await mgr.probe_sandbox(sandbox_id)


@router.post("/sandboxes/deploy", status_code=201)
def deploy_sandbox(
    model_api_base: str = "",
    model_api_key: str = "",
    model_name: str = "",
    agent_config_path: str | None = None,
    auto_clear_seconds: int = 28800,
):
    """Start a new sandbox deployment."""
    model_api_key = resolve_env_ref(model_api_key)
    mgr = _get_sandbox_manager()
    job = mgr.deploy_sandbox(
        agent_config_path=agent_config_path,
        model_api_base=model_api_base,
        model_api_key=model_api_key,
        model_name=model_name,
        auto_clear_seconds=auto_clear_seconds,
    )
    return job.to_dict()


@router.get("/sandboxes/deploy/{deploy_id}")
def get_deploy_status(deploy_id: str):
    """Get status of a sandbox deployment."""
    mgr = _get_sandbox_manager()
    job = mgr.get_deploy_job(deploy_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Deploy job '{deploy_id}' not found")
    return job.to_dict()


@router.get("/sandboxes/deploy/{deploy_id}/logs")
def get_deploy_logs(deploy_id: str):
    """Get logs from a sandbox deployment."""
    mgr = _get_sandbox_manager()
    logs = mgr.get_deploy_logs(deploy_id)
    if logs is None:
        raise HTTPException(status_code=404, detail=f"Deploy job '{deploy_id}' not found")
    return {"deploy_id": deploy_id, "logs": logs}


@router.get("/sandboxes/deploys")
def list_deploys():
    """List all sandbox deploy jobs."""
    mgr = _get_sandbox_manager()
    return mgr.list_deploy_jobs()


@router.post("/jobs/deploy-and-run", status_code=201)
def deploy_and_run(req: DeployAndRunRequest):
    """Deploy a sandbox and automatically start an evaluation when ready.

    Returns a job_id immediately. The job stays in "pending" while the sandbox
    is being deployed, then transitions to "running" once the eval starts.
    """
    sandbox_mgr = _get_sandbox_manager()
    job_mgr = _get_job_manager()

    # Resolve $ENV_VAR references
    req.model_api_key = resolve_env_ref(req.model_api_key)
    for key in ("api_key", "gateway_token"):
        if key in req.agent_config and isinstance(req.agent_config[key], str):
            req.agent_config[key] = resolve_env_ref(req.agent_config[key])

    # Start sandbox deploy
    deploy_job = sandbox_mgr.deploy_sandbox(
        model_api_base=req.model_api_base,
        model_api_key=req.model_api_key,
        model_name=req.model_name,
        auto_clear_seconds=req.auto_clear_seconds,
        num_sandboxes=max(1, req.num_sandboxes),
    )

    # Create eval job in pending state — it will wait for deploy to finish
    job_req = CreateJobRequest(
        run_id=req.run_id,
        agent_name="openclaw",
        agent_version=req.agent_version,
        agent_config=req.agent_config,
        benchmark_name=req.benchmark_name,
        benchmark_config=req.benchmark_config,
        scorer_name=req.scorer_name,
        scorer_config=req.scorer_config,
        max_concurrent=req.max_concurrent,
        redo_all=req.redo_all,
        num_samples=req.num_samples,
        metadata={
            **req.metadata,
            "deploy_id": deploy_job.deploy_id,
            "deploy_and_run": True,
            "base_model": req.model_name,
            "model_api_base": req.model_api_base,
        },
    )

    job_status = job_mgr.create_job_after_deploy(
        req=job_req,
        sandbox_manager=sandbox_mgr,
        deploy_id=deploy_job.deploy_id,
    )

    return {
        "job_id": job_status.job_id,
        "run_id": job_status.run_id,
        "deploy_id": deploy_job.deploy_id,
        "status": job_status.status,
    }
