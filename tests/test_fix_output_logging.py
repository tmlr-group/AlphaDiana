"""OpenClaw 输出与日志（问题 3.1 ~ 3.3）

覆盖:
- 3.1 System prompt 提取与持久化：AgentResponse.system_prompt、trajectory 提取、JSONL/artifact
- 3.2 超时配置与错误分类：proxy_timeout、request_timeout、classify_error
- 3.3 空响应诊断：错误分类、retry_responses artifact、错误分布统计
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult


# ===================================================================
# 3.1 System prompt 提取与持久化
# ===================================================================


class TestSystemPromptField:
    """AgentResponse 包含 system_prompt 字段。"""

    def test_field_exists_default_empty(self):
        resp = AgentResponse(answer="42")
        assert resp.system_prompt == ""

    def test_in_dataclass_fields(self):
        assert "system_prompt" in AgentResponse.__dataclass_fields__

    def test_can_be_set(self):
        resp = AgentResponse(answer="42", system_prompt="You are a solver.")
        assert resp.system_prompt == "You are a solver."


class TestExtractSystemPrompt:
    """从 trajectory 中提取 system prompt。"""

    def test_from_system_role(self):
        from alphadiana.agent.openclaw import extract_system_prompt

        traj = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hi"},
        ]
        assert extract_system_prompt(traj) == "You are a helpful assistant."

    def test_from_first_assistant_with_you_are(self):
        from alphadiana.agent.openclaw import extract_system_prompt

        traj = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "You are an AI agent with 25 tools..."},
        ]
        assert "You are an AI agent" in extract_system_prompt(traj)

    def test_empty_when_no_system(self):
        from alphadiana.agent.openclaw import extract_system_prompt

        traj = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        assert extract_system_prompt(traj) == ""


class TestSystemPromptPersistence:
    """system_prompt 写入 JSONL 和 artifact。"""

    def test_saved_in_jsonl(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="sp-test")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")
        resp = AgentResponse(answer="42", system_prompt="You are a math expert.")
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")

        store.append(task, resp, score)
        results = store.load()

        assert results[0]["system_prompt"] == "You are a math expert."

    def test_artifact_file_created(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="sp-art")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")
        resp = AgentResponse(answer="42", system_prompt="You are a math expert with tools.")
        score = ScoreResult(correct=True, score=1.0, expected="42", predicted="42")

        store.append(task, resp, score)

        path = tmp_path / "sp-art" / "artifacts" / "t1" / "openclaw" / "system_prompt.txt"
        assert path.exists()
        assert "math expert" in path.read_text()


# ===================================================================
# 3.2 超时配置与错误分类
# ===================================================================


class TestProxyTimeout:
    """sandbox proxy_timeout 配置。"""

    def test_default_600(self):
        from alphadiana.sandbox.rock import ROCKSandbox

        sb = ROCKSandbox()
        sb.setup({
            "admin_base_url": "http://localhost:9000",
            "proxy_base_url": "http://localhost:9001",
        })
        assert sb._proxy_timeout == 600

    def test_custom_value(self):
        from alphadiana.sandbox.rock import ROCKSandbox

        sb = ROCKSandbox()
        sb.setup({
            "admin_base_url": "http://localhost:9000",
            "proxy_base_url": "http://localhost:9001",
            "proxy_timeout": 300,
        })
        assert sb._proxy_timeout == 300


class TestRequestTimeout:
    """agent request_timeout 配置。"""

    def test_default_600(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1"})
        assert agent._request_timeout == 600

    def test_custom_value(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1", "request_timeout": 300})
        assert agent._request_timeout == 300


class TestTimeoutErrorClassification:
    """超时错误正确分类。"""

    def test_httpx_timeout(self):
        from alphadiana.agent.openclaw import classify_error
        import httpx

        assert classify_error(
            httpx.TimeoutException("timed out"), response_json=None
        ) == "timeout"

    def test_proxy_timeout(self):
        from alphadiana.agent.openclaw import classify_error

        resp = {"status": "Failed", "message": "post proxy failed"}
        assert classify_error(None, response_json=resp) == "proxy_timeout"

    def test_error_type_in_jsonl(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="to-test")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")

        store.append_error(
            task,
            error={"error": "timed out", "error_type": "timeout"},
            response=AgentResponse(answer=None),
        )
        results = store.load()
        assert results[0]["error"]["error_type"] == "timeout"


# ===================================================================
# 3.3 空响应诊断
# ===================================================================


class TestEmptyResponseClassification:
    """空响应错误分类。"""

    def test_classify_gateway_error(self):
        from alphadiana.agent.openclaw import classify_error

        assert classify_error(
            RuntimeError("500"), response_json=None, status_code=500
        ) == "gateway_error"

    def test_classify_empty_response(self):
        from alphadiana.agent.openclaw import classify_error

        resp = {"choices": [{"message": {"content": ""}}]}
        assert classify_error(None, response_json=resp) == "empty_response"


class TestRetryResponseArtifact:
    """retry_responses 保存到 artifact。"""

    def test_saved_to_artifact(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="retry")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")

        retries = [
            {"attempt": 1, "status_code": 200, "body": {"choices": [{"message": {"content": ""}}]}},
            {"attempt": 2, "status_code": 200, "body": {"status": "Failed", "message": "post proxy failed"}},
        ]
        resp = AgentResponse(answer=None, metadata={"retry_responses": retries})
        store.append_error(
            task,
            error={"error": "All retries failed", "error_type": "empty_response"},
            response=resp,
        )

        path = tmp_path / "retry" / "artifacts" / "t1" / "openclaw" / "retry_responses.json"
        assert path.exists()
        saved = json.loads(path.read_text())
        assert len(saved) == 2
        assert saved[0]["attempt"] == 1

    def test_json_structure(self, tmp_path):
        store = ResultStore(output_dir=str(tmp_path), run_id="struct")
        task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")

        retries = [{"attempt": 1, "status_code": 502, "body": "Bad Gateway"}]
        resp = AgentResponse(answer=None, metadata={"retry_responses": retries})
        store.append_error(
            task,
            error={"error": "gateway", "error_type": "gateway_error"},
            response=resp,
        )

        path = tmp_path / "struct" / "artifacts" / "t1" / "openclaw" / "retry_responses.json"
        saved = json.loads(path.read_text())
        assert saved[0]["status_code"] == 502


class TestErrorDistribution:
    """RunSummary 中的错误分布统计。"""

    def _cfg(self, run_id):
        return ExperimentConfig(
            run_id=run_id, agent_name="openclaw",
            agent_version="2026.3.7", benchmark_name="aime", scorer_name="numeric",
        )

    def test_summary_has_error_distribution(self, tmp_path):
        from alphadiana.results.report import ReportGenerator

        store = ResultStore(output_dir=str(tmp_path), run_id="ed")

        # 2 successes
        for i in range(2):
            task = BenchmarkTask(task_id=f"ok{i}", problem="P", ground_truth="42")
            store.append(
                task, AgentResponse(answer="42"),
                ScoreResult(correct=True, score=1.0, expected="42", predicted="42"),
            )
        # 1 timeout + 1 proxy_timeout
        for etype in ("timeout", "proxy_timeout"):
            task = BenchmarkTask(task_id=f"err-{etype}", problem="P", ground_truth="42")
            store.append_error(
                task,
                error={"error": etype, "error_type": etype},
                response=AgentResponse(answer=None),
            )

        summary = ReportGenerator().generate(store, self._cfg("ed"))
        assert summary.error_distribution["timeout"] == 1
        assert summary.error_distribution["proxy_timeout"] == 1

    def test_error_distribution_counts(self, tmp_path):
        from alphadiana.results.report import ReportGenerator

        store = ResultStore(output_dir=str(tmp_path), run_id="cnt")

        for i in range(3):
            task = BenchmarkTask(task_id=f"e{i}", problem="P", ground_truth="42")
            store.append_error(
                task,
                error={"error": "empty", "error_type": "empty_response"},
                response=AgentResponse(answer=None),
            )

        summary = ReportGenerator().generate(store, self._cfg("cnt"))
        assert summary.error_distribution["empty_response"] == 3
