"""Background job manager for evaluation runs."""

from __future__ import annotations

import contextvars
import io
import logging
import threading
import uuid
from datetime import datetime, timezone

from alphadiana.dashboard.backend.models import CreateJobRequest, JobStatus

logger = logging.getLogger(__name__)

# Context variable to tag the current job ID.  Inherited by
# ThreadPoolExecutor workers (Python 3.12+), so logs from worker
# threads are correctly routed to their owning job's buffer.
_current_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_job_id", default=None
)


class _JobLogCapture(logging.Handler):
    """Captures log messages for a specific job into a StringIO buffer.

    Uses a contextvars-based job ID to ensure only logs from the owning
    job (including its ThreadPoolExecutor workers) are captured, preventing
    cross-contamination between concurrent jobs.
    """

    def __init__(self, buffer: io.StringIO, job_id: str) -> None:
        super().__init__(level=logging.DEBUG)
        self._buffer = buffer
        self._lock_obj = threading.Lock()
        self._job_id = job_id

    def emit(self, record: logging.LogRecord) -> None:
        # Only capture log records belonging to this job
        if _current_job_id.get() != self._job_id:
            return
        try:
            msg = self.format(record)
            with self._lock_obj:
                self._buffer.write(msg + "\n")
        except Exception:
            pass


