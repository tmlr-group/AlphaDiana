from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_solve_uses_multimodal_api_for_image_tasks():
    agent = OpenCodeAgent()
    agent.setup({
        "model_name": "test-model",
        "api_base": "http://localhost:9001/v1",
        "api_key": "test-key",
        "timeout": 5,
    })

    usage = MagicMock(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    message = MagicMock(content="Done.\\boxed{A}")
    message.model_extra = {}
    choice = MagicMock(message=message, finish_reason="stop")
    response = MagicMock(choices=[choice], usage=usage)
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=response)

    task = BenchmarkTask(
        task_id="hle_0",
        problem="Which option is correct?",
        ground_truth="A",
        attachments={
            "image_1": b"\x89PNG\r\n",
            "image_1_mime": b"image/png",
        },
    )

    with patch("alphadiana.agent.opencode.OpenAI", return_value=client):
        with patch("subprocess.Popen") as popen:
            result = agent.solve(task)

    popen.assert_not_called()
    call_kwargs = client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "test-model"
    content = call_kwargs["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result.answer == "A"
    assert result.metadata["transport"] == "openai_multimodal"
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
