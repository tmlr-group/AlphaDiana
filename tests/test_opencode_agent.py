import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alphadiana.agent.opencode import OpenCodeAgent, _build_prompt, _extract_event_texts, _extract_strict_answer
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.utils.attachments import write_attachments


def test_extract_event_texts_reads_nested_part_text():
    obj = {
        "type": "text",
        "part": {
            "type": "text",
            "text": "Nested assistant text",
        },
    }
    assert _extract_event_texts(obj) == ["Nested assistant text"]


def test_extract_strict_answer_ignores_partial_reasoning_numbers():
    text = "Trying N=3, then N=6, but still checking cases."
    assert _extract_strict_answer(text) == ""


def test_extract_strict_answer_accepts_boxed_answer():
    text = "Done.\n\\boxed{3}"
    assert _extract_strict_answer(text) == "3"


def test_build_prompt_mentions_attached_files():
    prompt = _build_prompt(
        "What letter is shown?",
        "Use tools when needed.",
        [Path("attachments/image_1.png")],
    )

    assert "--- Attachments ---" in prompt
    assert "image_1.png" in prompt


def test_write_attachments_uses_image_suffixes(tmp_path):
    paths = write_attachments(tmp_path, {
        "image_1": b"\x89PNG\r\n",
        "image_1_mime": b"image/png",
    })

    assert len(paths) == 1
    assert paths[0].name == "image_1.png"
    assert paths[0].read_bytes() == b"\x89PNG\r\n"


def test_solve_uses_cli_with_image_attachments():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
    })

    process = MagicMock()
    process.communicate.return_value = (
        '{"type":"assistant","message":{"content":"Done.\\\\boxed{A}"}}\n',
        "",
    )
    process.returncode = 0
    seen: dict[str, object] = {}

    task = BenchmarkTask(
        task_id="hle_0",
        problem="Which option is correct?",
        ground_truth="A",
        attachments={
            "image_1": b"\x89PNG\r\n",
            "image_1_mime": b"image/png",
        },
    )

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs["cwd"]
        seen["env"] = kwargs["env"]
        config_path = Path(kwargs["env"]["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json"
        seen["provider_config"] = json.loads(config_path.read_text(encoding="utf-8"))
        return process

    with patch("subprocess.Popen", side_effect=fake_popen) as popen:
        result = agent.solve(task)

    popen.assert_called_once()
    cmd = seen["cmd"]
    assert isinstance(cmd, list)
    assert "--file" in cmd
    attachment_path = Path(cmd[cmd.index("--file") + 1])
    assert attachment_path.name == "image_1.png"
    provider_config = seen["provider_config"]
    assert provider_config["model"] == "custom/test-model"
    model_spec = provider_config["provider"]["custom"]["models"]["test-model"]
    assert model_spec["attachment"] is True
    assert model_spec["modalities"]["input"] == ["text", "image"]
    assert result.answer == "A"
    assert result.metadata["transport"] == "opencode_cli"
    assert result.metadata["num_attachments"] == 1


def test_solve_text_only_uses_opencode_cli():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
    })

    process = MagicMock()
    process.communicate.return_value = (
        '{"type":"assistant","message":{"content":"Done.\\\\boxed{A}"}}\n',
        "",
    )
    process.returncode = 0

    task = BenchmarkTask(
        task_id="text_0",
        problem="Which option is correct?",
        ground_truth="A",
    )

    with patch("subprocess.Popen", return_value=process) as popen:
        result = agent.solve(task)

    cmd = popen.call_args.args[0]
    assert "--file" not in cmd
    assert cmd[-2] == "--"
    assert "Which option is correct?" in cmd[-1]
    assert result.answer == "A"
    assert result.metadata["transport"] == "opencode_cli"


def test_setup_rejects_unknown_controller_mode():
    agent = OpenCodeAgent()

    with pytest.raises(ValueError, match="Unsupported opencode controller_mode"):
        agent.setup({
            "model_name": "test-model",
            "api_base": "http://localhost:9001/v1",
            "api_key": "test-key",
            "controller_mode": "doccker",
        })


def test_solve_text_only_uses_docker_runner_when_configured():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
        "controller_mode": "docker",
    })

    task = BenchmarkTask(
        task_id="text_0",
        problem="Which option is correct?",
        ground_truth="A",
    )

    with patch.object(
        agent,
        "_run_in_docker",
        return_value=('{"type":"assistant","message":{"content":"Done.\\\\boxed{A}"}}\n', "", 0),
    ) as run_in_docker:
        with patch("subprocess.Popen") as popen:
            result = agent.solve(task)

    run_in_docker.assert_called_once()
    popen.assert_not_called()
    assert result.answer == "A"
    assert result.metadata["controller_mode"] == "docker"
