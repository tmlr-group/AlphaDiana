"""Background job manager for evaluation runs."""

from __future__ import annotations

import contextvars
import hashlib
import io
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from alphadiana.dashboard.backend.models import CreateJobRequest, JobStatus

logger = logging.getLogger(__name__)

# Context variable to tag the current job ID.  Inherited by
# ThreadPoolExecutor workers (Python 3.12+), so logs from worker
# threads are correctly routed to their owning job's buffer.
_current_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_job_id", default=None
)


class _DualWriter(io.StringIO):
    """StringIO that simultaneously appends every write to a log file on disk.

    This ensures logs are persisted in real-time so they survive server crashes.
    The in-memory buffer is kept for fast API access (polling during a run).
    """

    def __init__(self, log_path: str) -> None:
        super().__init__()
        self._log_path = log_path
        self._file_lock = threading.Lock()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def write(self, s: str) -> int:
        result = super().write(s)
        if s:
            try:
                with self._file_lock:
                    with open(self._log_path, "a", encoding="utf-8") as f:
                        f.write(s)
            except Exception:
                pass
        return result


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
        self._cancel_events: dict[str, threading.Event] = {}
        # Baseline non-errored JSONL count at job start; used to compute
        # delta progress so retries don't inherit old error records as progress.
        self._job_progress_offsets: dict[str, int] = {}
        # Optional log prefix (old run logs carried over from a resumed job).
        self._job_log_prefixes: dict[str, str] = {}
        self._lock = threading.RLock()  # RLock: reentrant, same thread can acquire multiple times
        self._jobs_file = os.path.join(results_dir, ".jobs.json")
        self._logs_dir = os.path.join(results_dir, ".job_logs")
        self._load_persisted_jobs()

    # --- Persistence ---

    # Status priority for dedup: higher = preferred when same run_id
    _STATUS_PRIORITY = {
        "running": 6,
        "pending": 5,
        "completed": 4,
        "interrupted": 3,
        "failed": 2,
        "cancelled": 1,
    }

    def _load_persisted_jobs(self) -> None:
        """Restore job history from disk on startup.

        Jobs that were 'running' or 'pending' when the server last stopped
        are marked as 'interrupted' so the user knows they can resume.
        Progress is recovered from the JSONL file on disk.
        Deduplicates by run_id, keeping only the highest-priority / newest entry.
        """
        if not os.path.exists(self._jobs_file):
            return
        try:
            with open(self._jobs_file, encoding="utf-8") as f:
                records = json.load(f)
            all_jobs: list[JobStatus] = []
            for rec in records:
                job = JobStatus(**rec)
                if job.status in ("running", "pending"):
                    job.status = "interrupted"
                    job.error = (
                        "Server restarted while this job was running. "
                        "Results up to this point are saved. "
                        "Re-submit the same Run ID to resume."
                    )
                # Always recover progress from JSONL (may have stale values)
                if job.status in ("interrupted", "completed", "failed"):
                    self._recover_progress(job)
                all_jobs.append(job)

            # Dedup by run_id: keep highest priority, then newest
            deduped = self._dedup_by_run_id(all_jobs)
            for job in deduped:
                self._jobs[job.job_id] = job
                self._load_job_log(job.job_id)
            logger.info(
                "Restored %d jobs (%d after dedup) from %s",
                len(records), len(deduped), self._jobs_file,
            )
        except Exception:
            logger.warning("Failed to load persisted jobs", exc_info=True)

    @staticmethod
    def _parse_total_from_log(log_text: str) -> int:
        """Parse the true total task count from runner log text.

        The runner logs:
          "Total work items: 15 (tasks=15, num_samples=2)"
        The first number is *remaining* after checkpoint, but tasks * num_samples
        is the true total. We use tasks * num_samples when available.
        Falls back to the first number for old-format logs, or "Loaded N tasks".
        """
        # Best: parse tasks=T, num_samples=K from the parenthetical
        m = re.search(r"Total work items: \d+ \(tasks=(\d+), num_samples=(\d+)\)", log_text)
        if m:
            return int(m.group(1)) * int(m.group(2))
        # Fallback: old format without parenthetical
        m = re.search(r"Total work items: (\d+)", log_text)
        if m:
            return int(m.group(1))
        # Last resort: "Loaded N tasks from benchmark" (single-sample, no checkpoint info)
        m = re.search(r"Loaded (\d+) tasks from benchmark", log_text)
        if m:
            return int(m.group(1))
        return 0

    @staticmethod
    def _count_jsonl(jsonl_path: str) -> tuple[int, int, int]:
        """Count unique results, correct count, and error count from a JSONL file.

        Deduplicates by (task_id, sample_index) keeping the latest entry,
        so retried tasks replace their error records correctly.
        Returns (unique_count, correct_count, error_count).
        """
        seen: dict[tuple[str, int], dict] = {}
        try:
            with open(jsonl_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        key = (record.get("task_id", ""), record.get("sample_index", 0))
                        seen[key] = record
                    except json.JSONDecodeError:
                        pass
        except Exception:
            return 0, 0, 0
        count = len(seen)
        correct = sum(1 for r in seen.values() if r.get("correct"))
        error_count = sum(1 for r in seen.values() if r.get("score") is None)
        return count, correct, error_count

    def _recover_progress(self, job: JobStatus) -> None:
        """Read the JSONL file to recover progress/accuracy for an interrupted job.

        Also parses persisted log files for total_tasks.
        """
        from pathlib import Path

        # Try to recover total_tasks from persisted log file
        log_path = os.path.join(self._logs_dir, f"{job.job_id}.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, encoding="utf-8") as f:
                    log_text = f.read()
                parsed = self._parse_total_from_log(log_text)
                if parsed > 0:
                    job.total_tasks = parsed
            except Exception:
                pass

        jsonl_path = Path(self._results_dir) / f"{job.run_id}.jsonl"
        if not jsonl_path.exists():
            return
        count, correct, error_count = self._count_jsonl(str(jsonl_path))
        if count > 0:
            job.progress = count
            job.accuracy = correct / count
            # If all tasks are now done with no errors, auto-complete the job.
            # This handles retries done outside the dashboard (e.g. CLI) or
            # jobs that were interrupted after the run actually finished.
            if (
                job.status == "interrupted"
                and job.total_tasks > 0
                and count >= job.total_tasks
                and error_count == 0
            ):
                job.status = "completed"

    def _save_job_log(self, job_id: str) -> None:
        """Persist in-memory log buffer to disk so it survives restarts."""
        try:
            os.makedirs(self._logs_dir, exist_ok=True)
            buf = self._job_logs.get(job_id)
            if buf:
                log_path = os.path.join(self._logs_dir, f"{job_id}.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(buf.getvalue())
        except Exception:
            logger.debug("Failed to save log for %s", job_id, exc_info=True)

    def _load_job_log(self, job_id: str) -> None:
        """Restore log from disk into in-memory buffer."""
        log_path = os.path.join(self._logs_dir, f"{job_id}.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, encoding="utf-8") as f:
                    content = f.read()
                self._job_logs[job_id] = io.StringIO(content)
            except Exception:
                logger.debug("Failed to load log for %s", job_id, exc_info=True)

    def _persist_jobs(self) -> None:
        """Save all job statuses to disk. Must be called with self._lock held."""
        try:
            os.makedirs(os.path.dirname(self._jobs_file) or ".", exist_ok=True)
            records = [job.model_dump() for job in self._jobs.values()]
            tmp = self._jobs_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False)
            os.replace(tmp, self._jobs_file)
        except Exception:
            logger.warning("Failed to persist jobs", exc_info=True)

    def remove_job(self, job_id: str) -> None:
        """Remove a job from the list (used when resuming replaces the old entry)."""
        with self._lock:
            self._jobs.pop(job_id, None)
            self._job_logs.pop(job_id, None)
            self._job_progress_offsets.pop(job_id, None)
            self._job_log_prefixes.pop(job_id, None)
            self._persist_jobs()
        # Clean up persisted log file
        log_path = os.path.join(self._logs_dir, f"{job_id}.log")
        try:
            if os.path.exists(log_path):
                os.remove(log_path)
        except OSError:
            pass

    def delete_run_data(self, run_id: str) -> dict[str, Any]:
        """Delete all data for a run: JSONL, YAML config, associated jobs, and logs.

        Refuses to delete if any associated job is currently running.
        Returns a summary of what was deleted.
        """
        from pathlib import Path

        deleted: dict[str, Any] = {"run_id": run_id, "jobs_removed": 0, "files_removed": []}

        # Check for running jobs with this run_id
        with self._lock:
            for job in self._jobs.values():
                if job.run_id == run_id and job.status in ("running", "pending"):
                    raise ValueError(f"Cannot delete: job {job.job_id} is {job.status}")

        # Remove all job entries with this run_id
        with self._lock:
            to_remove = [jid for jid, j in self._jobs.items() if j.run_id == run_id]
            for jid in to_remove:
                self._jobs.pop(jid, None)
                self._job_logs.pop(jid, None)
                # Remove persisted log
                log_path = os.path.join(self._logs_dir, f"{jid}.log")
                try:
                    if os.path.exists(log_path):
                        os.remove(log_path)
                except OSError:
                    pass
            deleted["jobs_removed"] = len(to_remove)
            if to_remove:
                self._persist_jobs()

        # Remove JSONL results file
        jsonl_path = Path(self._results_dir) / f"{run_id}.jsonl"
        if jsonl_path.exists():
            try:
                jsonl_path.unlink()
                deleted["files_removed"].append(str(jsonl_path))
            except OSError:
                pass

        # Remove YAML config file
        if self._configs_dir:
            yaml_path = Path(self._configs_dir) / f"{run_id}.yaml"
            if yaml_path.exists():
                try:
                    yaml_path.unlink()
                    deleted["files_removed"].append(str(yaml_path))
                except OSError:
                    pass

        return deleted

    def remove_jobs_by_status(self, status_list: list[str]) -> int:
        """Remove all job entries matching the given statuses. Returns count removed."""
        with self._lock:
            to_remove = [
                jid for jid, j in self._jobs.items()
                if j.status in status_list
            ]
            for jid in to_remove:
                self._jobs.pop(jid, None)
                self._job_logs.pop(jid, None)
                log_path = os.path.join(self._logs_dir, f"{jid}.log")
                try:
                    if os.path.exists(log_path):
                        os.remove(log_path)
                except OSError:
                    pass
            if to_remove:
                self._persist_jobs()
            return len(to_remove)

    def _update_job(self, job_id: str, **fields: object) -> None:
        """Update job fields and persist to disk. Thread-safe."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            self._persist_jobs()

    @classmethod
    def _dedup_by_run_id(cls, jobs: list[JobStatus]) -> list[JobStatus]:
        """Deduplicate jobs by run_id, keeping the highest-priority / newest."""
        best: dict[str, JobStatus] = {}
        for job in jobs:
            existing = best.get(job.run_id)
            if existing is None:
                best[job.run_id] = job
                continue
            ep = cls._STATUS_PRIORITY.get(existing.status, 0)
            jp = cls._STATUS_PRIORITY.get(job.status, 0)
            if jp > ep or (jp == ep and job.created_at > existing.created_at):
                best[job.run_id] = job
        return list(best.values())

    def list_jobs(self) -> list[JobStatus]:
        with self._lock:
            all_jobs = list(self._jobs.values())
        deduped = self._dedup_by_run_id(all_jobs)
        # Sort newest first
        deduped.sort(key=lambda j: j.created_at, reverse=True)
        return deduped

    def get_job(self, job_id: str) -> JobStatus | None:
        with self._lock:
            return self._jobs.get(job_id)

    def get_raw_log_content(self, job_id: str) -> str:
        """Return actual log content for a job (empty string if no logs exist).

        Unlike get_job_logs, never returns hint messages — only real log text.
        Used to carry logs forward when resuming a job.
        """
        with self._lock:
            buf = self._job_logs.get(job_id)
            if buf is not None:
                content = buf.getvalue()
                if content:
                    return content
        log_path = os.path.join(self._logs_dir, f"{job_id}.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
        return ""

    def set_log_prefix(self, job_id: str, prefix: str) -> None:
        """Prepend content to a job's log output (e.g. carried-over logs from previous run)."""
        with self._lock:
            self._job_log_prefixes[job_id] = prefix

    def get_job_logs(self, job_id: str) -> str | None:
        with self._lock:
            prefix = self._job_log_prefixes.get(job_id, "")
            buf = self._job_logs.get(job_id)
            if buf is not None:
                content = buf.getvalue()
                if content:
                    return prefix + content
            job = self._jobs.get(job_id)

        # Try loading from disk if not in memory
        log_path = os.path.join(self._logs_dir, f"{job_id}.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, encoding="utf-8") as f:
                    content = f.read()
                if content:
                    return prefix + content
            except Exception:
                pass

        if prefix:
            return prefix

        # Job exists but no logs available — return friendly hint
        if job is not None:
            hints = {
                "pending": "Job is pending. Logs will appear once execution starts.",
                "running": "Job is running but no log output yet.",
                "completed": "Logs are no longer available for this completed job.",
                "interrupted": "Logs are no longer available. The job was interrupted by a server restart.",
                "failed": "Logs are no longer available for this failed job.",
                "cancelled": "Job was cancelled. No logs were captured.",
            }
            return hints.get(job.status, "No logs available.")
        return None

    @staticmethod
    def _run_identity_parts(req: CreateJobRequest) -> dict[str, Any]:
        """Build the deterministic identity payload for a run.

        OpenClaw runs need sandbox/deploy context in addition to agent params.
        Otherwise a fresh deploy-and-run job can collide with an existing-sandbox
        run and be treated as the same checkpointed evaluation.
        """
        # Strip credential fields from agent_config before hashing
        credential_fields = {"api_key", "gateway_token", "secret", "secret_key", "token"}
        filtered_agent_config = {
            k: v for k, v in req.agent_config.items()
            if k.lower() not in credential_fields
        }
        key_parts: dict[str, Any] = {
            "agent": req.agent_name,
            "benchmark": req.benchmark_name,
            "agent_config": filtered_agent_config,
            "benchmark_config": req.benchmark_config,
        }

        metadata = req.metadata if isinstance(req.metadata, dict) else {}
        if req.agent_name == "openclaw":
            sandbox_id = metadata.get("sandbox_id")
            if sandbox_id:
                key_parts["sandbox_id"] = sandbox_id

            if metadata.get("deploy_and_run"):
                key_parts["deploy_and_run"] = True

            base_model = metadata.get("base_model")
            if base_model:
                key_parts["base_model"] = base_model

            model_api_base = metadata.get("model_api_base")
            if model_api_base:
                key_parts["model_api_base"] = model_api_base

        return key_parts

    @staticmethod
    def _deterministic_run_id(req: CreateJobRequest) -> str:
        """Generate a deterministic run_id from config content.

        Same config → same run_id → automatic checkpoint resume.
        Excludes credentials (api_key, tokens) — they don't affect results.
        """
        key_parts = JobManager._run_identity_parts(req)
        # Use compact separators to match JavaScript's JSON.stringify output
        canonical = json.dumps(key_parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        short_hash = hashlib.sha256(canonical.encode()).hexdigest()[:12]
        return f"{req.agent_name}-{req.benchmark_name}-{short_hash}"

    def create_job(self, req: CreateJobRequest) -> JobStatus:
        """Create and start a new evaluation job in a background thread."""
        job_id = uuid.uuid4().hex[:12]
        run_id = req.run_id or self._deterministic_run_id(req)

        benchmark_label = req.metadata.get("benchmark_label", req.benchmark_name)
        # Extract model name from agent config
        model = req.agent_config.get("model", "")
        # Mask API keys in persisted request to avoid storing secrets
        persisted_req = self._mask_request(req)

        status = JobStatus(
            job_id=job_id,
            run_id=run_id,
            status="pending",
            agent=req.agent_name,
            benchmark=benchmark_label,
            model=model,
            created_at=datetime.now(timezone.utc).isoformat(),
            original_request=persisted_req,
        )
        with self._lock:
            self._jobs[job_id] = status
            log_path = os.path.join(self._logs_dir, f"{job_id}.log")
            self._job_logs[job_id] = _DualWriter(log_path)
            self._persist_jobs()

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, run_id, req),
            daemon=True,
        )
        thread.start()
        return status

    @staticmethod
    def _mask_request(req: CreateJobRequest) -> dict[str, Any]:
        """Return a serializable copy of CreateJobRequest with API keys masked.

        Preserves $VAR_NAME references (they are not secrets and are needed
        for resume). Only masks resolved key values.
        """
        data = req.model_dump()
        agent_cfg = data.get("agent_config", {})
        if isinstance(agent_cfg, dict):
            for key in list(agent_cfg.keys()):
                if "key" in key.lower() and isinstance(agent_cfg.get(key), str) and agent_cfg[key]:
                    v = agent_cfg[key]
                    # Keep $VAR_NAME env references as-is for resume
                    if v.startswith("$"):
                        continue
                    agent_cfg[key] = f"{v[:6]}...{v[-4:]}" if len(v) > 12 else "***"
        return data

    def create_job_after_deploy(
        self,
        req: "CreateJobRequest",
        sandbox_manager: "Any",
        deploy_id: str,
    ) -> JobStatus:
        """Create a job that waits for sandbox deploy to complete first."""
        import time as _time

        job_id = uuid.uuid4().hex[:12]
        run_id = req.run_id or self._deterministic_run_id(req)
        benchmark_label = req.metadata.get("benchmark_label", req.benchmark_name)
        model = req.agent_config.get("model", "") or req.metadata.get("base_model", "")
        persisted_req = self._mask_request(req)

        status = JobStatus(
            job_id=job_id,
            run_id=run_id,
            status="pending",
            agent=req.agent_name,
            benchmark=benchmark_label,
            model=model,
            created_at=datetime.now(timezone.utc).isoformat(),
            original_request=persisted_req,
        )
        with self._lock:
            self._jobs[job_id] = status
            log_path = os.path.join(self._logs_dir, f"{job_id}.log")
            self._job_logs[job_id] = _DualWriter(log_path)
            self._persist_jobs()

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
                        self._update_job(job_id, status="failed", error=f"Deploy job {deploy_id} not found")
                        return

                    if deploy_job.status == "completed":
                        if deploy_job.gateway_pool:
                            _log(f"Sandboxes deployed! Gateway pool: {len(deploy_job.gateway_pool)} sandboxes")
                            req.agent_config["gateway_pool"] = deploy_job.gateway_pool
                        else:
                            _log(f"Sandbox deployed! API base: {deploy_job.api_base}")
                            req.agent_config["api_base"] = deploy_job.api_base
                        req.agent_config.setdefault("model", "openclaw")
                        break
                    elif deploy_job.status == "failed":
                        _log(f"[ERROR] Sandbox deploy failed: {deploy_job.error}")
                        self._update_job(job_id, status="failed", error=f"Sandbox deploy failed: {deploy_job.error}")
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
                    self._update_job(job_id, status="failed", error="Sandbox deploy timed out")
                    return

                # Now run the actual evaluation
                _log("Starting evaluation...")
                self._run_job(job_id, run_id, req, _skip_log_setup=True)

            except Exception as exc:
                logger.exception("Deploy-and-run job %s failed", job_id)
                self._update_job(job_id, status="failed", error=str(exc))
            finally:
                root_logger.removeHandler(handler)
                self._save_job_log(job_id)

        thread = threading.Thread(target=_wait_and_run, daemon=True)
        thread.start()
        return status

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running or pending job.

        Sets the cancel event so the TaskDispatcher stops dispatching new tasks.
        Already-running tasks will finish but no new ones will start.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.status in ("pending", "running"):
                job.status = "cancelled"
                self._persist_jobs()
                # Signal the cancel event so the dispatcher stops
                cancel_ev = self._cancel_events.get(job_id)
                if cancel_ev:
                    cancel_ev.set()
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

        # Create a cancel event for this job
        cancel_event = threading.Event()
        self._cancel_events[job_id] = cancel_event

        self._update_job(job_id, status="running")

        # Record how many non-errored records are already in the JSONL.
        # These tasks will be skipped (checkpoint) and must not inflate progress.
        from pathlib import Path as _Path
        _baseline_path = _Path(self._results_dir) / f"{run_id}.jsonl"
        if _baseline_path.exists():
            _bc, _, _be = self._count_jsonl(str(_baseline_path))
            self._job_progress_offsets[job_id] = _bc - _be  # non-errored = will be skipped
        else:
            self._job_progress_offsets[job_id] = 0

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

            # redo_all: clear existing results so we truly start from scratch
            if req.redo_all:
                from pathlib import Path
                jsonl_path = Path(self._results_dir) / f"{run_id}.jsonl"
                if jsonl_path.exists():
                    _log(f"redo_all: clearing existing results ({jsonl_path.name})")
                    jsonl_path.unlink()

            _log("Setting up runner...")
            runner = Runner(config, cancel_event=cancel_event)
            runner.setup()
            _log("Runner ready. Tasks will be loaded when evaluation starts.")

            with self._lock:
                job = self._jobs[job_id]
                if job.status == "cancelled":
                    _log("Job cancelled before execution")
                    return

            # Pre-evaluation gateway health check for openclaw agents.
            # Use api_base if set (single sandbox), otherwise fall back to the
            # first URL in gateway_pool (multi-sandbox deploy-and-run).
            _api_base = req.agent_config.get("api_base", "") or ""
            _gateway_pool = req.agent_config.get("gateway_pool") or []
            probe_url = _api_base or (_gateway_pool[0] if _gateway_pool else "")
            if req.agent_name == "openclaw" and probe_url:
                _log(f"Probing OpenClaw gateway at {probe_url} ...")
                if not self._probe_gateway(probe_url):
                    _log("[ERROR] OpenClaw gateway is not responding. Aborting job.")
                    self._update_job(
                        job_id, status="failed",
                        error=f"OpenClaw gateway not ready at {probe_url}. Please check sandbox status before starting evaluation.",
                    )
                    return
                _log("Gateway health check passed.")

            try:
                _log("Starting evaluation run...")
                summary = runner.run()
                with self._lock:
                    job = self._jobs.get(job_id)
                    if not job or job.status == "cancelled":
                        _log(f"Job cancelled. {summary.completed}/{summary.total_tasks} tasks completed before cancellation.")
                        self._persist_jobs()
                        return
                    _log(f"Evaluation complete: {summary.completed}/{summary.total_tasks} tasks, accuracy={summary.accuracy:.2%}")
                    job.progress = summary.completed
                    job.total_tasks = summary.total_tasks
                    job.accuracy = summary.accuracy
                    if summary.completed == 0 and summary.total_tasks > 0:
                        job.status = "failed"
                        job.error = f"All {summary.total_tasks} tasks failed. Check logs for details."
                    elif summary.failed > 0:
                        job.status = "completed"
                        job.error = f"{summary.failed}/{summary.total_tasks} tasks failed"
                    else:
                        job.status = "completed"
                    self._persist_jobs()
            finally:
                runner.teardown()

        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            _log(f"[ERROR] Job failed: {exc}")
            self._update_job(job_id, status="failed", error=str(exc))
        finally:
            self._cancel_events.pop(job_id, None)
            if handler is not None:
                root_logger.removeHandler(handler)
            # Persist log to disk so it survives restarts
            self._save_job_log(job_id)

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

        # Parse true total from job logs. The runner logs:
        #   "Total work items: 15 (tasks=15, num_samples=2)"
        # We use tasks * num_samples (= true total), not the first number
        # (which is remaining after checkpoint and can be misleading).
        # Always parse — even if current_total is set from resume, the log
        # value is authoritative (config may have changed, e.g. k=1 → k=2).
        log_buf = self._job_logs.get(job_id)
        if log_buf:
            parsed = self._parse_total_from_log(log_buf.getvalue())
            if parsed > 0:
                current_total = parsed

        jsonl_path = Path(self._results_dir) / f"{run_id}.jsonl"
        if not jsonl_path.exists():
            # Even if no JSONL yet, update total_tasks if we found it in logs
            if current_total > 0:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job:
                        job.total_tasks = current_total
            return

        count, correct, error_count = self._count_jsonl(str(jsonl_path))

        # Non-errored records are tasks that have been scored (correct or wrong).
        # Subtract the baseline recorded at job start so retried jobs show delta
        # progress (0 → N) instead of inheriting the old error count as progress.
        non_error_count = count - error_count
        offset = self._job_progress_offsets.get(job_id, 0)
        displayed_progress = max(0, non_error_count - offset)
        # Adjust total_tasks to reflect only the work for this run
        adjusted_total = max(0, current_total - offset) if current_total > 0 else 0

        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                if adjusted_total > 0 and job.total_tasks != adjusted_total:
                    job.total_tasks = adjusted_total
                changed = job.progress != displayed_progress
                job.progress = displayed_progress
                if non_error_count > 0:
                    job.accuracy = correct / non_error_count
                if changed:
                    self._persist_jobs()
