"""Solver driver for disposable containers or explicit trusted host processes.

Pi final-message extraction follows the role/content approach in Diana PR #78
(198d4db0638a69e57a6de16cf2898ab3420cbe2a), generalized without Harbor/TB2.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PHASE = "initialization"


def pi_compaction_settings(context_window, max_tokens):
    # Pi also uses 80% of this reserve as the summary output budget. Scaling it
    # with the solver's max_tokens produces oversized summaries; keep its default.
    reserve = 16384
    if (
        type(context_window) is not int
        or context_window <= max(reserve, max_tokens) + 4096
    ):
        raise ValueError(
            "context_window must be an integer and leave room for "
            "max(max_tokens, 16384) plus 4096 tokens"
        )
    return {
        "enabled": True,
        "reserveTokens": reserve,
        "keepRecentTokens": min(20000, (context_window - reserve) // 2),
    }


def text_blocks(content, kind="text"):
    if isinstance(content, str):
        return content if kind == "text" else ""
    return "\n".join(
        str(block.get(kind, ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == kind
    )


def pi_run(request):
    global PHASE
    config = request["config"]
    home = Path(os.environ["HOME"]) / ".pi" / "agent"
    home.mkdir(parents=True)
    model = {
        "id": config["model"],
        "name": config["model"],
        "reasoning": bool(config.get("reasoning")),
        "input": ["text"],
        "contextWindow": config.get("context_window", 128000),
        "maxTokens": config.get("max_tokens", 4096),
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
    }
    if "presence_penalty" in config:
        model["samplingParams"] = {"presence_penalty": config["presence_penalty"]}
    settings = {}
    if "context_window" in config:
        settings["compaction"] = pi_compaction_settings(
            model["contextWindow"], model["maxTokens"]
        )
    if "bash_timeout" in config:
        if not Path("/usr/bin/timeout").is_file() or not Path("/bin/bash").is_file():
            raise ValueError("bash_timeout requires GNU timeout and bash in the image")
        # Pi receives exit 124 as a tool failure and can continue the episode.
        # GNU timeout terminates the shell's process group, including children.
        shell = home / "bounded-bash"
        shell.write_text(
            "#!/bin/sh\nexec /usr/bin/timeout --verbose --kill-after=5s "
            f'{config["bash_timeout"]}s /bin/bash "$@"\n'
        )
        shell.chmod(0o700)
        settings["shellPath"] = str(shell)
    if settings:
        (home / "settings.json").write_text(json.dumps(settings))
    (home / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "diana": {
                        "baseUrl": config["api_base"],
                        "api": "openai-completions",
                        "apiKey": "$DIANA_MODEL_API_KEY",
                        "models": [model],
                    }
                }
            }
        )
    )
    environment = {
        **os.environ,
        "PI_CODING_AGENT_DIR": str(home),
        "PI_OFFLINE": "1",
        "PI_TELEMETRY": "0",
        "DIANA_MODEL_API_KEY": config.get("api_key", "keyless"),
    }
    command = [
        config.get("pi_bin", "/opt/pi/node_modules/.bin/pi"),
        "--mode",
        "json",
        "--print",
        "--no-session",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-approve",
        "--provider",
        "diana",
        "--model",
        config["model"],
        "--system-prompt",
        config.get("system_prompt", "Solve the task using available tools."),
    ]
    if "reasoning" in config:
        command += ["--thinking", config["reasoning"]]
    if "candidate_entry" in config:
        command += [
            "--extension",
            config.get("candidate_path", "/candidate/" + config["candidate_entry"]),
        ]
    # stdin prevents a task string from becoming a CLI option or @file argument.
    PHASE = "execution"
    process = subprocess.run(
        command,
        input=request["problem"],
        env=environment,
        text=True,
        capture_output=True,
        cwd=request.get("work_dir"),
        check=True,
        timeout=config.get("timeout", 120),
    )
    if any(
        marker in process.stderr.lower()
        for marker in (
            "failed to load extension",
            "extension issues",
            "extension errors",
            "extension error (",
            "error loading extension",
        )
    ):
        PHASE = "plugin"
        raise ValueError("Pi extension failed to load")
    PHASE = "protocol"
    events = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    messages = [
        event["message"] for event in events if event.get("type") == "message_end"
    ]
    assistants = [m for m in messages if m.get("role") == "assistant"]
    if not assistants:
        raise ValueError("Pi returned no assistant message")
    final = assistants[-1]
    if final.get("stopReason") in {"error", "aborted"}:
        PHASE = "execution"
        raise RuntimeError("Pi model request did not complete")
    usage = {}
    for message in assistants:
        values = message.get("usage", {})
        if "input" in values or "output" in values:
            usage["prompt_tokens"] = (
                usage.get("prompt_tokens", 0)
                + values.get("input", 0)
                + values.get("cacheRead", 0)
                + values.get("cacheWrite", 0)
            )
            usage["completion_tokens"] = usage.get("completion_tokens", 0) + values.get(
                "output", 0
            )
    return {
        "answer": text_blocks(final.get("content", [])),
        "trajectory": pi_messages(messages),
        "response_json": {"native_events": events},
        "reasoning_trajectory": [
            {
                "role": "assistant",
                "type": "reasoning",
                "content": text_blocks(m.get("content", []), "thinking"),
            }
            for m in assistants
            if text_blocks(m.get("content", []), "thinking")
        ],
        "token_usage": usage,
        "finish_reason": final.get("stopReason"),
    }


def pi_messages(messages):
    """Map Pi blocks and toolResult linkage to the persisted Diana schema."""
    trajectory = []
    for message in messages:
        if message.get("role") == "toolResult":
            trajectory.append(
                {
                    "role": "tool_result",
                    "type": "tool_result",
                    "tool_use_id": message["toolCallId"],
                    "content": text_blocks(message.get("content", [])),
                    "is_error": message.get("isError", False),
                }
            )
            continue
        step = {
            "role": message["role"],
            "content": text_blocks(message.get("content", [])),
        }
        thinking = text_blocks(message.get("content", []), "thinking")
        if thinking:
            step["thinking"] = thinking
        calls = [
            block
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "toolCall"
        ]
        if calls:
            step["tool_calls"] = [
                {"id": call["id"], "tool": call["name"], "input": call["arguments"]}
                for call in calls
            ]
        trajectory.append(step)
    return trajectory


def deepseek_events(events):
    """Project completed messages/tools; chunks duplicate message-level usage."""
    trajectory = []
    usage = {}
    for event in events:
        kind, data = event.get("type"), event.get("data", {})
        if kind == "assistant/message":
            message = data["message"]
            content = text_blocks(message.get("content", []))
            thinking = "\n".join(
                block["text"]
                for block in message.get("content", [])
                if block.get("type") == "reasoning"
            )
            if content or thinking:
                step = {"role": "assistant", "content": content}
                if thinking:
                    step["thinking"] = thinking
                trajectory.append(step)
            # The pinned runtime publishes the aggregate for each completed
            # model request here, as well as duplicate streaming usage chunks.
            values = data.get("usage") or {}
            for source, target in (
                ("inputTokens", "prompt_tokens"),
                ("outputTokens", "completion_tokens"),
                ("totalTokens", "total_tokens"),
            ):
                if source in values:
                    usage[target] = usage.get(target, 0) + values[source]
        elif kind == "user/message":
            trajectory.append(
                {"role": "user", "content": text_blocks(data.get("content", []))}
            )
        elif kind == "tool/call":
            arguments = data.get("arguments", {})
            if isinstance(arguments, str):
                # Preserve unparsable tool input for diagnosis, not a fake call.
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {"raw_arguments": arguments}
            trajectory.append(
                {
                    "role": "assistant",
                    "type": "tool_use",
                    "id": data["callId"],
                    "tool": data["name"],
                    "input": arguments,
                }
            )
        elif kind == "tool/result":
            for block in data["message"].get("content", []):
                if block.get("type") == "tool-result":
                    trajectory.append(
                        {
                            "role": "tool_result",
                            "type": "tool_result",
                            "tool_use_id": block["toolCallId"],
                            "content": text_blocks(block.get("content", [])),
                            "is_error": block.get("isError", False),
                        }
                    )
    return trajectory, usage


def deepseek_run(request):
    global PHASE
    from deepseek_harness import DeepSeekHarness

    config = request["config"]
    options = {
        "provider": "deepseek-official",
        "model": config["model"],
        "cwd": request.get("work_dir", "/work"),
        "runtime_cwd": request.get("work_dir", "/work"),
        "dsh_home": str(Path(os.environ["HOME"]) / "dsh-home")
        if config.get("runtime") == "process"
        else "/tmp/dsh-home",
        "profile": "sdk-minimal",
        "base_url": config["api_base"],
        "api_key": config.get("api_key", "keyless"),
        "request_timeout_seconds": config.get("timeout", 120),
        "env": {
            "DSH_SYSTEM_PROMPT": config.get(
                "system_prompt", "Solve the task using available tools."
            )
        },
    }
    for field, target in (
        ("reasoning", "reasoning_effort"),
        ("max_tokens", "max_tokens"),
    ):
        if field in config:
            options[target] = config[field]
    if config.get("training", {}).get("enabled", True) and "training" in config:
        # The checkpoint contract freezes enable_thinking=False. The SDK's
        # default is high reasoning, which would otherwise change that policy.
        options["reasoning_effort"] = "off"
    if "candidate_entry" in config:
        # JSON is valid YAML. The runtime compiles the local TS plugin; no package
        # installation or candidate-controlled startup command is involved.
        patch = (
            Path(os.environ["HOME"]) / "candidate.patch.yml"
            if config.get("runtime") == "process"
            else Path("/tmp/candidate.patch.yml")
        )
        patch.write_text(
            json.dumps(
                [
                    {
                        "insert": [
                            {
                                "id": "diana-candidate",
                                "name": config.get(
                                    "candidate_path",
                                    "/candidate/" + config["candidate_entry"],
                                ),
                            }
                        ]
                    }
                ]
            )
        )
        options["patches"] = (str(patch),)
    PHASE = "initialization"
    with DeepSeekHarness(**options) as harness:
        PHASE = "execution"
        result = harness.run(request["problem"], session_id="task")
    trajectory, usage = deepseek_events(result.events)
    return {
        "answer": result.final_response,
        "finish_reason": result.finish_reason,
        "trajectory": trajectory,
        "reasoning_trajectory": [
            {"role": "assistant", "type": "reasoning", "content": step["thinking"]}
            for step in trajectory
            if step.get("thinking")
        ],
        "token_usage": usage,
        "response_json": {"native_events": result.events},
    }


def main():
    request = json.load(sys.stdin)
    Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
    attachments = Path(request.get("input_dir", "/input")) / "attachments"
    if attachments.exists():
        shutil.copytree(
            attachments, Path(request.get("work_dir", "/work")) / "attachments"
        )
    result = pi_run(request) if request["harness"] == "pi" else deepseek_run(request)
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps({"error": type(exc).__name__, "stage": PHASE}), file=sys.stderr
        )
        sys.exit(1)
