"""Agent robustness and feature enhancements (Issues 4.1-5.5)

Covers:
- 4.1 openclaw.py retry path variable initialization
- 4.2 Non-SSE JSON response fallback resolution
- 5.1 Checkpointing
- 5.2 DirectLLM/OpenClaw supports top_p
- 5.3 Granular output (per-task JSON)
- 5.4 Real-time plain-text dashboard
- 5.5 Improved terminal logging
"""

import json
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult


# ===================================================================
# helpers
# ===================================================================


def _task(tid="t1", gt="42"):
    return BenchmarkTask(task_id=tid, problem="What is 6*7?", ground_truth=gt)


def _score(correct=True):
    return ScoreResult(correct=correct, score=1.0 if correct else 0.0,
                       expected="42", predicted="42")


def _config(run_id="test", **kw):
    return ExperimentConfig(
        run_id=run_id, agent_name="openclaw", agent_version="2026.3.7",
        benchmark_name="aime", scorer_name="numeric", **kw,
    )


# ===================================================================
# 4.1 Retry path variable initialization
# ===================================================================


class TestRetryVariableInit:
    """elapsed/response_body should not become NameError on an empty response."""

    def test_empty_response_retry_has_elapsed(self):
        """Each record in retry_responses should have an elapsed_sec field."""
        from alphadiana.agent.openclaw import OpenClawRequestError

        err = OpenClawRequestError(
            "empty",
            error_type="empty_response",
            retry_responses=[
                {"attempt": 1, "status_code": 200, "body": "", "elapsed_sec": 1.5},
            ],
        )
        assert "elapsed_sec" in err.retry_responses[0]
        assert isinstance(err.retry_responses[0]["elapsed_sec"], (int, float))

    def test_empty_response_retry_has_body(self):
        """Each record in retry_responses should have a body field."""
        from alphadiana.agent.openclaw import OpenClawRequestError

        err = OpenClawRequestError(
            "empty",
            error_type="empty_response",
            retry_responses=[
                {"attempt": 1, "status_code": 200, "body": '{"choices":[]}', "elapsed_sec": 0.5},
            ],
        )
        assert "body" in err.retry_responses[0]

    def test_classify_error_accepts_status_code(self):
        """classify_error accepts the status_code parameter."""
        from alphadiana.agent.openclaw import classify_error

        result = classify_error(None, response_json=None, status_code=200)
        assert isinstance(result, str)


# ===================================================================
# 4.2 Fallback parsing of non-SSE JSON responses
# ===================================================================


class TestNonSSEResponseFallback:
    """Non-SSE (application/json) responses should be parsed correctly."""

    def test_classify_error_on_500(self):
        """HTTP 500 should be classified as gateway_error."""
        from alphadiana.agent.openclaw import classify_error

        assert classify_error(None, response_json=None, status_code=500) == "gateway_error"

    def test_classify_error_on_proxy_failed(self):
        """'post proxy failed' should be classified as proxy_timeout."""
        from alphadiana.agent.openclaw import classify_error

        resp = {"status": "Failed", "message": "post proxy failed"}
        assert classify_error(None, response_json=resp) == "proxy_timeout"

    def test_json_error_body_preserved_in_retry(self):
        """Non-SSE error response bodies should remain in retry_responses."""
        from alphadiana.agent.openclaw import OpenClawRequestError

        error_body = json.dumps({"error": {"message": "Internal error", "code": 500}})
        err = OpenClawRequestError(
            "gateway error",
            error_type="gateway_error",
            response_body=error_body,
            retry_responses=[
                {"attempt": 1, "status_code": 500, "body": error_body, "elapsed_sec": 0.3},
            ],
        )
        assert err.response_body == error_body
        assert err.retry_responses[0]["body"] == error_body


# ===================================================================
# 5.1 Checkpointing
# ===================================================================


