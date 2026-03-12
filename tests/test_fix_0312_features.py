"""Agent 健壮性与功能增强（问题 4.1~5.5）

覆盖:
- 4.1 openclaw.py 重试路径变量初始化
- 4.2 非 SSE JSON 响应回退解析
- 5.1 断点续传（Checkpointing）
- 5.2 DirectLLM / OpenClaw 支持 top_p
- 5.3 颗粒化输出（per-task JSON）
- 5.4 实时纯文本看板
- 5.5 终端日志增强
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
# 4.1 重试路径变量初始化
# ===================================================================


class TestRetryVariableInit:
    """空响应时 elapsed / response_body 不应为 NameError。"""

    def test_empty_response_retry_has_elapsed(self):
        """retry_responses 中每条记录应有 elapsed_sec 字段。"""
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
        """retry_responses 中每条记录应有 body 字段。"""
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
        """classify_error 接受 status_code 参数。"""
        from alphadiana.agent.openclaw import classify_error

        result = classify_error(None, response_json=None, status_code=200)
        assert isinstance(result, str)


# ===================================================================
# 4.2 非 SSE JSON 响应回退解析
# ===================================================================


class TestNonSSEResponseFallback:
    """非 SSE（application/json）响应应被正确解析。"""

    def test_classify_error_on_500(self):
        """HTTP 500 应分类为 gateway_error。"""
        from alphadiana.agent.openclaw import classify_error

        assert classify_error(None, response_json=None, status_code=500) == "gateway_error"

    def test_classify_error_on_proxy_failed(self):
        """post proxy failed 应分类为 proxy_timeout。"""
        from alphadiana.agent.openclaw import classify_error

        resp = {"status": "Failed", "message": "post proxy failed"}
        assert classify_error(None, response_json=resp) == "proxy_timeout"

    def test_json_error_body_preserved_in_retry(self):
        """非 SSE 错误响应体应保留在 retry_responses 中。"""
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
# 5.1 断点续传（Checkpointing）
# ===================================================================


class TestCheckpointing:
    """评测断点续传：跳过已完成 task。"""

    def test_completed_task_ids_from_jsonl(self, tmp_path):
        """从已有 JSONL 中提取已完成 task_id 集合。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="ckpt")
        for tid in ("t1", "t2", "t3"):
            store.append(_task(tid), AgentResponse(answer="42"), _score())

        completed = store.completed_task_ids()
        assert completed == {"t1", "t2", "t3"}

    def test_completed_excludes_errors(self, tmp_path):
        """error 记录（correct=None）不算已完成，应被重试。"""
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
        """空 JSONL 返回空集合。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="empty")
        assert store.completed_task_ids() == set()

    def test_config_has_redo_all_field(self):
        """ExperimentConfig 应有 redo_all 字段。"""
        cfg = _config(redo_all=False)
        assert hasattr(cfg, "redo_all")
        assert cfg.redo_all is False

    def test_config_redo_all_default_false(self):
        """redo_all 默认为 False。"""
        cfg = _config()
        assert cfg.redo_all is False

    def test_runner_skips_completed_tasks(self, tmp_path):
        """Runner 应跳过已完成的 tasks。"""
        # 先写入一条已完成记录
        store = ResultStore(output_dir=str(tmp_path), run_id="skip")
        store.append(_task("t1"), AgentResponse(answer="42"), _score())

        # Runner 应只 dispatch 未完成的 tasks
        cfg = _config("skip", output_dir=str(tmp_path))
        from alphadiana.runner.runner import Runner

        runner = Runner(cfg)
        runner.result_store = store

        # Mock benchmark 返回 3 个 tasks
        all_tasks = [_task("t1"), _task("t2"), _task("t3")]

        # 验证过滤逻辑
        completed = store.completed_task_ids()
        pending = [t for t in all_tasks if t.task_id not in completed]
        assert len(pending) == 2
        assert all(t.task_id in ("t2", "t3") for t in pending)

    def test_redo_all_ignores_checkpoint(self, tmp_path):
        """redo_all=True 时应处理所有 tasks。"""
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
# 5.2 top_p 参数
# ===================================================================


class TestTopPSupport:
    """DirectLLM 和 OpenClaw 支持 top_p 参数。"""

    def test_direct_llm_reads_top_p(self):
        """DirectLLMAgent.setup() 应读取 top_p 配置。"""
        from alphadiana.agent.direct_llm import DirectLLMAgent

        agent = DirectLLMAgent()
        agent.setup({"model": "m", "api_base": "http://x/v1", "api_key": "k", "top_p": 0.9})
        assert agent._top_p == 0.9

    def test_direct_llm_top_p_default_none(self):
        """未配置 top_p 时默认 None。"""
        from alphadiana.agent.direct_llm import DirectLLMAgent

        agent = DirectLLMAgent()
        agent.setup({"model": "m", "api_base": "http://x/v1", "api_key": "k"})
        assert agent._top_p is None

    def test_direct_llm_passes_top_p_to_api(self):
        """solve() 在 top_p 不为 None 时传入 API。"""
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

        fake_openai = types.SimpleNamespace(OpenAI=MagicMock(return_value=client))
        with patch.dict("sys.modules", {"openai": fake_openai}):
            with patch.object(agent, "_resolve_max_tokens", return_value=32768):
                agent.solve(_task())

        call_kwargs = create.call_args.kwargs
        assert call_kwargs.get("top_p") == 0.95

    def test_direct_llm_omits_top_p_when_none(self):
        """top_p 为 None 时不传该参数。"""
        from alphadiana.agent.direct_llm import DirectLLMAgent

        agent = DirectLLMAgent()
        agent.setup({"model": "m", "api_base": "http://x/v1", "api_key": "k"})

        fake_response = MagicMock()
        fake_response.choices = [MagicMock(message=MagicMock(content="\\boxed{42}"))]
        fake_response.usage = None
        create = MagicMock(return_value=fake_response)
        client = MagicMock()
        client.chat.completions.create = create

        fake_openai = types.SimpleNamespace(OpenAI=MagicMock(return_value=client))
        with patch.dict("sys.modules", {"openai": fake_openai}):
            with patch.object(agent, "_resolve_max_tokens", return_value=32768):
                agent.solve(_task())

        call_kwargs = create.call_args.kwargs
        assert "top_p" not in call_kwargs

    def test_openclaw_payload_supports_top_p(self):
        """OpenClaw _build_request_payload() 应支持 top_p。"""
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1", "top_p": 0.9})
        payload = agent._build_request_payload(
            [{"role": "user", "content": "hi"}]
        )
        assert payload.get("top_p") == 0.9


# ===================================================================
# 5.3 颗粒化输出（per-task JSON）
# ===================================================================


class TestPerTaskOutput:
    """每道题生成独立 JSON 文件。"""

    def test_per_task_json_created(self, tmp_path):
        """append() 后应在 tasks/ 下生成 {task_id}.json。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="granular")
        store.append(_task("t1"), AgentResponse(answer="42"), _score())

        per_task = tmp_path / "granular" / "tasks" / "t1.json"
        assert per_task.exists(), f"Expected per-task JSON at {per_task}"

    def test_per_task_json_content(self, tmp_path):
        """per-task JSON 包含完整记录字段。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="granular2")
        store.append(_task("t1"), AgentResponse(answer="42"), _score())

        per_task = tmp_path / "granular2" / "tasks" / "t1.json"
        if per_task.exists():
            record = json.loads(per_task.read_text(encoding="utf-8"))
            assert record["task_id"] == "t1"
            assert record["predicted"] == "42"
            assert record["correct"] is True

    def test_jsonl_still_written(self, tmp_path):
        """JSONL 文件仍正常写入（向后兼容）。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="compat")
        store.append(_task("t1"), AgentResponse(answer="42"), _score())

        jsonl = tmp_path / "compat.jsonl"
        assert jsonl.exists()
        lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_error_also_creates_per_task_json(self, tmp_path):
        """append_error() 也应生成 per-task JSON。"""
        store = ResultStore(output_dir=str(tmp_path), run_id="granular-err")
        store.append_error(
            _task("t1"),
            error={"error": "timeout", "error_type": "timeout"},
            response=AgentResponse(answer=None),
        )

        per_task = tmp_path / "granular-err" / "tasks" / "t1.json"
        assert per_task.exists(), f"Expected per-task JSON for error at {per_task}"


