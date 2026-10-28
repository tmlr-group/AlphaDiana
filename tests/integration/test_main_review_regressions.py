"""Issue #108 regressions using localhost HTTP and real persistence boundaries."""

import io
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from alphadiana.dashboard.backend.data_loader import DataLoader
from alphadiana.dashboard.backend.job_manager import (
    _current_job_id,
    _JobLogCapture,
)
from alphadiana.results.store import ResultStore
from alphadiana.analysis.export import ReportGenerator
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.task_dispatcher import TaskDispatcher
from alphadiana.harness.direct_llm.agent import DirectLLMAgent
from alphadiana.scorer.numeric import NumericScorer
from tests.api.test_api_pipeline_demo import ScriptedProvider


@pytest.fixture
def provider():
    class Provider(ScriptedProvider):
        def do_POST(self):
            if self.server.mode == "normal":
                return super().do_POST()
            self.rfile.read(int(self.headers["Content-Length"]))
            try:
                if self.server.mode == "headers":
                    time.sleep(0.8)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunk = {
                    "id": "test",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "fixture",
                    "choices": [
                        {"index": 0, "delta": {"content": "2"}, "finish_reason": None}
                    ],
                }
                if self.server.mode == "partial":
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    self.wfile.flush()
                if self.server.mode in {"chunk", "partial"}:
                    time.sleep(0.8)
                if self.server.mode == "dribble":
                    for _ in range(30):
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        time.sleep(0.05)
                chunk["choices"][0]["finish_reason"] = "stop"
                self.wfile.write(
                    ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
                )
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.mode = "normal"
    server.answers = {"1+1": "2"}
    server.calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def agent_config(provider, **extra):
    return dict(
        model="local-demo-fixture",
        api_base=f"http://127.0.0.1:{provider.server_port}/v1",
        api_key="fixture",
        max_tokens=8,
        max_retries=0,
        request_timeout=2,
        **extra,
    )


@pytest.mark.parametrize("mode", [None, "float"])
def test_default_and_float_sidecars_preserve_candidates(provider, tmp_path, mode):
    agent = DirectLLMAgent()
    config = agent_config(provider, stream=False)
    if mode is not None:
        config["logprobs_format"] = mode
    agent.setup(config)
    try:
        task = BenchmarkTask("a", "1+1", "2")
        response = agent.solve(task)
        store = ResultStore(str(tmp_path), "probabilities")
        store.append(task, response, NumericScorer().score(task, response))
        saved = store.load()[0]
        raw = json.loads(
            (tmp_path / saved["logprobs_path"]).read_text().splitlines()[0]
        )
        compact = json.loads(
            (tmp_path / saved["logprobs_int16_path"]).read_text().splitlines()[0]
        )
        assert [x["logprob"] for x in raw["top_logprobs"]] == [-0.1, -2.5]
        assert len(compact["top20"]) == 2
        assert compact["entropy_nats"] == pytest.approx(0.28645062373936336)
    finally:
        agent._client.close()


@pytest.mark.parametrize("mode", ["headers", "chunk", "partial", "dribble", "success"])
def test_stream_deadline_covers_entire_request(provider, mode):
    provider.mode = mode
    agent = DirectLLMAgent()
    agent.setup(agent_config(provider, stream=True, stream_total_timeout=0.25))
    try:
        start = time.monotonic()
        response = agent.solve(BenchmarkTask("a", "1+1", "2"))
        elapsed = time.monotonic() - start
        assert elapsed < 0.65, elapsed  # stalled fixture takes at least 0.8 s
        if mode == "success":
            assert response.answer == "2" and response.finish_reason == "stop"
        else:
            assert response.answer is None and response.finish_reason == "timeout"
            assert response.metadata["directllm_timeout_scored_zero"] is True
            score = NumericScorer().score(BenchmarkTask("a", "1+1", "2"), response)
            assert score.score == 0 and score.correct is False
            if mode == "partial":
                assert response.raw_output == "2"
    finally:
        agent._client.close()


@pytest.mark.parametrize(
    "kind",
    ["complete", "missing", "invalid", "retry_success", "retry_failure", "legacy"],
)
def test_dashboard_and_report_share_denominators(tmp_path, kind):
    store = ResultStore(str(tmp_path), "run")
    store.save_manifest(
        dict(
            num_samples=4,
            expected_task_count=2,
            expected_sample_count=8,
            expected_task_ids=["a", "b"],
        )
    )
    rows = [
        dict(
            task_id=task,
            sample_index=i,
            num_samples=4,
            score=1,
            correct=True,
            predicted="2",
            score_status="valid_scored",
        )
        for task in ["a", "b"]
        for i in range(4)
    ]
    if kind == "missing":
        rows = rows[:1]
    if kind in {"invalid", "retry_failure"}:
        invalid = dict(
            rows[0], score_status="scorer_error", error={"error_type": "ScorerError"}
        )
        if kind == "invalid":
            rows[0] = invalid
        else:
            rows.append(invalid)
    if kind == "retry_success":
        rows.insert(
            0, dict(rows[0], score=None, correct=None, score_status="runtime_error")
        )
    if kind == "legacy":
        store.manifest_path.unlink()
        for r in rows:
            r.pop("score_status")
    Path(store.path).write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf8"
    )
    core = ReportGenerator().generate(store)
    dashboard = DataLoader(str(tmp_path)).get_run("run").summary
    for field in [
        "pass_at_k",
        "avg_at_k",
        "accuracy",
        "accuracy_total",
        "missing_samples",
        "missing_tasks",
        "valid_scored",
        "expected_sample_count",
        "per_category_pass_at_k",
    ]:
        assert getattr(dashboard, field) == getattr(core, field), field
    if kind == "missing":
        assert dashboard.pass_at_k == 0.5 and dashboard.avg_at_k == 0.125
        assert dashboard.missing_samples == 7 and dashboard.total_tasks == 2
    assert len(Path(store.path).read_text().splitlines()) == len(rows)


def test_concurrent_jobs_capture_only_their_own_worker_logs():
    log = logging.getLogger("review-workers")
    log.setLevel(logging.INFO)
    buffers = {job: io.StringIO() for job in ["job-a", "job-b"]}
    handlers = [_JobLogCapture(buffer, job) for job, buffer in buffers.items()]
    for handler in handlers:
        log.addHandler(handler)
    barrier = threading.Barrier(2)

    def run_job(job):
        token = _current_job_id.set(job)
        try:
            barrier.wait(timeout=5)

            def solve(task):
                log.info("%s task=%s", job, task.task_id)
                return {}

            TaskDispatcher(max_concurrent=2).dispatch(
                [BenchmarkTask(str(i), "p", "a") for i in range(4)], solve
            )
        finally:
            _current_job_id.reset(token)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(run_job, buffers))
        for job, buffer in buffers.items():
            assert buffer.getvalue().count(job) == 4
            assert ("job-b" if job == "job-a" else "job-a") not in buffer.getvalue()
    finally:
        for handler in handlers:
            log.removeHandler(handler)