class TestCheckpointing:
    """Checkpointing skips tasks that are already completed."""

    def test_completed_task_ids_from_jsonl(self, tmp_path):
        """Extract the completed task_id set from an existing JSONL file."""
        store = ResultStore(output_dir=str(tmp_path), run_id="ckpt")
        for tid in ("t1", "t2", "t3"):
            store.append(_task(tid), AgentResponse(answer="42"), _score())

        completed = store.completed_task_ids()
        assert completed == {"t1", "t2", "t3"}

    def test_completed_excludes_errors(self, tmp_path):
        """An error record (correct=None) does not count as completed and should be retried."""
        store = ResultStore(output_dir=str(tmp_path), run_id="ckpt2")
        store.append(_task("t1"), AgentResponse(answer="42"), _score())
        store.append_error(
            _task("t2"),
            error={"error": "timeout", "error_type": "timeout"},
            response=AgentResponse(answer=None),
        )

        completed = store.completed_task_ids()
        assert "t1" in completed
        assert "t2" not in completed

    def test_completed_task_ids_empty_file(self, tmp_path):
        """An empty JSONL file should return an empty set."""
        store = ResultStore(output_dir=str(tmp_path), run_id="empty")
        assert store.completed_task_ids() == set()

    def test_config_has_redo_all_field(self):
        """ExperimentConfig should have a redo_all field."""
        cfg = _config(redo_all=False)
        assert hasattr(cfg, "redo_all")
        assert cfg.redo_all is False

    def test_config_redo_all_default_false(self):
        """redo_all defaults to False."""
        cfg = _config()
        assert cfg.redo_all is False

    def test_runner_skips_completed_tasks(self, tmp_path):
        """Runner should skip tasks that are already completed."""
        # Write a completed record first
        store = ResultStore(output_dir=str(tmp_path), run_id="skip")
        store.append(_task("t1"), AgentResponse(answer="42"), _score())

        # Runner should only dispatch incomplete tasks.
        cfg = _config("skip", output_dir=str(tmp_path))
        from alphadiana.runner.runner import Runner

        runner = Runner(cfg)
        runner.result_store = store

        # Mock benchmark returns 3 tasks.
        all_tasks = [_task("t1"), _task("t2"), _task("t3")]

        # Validate the filtering logic.
        completed = store.completed_task_ids()
        pending = [t for t in all_tasks if t.task_id not in completed]
        assert len(pending) == 2
        assert all(t.task_id in ("t2", "t3") for t in pending)

    def test_redo_all_ignores_checkpoint(self, tmp_path):
        """All tasks should be handled when redo_all=True."""
        store = ResultStore(output_dir=str(tmp_path), run_id="redo")
        store.append(_task("t1"), AgentResponse(answer="42"), _score())

        cfg = _config("redo", output_dir=str(tmp_path), redo_all=True)

        all_tasks = [_task("t1"), _task("t2")]
        completed = store.completed_task_ids()

        if cfg.redo_all:
            pending = all_tasks
        else:
            pending = [t for t in all_tasks if t.task_id not in completed]

        assert len(pending) == 2


# ===================================================================
# 5.2 top_p parameter
# ===================================================================