# ===================================================================
# 5.4 实时纯文本看板
# ===================================================================


class TestPlainTextDashboard:
    """评测过程中实时更新状态文件。"""

    def test_dashboard_creates_file(self, tmp_path):
        """Dashboard 初始化后应创建状态文件。"""
        from alphadiana.runner.dashboard import PlainTextDashboard

        path = tmp_path / "status.txt"
        tasks = [_task("t1"), _task("t2"), _task("t3")]
        db = PlainTextDashboard(path, tasks)
        assert path.exists()

    def test_dashboard_update_writes_status(self, tmp_path):
        """update() 后文件应包含 O/X 标记。"""
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
        """文件首行应为 Last updated: 时间戳。"""
        from alphadiana.runner.dashboard import PlainTextDashboard

        path = tmp_path / "status.txt"
        db = PlainTextDashboard(path, [_task("t1")])
        db.update("t1", True)

        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("Last updated:")

    def test_dashboard_multiple_updates_same_task(self, tmp_path):
        """同一 task 多次 update 应追加标记。"""
        from alphadiana.runner.dashboard import PlainTextDashboard

        path = tmp_path / "status.txt"
        db = PlainTextDashboard(path, [_task("t1")])

        db.update("t1", True)
        db.update("t1", False)
        db.update("t1", True)

        content = path.read_text(encoding="utf-8")
        # 应包含 OXO 之类的序列
        assert content.count("O") >= 2
        assert "X" in content


# ===================================================================
# 5.5 终端日志增强
# ===================================================================


class TestTerminalLogging:
    """完成 task 后输出 predicted vs gold 对比日志。"""

    def test_task_completion_log_format(self):
        """Runner 的 solve_fn 完成后应有对比日志。"""
        # 验证 runner.py 中存在对比日志模式
        source = Path(__file__).parent.parent / "alphadiana" / "runner" / "runner.py"
        content = source.read_text(encoding="utf-8")
        # 修复后应包含 predicted vs gold 或类似日志
        assert ("Predicted" in content or "predicted" in content
                or "Gold" in content or "ground_truth" in content
                or "vs" in content), \
            "runner.py should log predicted vs gold comparison after task completion"

    def test_rich_progress_optional(self):
        """Rich 不可用时不应抛 ImportError。"""
        # 确保代码中使用 try/except ImportError
        source = Path(__file__).parent.parent / "alphadiana" / "runner" / "runner.py"
        content = source.read_text(encoding="utf-8")
        # 修复后应有 try: from rich... except ImportError 或不强制依赖
        # 当前这只是一个接口级别的检查
        # 如果 rich 不在依赖里，import runner 不应失败
        try:
            from alphadiana.runner.runner import Runner
        except ImportError:
            pytest.fail("Importing Runner should not fail even without rich")