class JobManager:
    """Manages background evaluation jobs."""

    def __init__(self, results_dir: str, configs_dir: str | None = None) -> None:
        self._results_dir = results_dir
        self._configs_dir = configs_dir
        self._jobs: dict[str, JobStatus] = {}
        self._job_logs: dict[str, io.StringIO] = {}
        self._lock = threading.Lock()

    def list_jobs(self) -> list[JobStatus]:
        with self._lock:
            return list(reversed(self._jobs.values()))

    def get_job(self, job_id: str) -> JobStatus | None:
        with self._lock:
            return self._jobs.get(job_id)

    def get_job_logs(self, job_id: str) -> str | None:
        with self._lock:
            buf = self._job_logs.get(job_id)
            if buf is None:
                return None
            return buf.getvalue()

    def create_job(self, req: CreateJobRequest) -> JobStatus:
        """Create and start a new evaluation job in a background thread."""
        job_id = uuid.uuid4().hex[:12]
        run_id = req.run_id or f"{req.agent_name}-{req.benchmark_name}-{job_id}"

        benchmark_label = req.metadata.get("benchmark_label", req.benchmark_name)
        # Extract model name from agent config
        model = req.agent_config.get("model", "")
        status = JobStatus(
            job_id=job_id,
            run_id=run_id,
            status="pending",
            agent=req.agent_name,
            benchmark=benchmark_label,
            model=model,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._jobs[job_id] = status
            self._job_logs[job_id] = io.StringIO()

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, run_id, req),
            daemon=True,
        )
        thread.start()
        return status

    def create_job_after_deploy(
        self,
        req: "CreateJobRequest",
        sandbox_manager: "Any",
        deploy_id: str,
    ) -> JobStatus:
        """Create a job that waits for sandbox deploy to complete first."""
        import time as _time

        job_id = uuid.uuid4().hex[:12]
        run_id = req.run_id or f"{req.agent_name}-{req.benchmark_name}-{job_id}"
        benchmark_label = req.metadata.get("benchmark_label", req.benchmark_name)
        model = req.agent_config.get("model", "") or req.metadata.get("base_model", "")

        status = JobStatus(
            job_id=job_id,
            run_id=run_id,
            status="pending",
            agent=req.agent_name,
            benchmark=benchmark_label,
            model=model,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._jobs[job_id] = status
            self._job_logs[job_id] = io.StringIO()

        def _wait_and_run() -> None:
            # Tag this thread with the job ID for log isolation
            _current_job_id.set(job_id)

            # Set up log capture
            log_buf = self._job_logs.get(job_id, io.StringIO())
            handler = _JobLogCapture(log_buf, job_id)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            ))
            root_logger = logging.getLogger("alphadiana")
            root_logger.addHandler(handler)

            try:
                # Write deploy wait logs to the job log buffer
                def _log(msg: str) -> None:
                    ts = datetime.now().strftime("%H:%M:%S")
                    with self._lock:
                        log_buf.write(f"{ts} [INFO] {msg}\n")

                _log(f"Waiting for sandbox deploy {deploy_id} to complete...")

                # Poll deploy status
                deadline = _time.monotonic() + 1200  # 20 min max wait (npm install can take 5-10 min)
                while _time.monotonic() < deadline:
                    with self._lock:
                        job = self._jobs.get(job_id)
                        if job and job.status == "cancelled":
                            _log("Job cancelled during deploy wait")
                            return

                    deploy_job = sandbox_manager.get_deploy_job(deploy_id)
                    if deploy_job is None:
                        _log(f"[ERROR] Deploy job {deploy_id} not found")
                        with self._lock:
                            self._jobs[job_id].status = "failed"
                            self._jobs[job_id].error = f"Deploy job {deploy_id} not found"
                        return

                    if deploy_job.status == "completed":
                        _log(f"Sandbox deployed! API base: {deploy_job.api_base}")
                        # Inject api_base into agent config
                        req.agent_config["api_base"] = deploy_job.api_base
                        req.agent_config.setdefault("model", "openclaw")
                        break
                    elif deploy_job.status == "failed":
                        _log(f"[ERROR] Sandbox deploy failed: {deploy_job.error}")
                        with self._lock:
                            self._jobs[job_id].status = "failed"
                            self._jobs[job_id].error = f"Sandbox deploy failed: {deploy_job.error}"
                        return

                    # Forward deploy logs
                    deploy_logs = sandbox_manager.get_deploy_logs(deploy_id)
                    if deploy_logs:
                        last_line = deploy_logs.strip().split("\n")[-1] if deploy_logs.strip() else ""
                        if last_line:
                            _log(f"[deploy] {last_line}")

                    _time.sleep(5)
                else:
                    _log("[ERROR] Sandbox deploy timed out (10 min)")
                    with self._lock:
                        self._jobs[job_id].status = "failed"
                        self._jobs[job_id].error = "Sandbox deploy timed out"
                    return

                # Now run the actual evaluation
                _log("Starting evaluation...")
                self._run_job(job_id, run_id, req, _skip_log_setup=True)

            except Exception as exc:
                logger.exception("Deploy-and-run job %s failed", job_id)
                with self._lock:
                    self._jobs[job_id].status = "failed"
                    self._jobs[job_id].error = str(exc)
            finally:
                root_logger.removeHandler(handler)

        thread = threading.Thread(target=_wait_and_run, daemon=True)
        thread.start()
        return status

    def cancel_job(self, job_id: str) -> bool:
        """Mark a job as cancelled (the thread will check this flag)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.status in ("pending", "running"):
                job.status = "cancelled"
                return True
            return False

    def _run_job(self, job_id: str, run_id: str, req: CreateJobRequest, *, _skip_log_setup: bool = False) -> None:
        """Execute the evaluation in a background thread."""
        from alphadiana.config.experiment_config import ExperimentConfig
        from alphadiana.runner.runner import Runner

        # Tag this thread (and future ThreadPoolExecutor children) with the job ID
        # so that _JobLogCapture only captures logs belonging to this job.
        _current_job_id.set(job_id)

        # Set up log capture for this job (skip if caller already set it up)
        handler = None
        root_logger = logging.getLogger("alphadiana")
        if not _skip_log_setup:
            log_buf = self._job_logs.get(job_id, io.StringIO())
            handler = _JobLogCapture(log_buf, job_id)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            ))
            root_logger.addHandler(handler)
            # Ensure the logger level allows INFO messages through
            if root_logger.level > logging.INFO or root_logger.level == logging.NOTSET:
                root_logger.setLevel(logging.INFO)

        # Helper to write directly to log buffer (always visible even if logger is quiet)
        def _log(msg: str) -> None:
            from datetime import datetime as _dt
            ts = _dt.now().strftime("%H:%M:%S")
            with self._lock:
                buf = self._job_logs.get(job_id)
                if buf:
                    buf.write(f"{ts} [INFO] job_manager: {msg}\n")

        with self._lock:
            self._jobs[job_id].status = "running"

        _log(f"Job {job_id} started (run_id={run_id})")
        _log(f"Agent: {req.agent_name}, Benchmark: {req.benchmark_name}")
        def _mask(k: str, v: object) -> object:
            if 'key' in k.lower() and isinstance(v, str) and len(v) > 12:
                return f"{v[:6]}...{v[-4:]}"
            elif 'key' in k.lower():
                return '***'
            return v
        _log(f"Agent config: { {k: _mask(k, v) for k, v in req.agent_config.items()} }")

        try:
            config = ExperimentConfig(
                run_id=run_id,
                agent_name=req.agent_name,
                agent_version=req.agent_version,
                agent_config=req.agent_config,
                benchmark_name=req.benchmark_name,
                benchmark_config=req.benchmark_config,
                scorer_name=req.scorer_name or "",
                scorer_config=req.scorer_config,
                sandbox_name=req.sandbox_name,
                sandbox_config=req.sandbox_config,
                max_concurrent=req.max_concurrent,
                output_dir=self._results_dir,
                redo_all=req.redo_all,
                num_samples=req.num_samples,
                metadata=req.metadata,
            )

            # Save config as YAML so RunDetail page can display it
            self._save_config(run_id, config)

            _log("Setting up runner...")
            runner = Runner(config)
            runner.setup()
            _log("Runner ready. Tasks will be loaded when evaluation starts.")

            with self._lock:
                job = self._jobs[job_id]
                if job.status == "cancelled":
                    _log("Job cancelled before execution")
                    return

            # Pre-evaluation gateway health check for openclaw agents
            api_base = req.agent_config.get("api_base", "")
            if req.agent_name == "openclaw" and api_base:
                _log(f"Probing OpenClaw gateway at {api_base} ...")
                if not self._probe_gateway(api_base):
                    _log("[ERROR] OpenClaw gateway is not responding. Aborting job.")
                    with self._lock:
                        self._jobs[job_id].status = "failed"
                        self._jobs[job_id].error = (
                            f"OpenClaw gateway not ready at {api_base}. "
                            "Please check sandbox status before starting evaluation."
                        )
                    return
                _log("Gateway health check passed.")

            try:
                _log("Starting evaluation run...")
                summary = runner.run()
                _log(f"Evaluation complete: {summary.completed}/{summary.total_tasks} tasks, accuracy={summary.accuracy:.2%}")
                with self._lock:
                    job = self._jobs[job_id]
                    if job.status == "cancelled":
                        return
                    job.status = "completed"
                    job.progress = summary.completed
                    job.total_tasks = summary.total_tasks
                    job.accuracy = summary.accuracy
                    # If all tasks failed, surface the error
                    if summary.completed == 0 and summary.total_tasks > 0:
                        job.status = "failed"
                        job.error = f"All {summary.total_tasks} tasks failed. Check logs for details."
                    elif summary.failed > 0:
                        job.error = f"{summary.failed}/{summary.total_tasks} tasks failed"
            finally:
                runner.teardown()

        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            _log(f"[ERROR] Job failed: {exc}")
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.error = str(exc)
        finally:
            if handler is not None:
                root_logger.removeHandler(handler)

    def _save_config(self, run_id: str, config) -> None:
        """Save ExperimentConfig as YAML so the RunDetail page can load it."""
        try:
            import yaml
            from pathlib import Path

            configs_dir = Path(self._configs_dir) if self._configs_dir else None
            if not configs_dir:
                return
            configs_dir.mkdir(parents=True, exist_ok=True)
            config_path = configs_dir / f"{run_id}.yaml"
            if config_path.exists():
                return  # Don't overwrite existing config

            # Build a serializable dict from ExperimentConfig.
            # Use a copy of agent_config to avoid mutating the original
            # when masking API keys below.
            import copy
            data = {
                "run_id": config.run_id,
                "agent": {
                    "name": config.agent_name,
                    "version": getattr(config, "agent_version", ""),
                    "config": copy.deepcopy(config.agent_config),
                },
                "benchmark": {
                    "name": config.benchmark_name,
                    "config": config.benchmark_config,
                },
                "scorer": {
                    "name": getattr(config, "scorer_name", ""),
                    "config": getattr(config, "scorer_config", {}),
                },
                "max_concurrent": config.max_concurrent,
                "num_samples": getattr(config, "num_samples", 1),
                "redo_all": getattr(config, "redo_all", False),
                "metadata": getattr(config, "metadata", {}),
            }
            # Mask API keys
            agent_cfg = data["agent"]["config"]
            if isinstance(agent_cfg, dict):
                for key in list(agent_cfg.keys()):
                    if "key" in key.lower() and agent_cfg[key]:
                        v = agent_cfg[key]
                        agent_cfg[key] = f"{v[:6]}...{v[-4:]}" if len(v) > 12 else "***"

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
        except Exception:
            logger.debug("Failed to save config for %s", run_id, exc_info=True)

    @staticmethod
    def _probe_gateway(api_base: str, timeout: float = 30.0, retries: int = 2) -> bool:
        """Synchronous gateway health check via chat/completions endpoint.

        Retries a few times to tolerate transient failures.
        """
        import json as _json
        import urllib.request
        import time as _time

        payload = _json.dumps({
            "model": "openclaw",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }).encode()

        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(
                    f"{api_base}/chat/completions",
                    data=payload,
                    headers={
                        "Authorization": "bearer OPENCLAW",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = _json.loads(resp.read())
                    if isinstance(body, dict) and body.get("status") == "Failed":
                        logger.warning("Gateway probe attempt %d: ROCK returned Failed", attempt + 1)
                    elif isinstance(body, dict) and body.get("choices"):
                        return True
                    else:
                        logger.warning("Gateway probe attempt %d: unexpected response: %s", attempt + 1, body)
            except Exception as exc:
                logger.warning("Gateway probe attempt %d failed: %s", attempt + 1, exc)

            if attempt < retries:
                _time.sleep(5)

        return False

    def refresh_progress(self, job_id: str) -> None:
        """Refresh job progress by reading the JSONL file and job logs."""
        import json
        import re
        from pathlib import Path

        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in ("running", "pending"):
                return
            run_id = job.run_id
            current_total = job.total_tasks

        # If we don't know total_tasks yet, try to parse it from the job logs.
        # The runner logs: "Total work items: 60 (tasks=15, num_samples=4)"
        # This includes num_samples, so progress/total_tasks are on the same scale.
        if current_total == 0:
            log_buf = self._job_logs.get(job_id)
            if log_buf:
                log_text = log_buf.getvalue()
                m = re.search(r"Total work items: (\d+)", log_text)
                if m:
                    current_total = int(m.group(1))
                else:
                    # Fallback: "Loaded N tasks from benchmark" (single-sample)
                    m = re.search(r"Loaded (\d+) tasks from benchmark", log_text)
                    if m:
                        current_total = int(m.group(1))

        jsonl_path = Path(self._results_dir) / f"{run_id}.jsonl"
        if not jsonl_path.exists():
            # Even if no JSONL yet, update total_tasks if we found it in logs
            if current_total > 0:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job:
                        job.total_tasks = current_total
            return

        count = 0
        correct = 0
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    count += 1
                    try:
                        record = json.loads(line)
                        if record.get("correct"):
                            correct += 1
                    except json.JSONDecodeError:
                        pass

        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.progress = count
                if current_total > 0:
                    job.total_tasks = current_total
                if count > 0:
                    job.accuracy = correct / count