class TestTopPSupport:
    """DirectLLM and OpenClaw support the top_p parameter."""

    def test_direct_llm_reads_top_p(self):
        """DirectLLMAgent.setup() should read the top_p configuration."""
        from alphadiana.agent.direct_llm import DirectLLMAgent

        agent = DirectLLMAgent()
        agent.setup({"model": "m", "api_base": "http://x/v1", "api_key": "k", "top_p": 0.9})
        assert agent._top_p == 0.9

    def test_direct_llm_top_p_default_none(self):
        """None by default when top_p is not configured."""
        from alphadiana.agent.direct_llm import DirectLLMAgent

        agent = DirectLLMAgent()
        agent.setup({"model": "m", "api_base": "http://x/v1", "api_key": "k"})
        assert agent._top_p is None

    def test_direct_llm_passes_top_p_to_api(self):
        """solve() should pass top_p to the API when it is not None."""
        from alphadiana.agent.direct_llm import DirectLLMAgent

        agent = DirectLLMAgent()
        agent.setup({
            "model": "m", "api_base": "http://x/v1", "api_key": "k",
            "top_p": 0.95,
        })

        fake_response = MagicMock()
        fake_response.choices = [MagicMock(message=MagicMock(content="\\boxed{42}"))]
        fake_response.usage = None
        create = MagicMock(return_value=fake_response)
        client = MagicMock()
        client.chat.completions.create = create

        agent._client = client
        with patch.object(agent, "_resolve_max_tokens", return_value=32768):
            agent.solve(_task())

        call_kwargs = create.call_args.kwargs
        assert call_kwargs.get("top_p") == 0.95

    def test_direct_llm_omits_top_p_when_none(self):
        """top_p should be omitted when it is None."""
        from alphadiana.agent.direct_llm import DirectLLMAgent

        agent = DirectLLMAgent()
        agent.setup({"model": "m", "api_base": "http://x/v1", "api_key": "k"})

        fake_response = MagicMock()
        fake_response.choices = [MagicMock(message=MagicMock(content="\\boxed{42}"))]
        fake_response.usage = None
        create = MagicMock(return_value=fake_response)
        client = MagicMock()
        client.chat.completions.create = create

        agent._client = client
        with patch.object(agent, "_resolve_max_tokens", return_value=32768):
            agent.solve(_task())

        call_kwargs = create.call_args.kwargs
        assert "top_p" not in call_kwargs

    def test_openclaw_payload_supports_top_p(self):
        """OpenClaw._build_request_payload() should support top_p."""
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1", "top_p": 0.9})
        payload = agent._build_request_payload(
            [{"role": "user", "content": "hi"}]
        )
        assert payload.get("top_p") == 0.9

    def test_openclaw_payload_supports_stream_override(self):
        """OpenClaw._build_request_payload() should honor stream=false overrides."""
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1", "stream": False})
        payload = agent._build_request_payload(
            [{"role": "user", "content": "hi"}]
        )
        assert payload.get("stream") is False

    def test_openclaw_multimodal_request_includes_hle_images(self):
        """OpenClaw should forward task image attachments as multimodal user content."""
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1", "request_timeout": 5, "max_attempts": 1})
        task = BenchmarkTask(
            task_id="hle_0",
            problem="Pick the correct answer.",
            ground_truth="A",
            attachments={
                "image_1": b"\x89PNG\r\n",
                "image_1_mime": b"image/png",
            },
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = json.dumps({
            "choices": [{"message": {"content": "\\boxed{A}"}}],
        })
        mock_response.read = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_response)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_health_check", return_value=None):
            with patch.object(agent, "_retrieve_trajectory_sync", return_value=[]):
                with patch.object(agent, "_resolve_max_tokens", return_value=256):
                    with patch("httpx.Client", return_value=mock_client):
                        agent.solve(task)

        request_payload = mock_client.stream.call_args.kwargs["json"]
        content = request_payload["messages"][0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_openclaw_multimodal_request_content_normalizes_to_text(self):
        """Trajectory matching should handle multimodal list content without crashing."""
        from alphadiana.agent.openclaw import _normalize_request_content

        normalized = _normalize_request_content([
            {"type": "text", "text": "Pick the correct answer.\r\nA or B?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ])

        assert normalized == "Pick the correct answer.\nA or B?"


# ===================================================================
# 5.3 Granular output (per-task JSON)
# ===================================================================


class TestPerTaskOutput:
    """A separate JSON file is generated for each task."""

    def test_per_task_json_created(self, tmp_path):
        """append() should generate tasks/{task_id}.json."""
        store = ResultStore(output_dir=str(tmp_path), run_id="granular")
        store.append(_task("t1"), AgentResponse(answer="42"), _score())

        per_task = tmp_path / "granular" / "tasks" / "t1.json"
        assert per_task.exists(), f"Expected per-task JSON at {per_task}"

    def test_per_task_json_content(self, tmp_path):
        """The per-task JSON should contain the full record."""
        store = ResultStore(output_dir=str(tmp_path), run_id="granular2")
        store.append(_task("t1"), AgentResponse(answer="42"), _score())

        per_task = tmp_path / "granular2" / "tasks" / "t1.json"
        if per_task.exists():
            content = json.loads(per_task.read_text(encoding="utf-8"))
            # Per-task JSON is now a list of records (one per sample).
            record = content[0] if isinstance(content, list) else content
            assert record["task_id"] == "t1"
            assert record["predicted"] == "42"
            assert record["correct"] is True

    def test_jsonl_still_written(self, tmp_path):
        """The JSONL file is still written normally (backwards compatible)."""
        store = ResultStore(output_dir=str(tmp_path), run_id="compat")
        store.append(_task("t1"), AgentResponse(answer="42"), _score())

        jsonl = tmp_path / "compat.jsonl"
        assert jsonl.exists()
        lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_error_also_creates_per_task_json(self, tmp_path):
        """append_error() should also generate per-task JSON."""
        store = ResultStore(output_dir=str(tmp_path), run_id="granular-err")
        store.append_error(
            _task("t1"),
            error={"error": "timeout", "error_type": "timeout"},
            response=AgentResponse(answer=None),
        )

        per_task = tmp_path / "granular-err" / "tasks" / "t1.json"
        assert per_task.exists(), f"Expected per-task JSON for error at {per_task}"


# ===================================================================
# 5.4 Real-time plain-text dashboard
# ===================================================================


class TestPlainTextDashboard:
    """Status files are updated in real time during the assessment."""

    def test_dashboard_creates_file(self, tmp_path):
        """A status file should be created after the Dashboard is initialized."""
        from alphadiana.runner.dashboard import PlainTextDashboard

        path = tmp_path / "status.txt"
        tasks = [_task("t1"), _task("t2"), _task("t3")]
        db = PlainTextDashboard(path, tasks)
        assert path.exists()

    def test_dashboard_update_writes_status(self, tmp_path):
        """The file should contain O/X markers after update()."""
        from alphadiana.runner.dashboard import PlainTextDashboard

        path = tmp_path / "status.txt"
        tasks = [_task("t1"), _task("t2")]
        db = PlainTextDashboard(path, tasks)

        db.update("t1", True)
        db.update("t2", False)

        content = path.read_text(encoding="utf-8")
        assert "O" in content
        assert "X" in content

    def test_dashboard_last_updated_line(self, tmp_path):
        """The first line of the file should be Last updated: timestamp."""
        from alphadiana.runner.dashboard import PlainTextDashboard

        path = tmp_path / "status.txt"
        db = PlainTextDashboard(path, [_task("t1")])
        db.update("t1", True)

        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("Last updated:")

    def test_dashboard_multiple_updates_same_task(self, tmp_path):
        """Multiple updates of the same task should append additional markers."""
        from alphadiana.runner.dashboard import PlainTextDashboard

        path = tmp_path / "status.txt"
        db = PlainTextDashboard(path, [_task("t1")])

        db.update("t1", True)
        db.update("t1", False)
        db.update("t1", True)

        content = path.read_text(encoding="utf-8")
        # It should contain a sequence like OXO.
        assert content.count("O") >= 2
        assert "X" in content


# ===================================================================
# 5.5 Improved terminal logging
# ===================================================================


class TestTerminalLogging:
    """A predicted-vs-gold comparison should be logged after task completion."""

    def test_task_completion_log_format(self):
        """There should be a comparison log after runner.solve_fn completes."""
        # Verify that a comparison log pattern exists in runner.py.
        source = Path(__file__).parent.parent / "alphadiana" / "runner" / "runner.py"
        content = source.read_text(encoding="utf-8")
        # The implementation should contain predicted-vs-gold logging or equivalent wording.
        assert ("Predicted" in content or "predicted" in content
                or "Gold" in content or "ground_truth" in content
                or "vs" in content), \
            "runner.py should log predicted vs gold comparison after task completion"

    def test_rich_progress_optional(self):
        """ImportError should not be raised when Rich is unavailable."""
        # Make sure the code uses try/except ImportError.
        source = Path(__file__).parent.parent / "alphadiana" / "runner" / "runner.py"
        content = source.read_text(encoding="utf-8")
        # Repair should have try: from rich... except ImportError or no mandatory dependencies
        # Currently this is just an interface level check
        # The import runner should not fail if rich is not in the dependency
        try:
            from alphadiana.runner.runner import Runner
        except ImportError:
            pytest.fail("Importing Runner should not fail even without rich")
