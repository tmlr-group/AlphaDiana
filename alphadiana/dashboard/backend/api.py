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


@router.get("/jobs", response_model=list[JobStatus])
def list_jobs():
    """List all evaluation jobs (running and completed)."""
    return _get_job_manager().list_jobs()


@router.post("/jobs", response_model=JobStatus, status_code=201)
def create_job(req: CreateJobRequest):
    """Create and start a new evaluation job."""
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
