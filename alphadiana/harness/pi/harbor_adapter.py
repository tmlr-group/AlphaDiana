"""Harbor-side installed-agent adapter for an exact local Pi runtime."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class PiHarborInstalledAgent(BaseInstalledAgent):
    """Run Pi inside Harbor's disposable Terminal-Bench task environment."""

    _OUTPUT_FILENAME = "pi.jsonl"

    def __init__(
        self,
        *args,
        candidate_path: str,
        audit_path: str,
        models_path: str,
        pi_version: str,
        pi_model: str,
        reasoning: str = "max",
        provider_reasoning: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.candidate_path = Path(candidate_path).resolve()
        self.audit_path = Path(audit_path).resolve()
        self.models_path = Path(models_path).resolve()
        self.pi_version = pi_version
        self.pi_model = pi_model
        self.reasoning = reasoning
        self.provider_reasoning = provider_reasoning or reasoning

    @staticmethod
    def name() -> str:
        return "alphadiana-pi-harbor"

    def version(self) -> str:
        return self.pi_version

    async def install(self, environment: BaseEnvironment) -> None:
        result = await environment.exec(
            command="PATH=/opt/alphadiana-pi-runtime:$PATH /opt/alphadiana-pi-runtime/pi-install/pi --version"
        )
        if result.return_code != 0:
            raise RuntimeError("read-only Pi runtime mount is unavailable")
        await self.exec_as_agent(
            environment,
            command="mkdir -p $HOME/.pi/agent /opt/alphadiana-pi /logs/agent",
        )
        await environment.upload_file(self.models_path, "/opt/alphadiana-pi/models.json")
        await environment.upload_file(self.audit_path, "/opt/alphadiana-pi/audit.ts")
        await environment.upload_file(self.candidate_path, "/opt/alphadiana-pi/candidate.ts")
        await self.exec_as_agent(
            environment,
            command=(
                "cp /opt/alphadiana-pi/models.json $HOME/.pi/agent/models.json && "
                "chmod 0644 /opt/alphadiana-pi/*.ts $HOME/.pi/agent/models.json"
            ),
        )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required")
        command = (
            "PATH=/opt/alphadiana-pi-runtime:$PATH "
            "/opt/alphadiana-pi-runtime/pi-install/pi "
            "--print --mode json --no-session --no-extensions --no-skills "
            "--no-prompt-templates --no-themes --no-context-files "
            "-e /opt/alphadiana-pi/audit.ts -e /opt/alphadiana-pi/candidate.ts "
            f"--model {shlex.quote(self.pi_model)} --thinking {shlex.quote(self.reasoning)} "
            f"{shlex.quote(instruction)} 2>&1 </dev/null | "
            f"stdbuf -oL tee /logs/agent/{self._OUTPUT_FILENAME}"
        )
        await self.exec_as_agent(
            environment,
            command=command,
            env={
                "OPENAI_API_KEY": api_key,
                "ALPHADIANA_PI_EXPECTED_MODEL": self.pi_model.split("/", 1)[-1],
                "ALPHADIANA_PI_EXPECTED_REASONING": self.provider_reasoning,
                "ALPHADIANA_PI_REQUEST_LOG": "/logs/agent/request-audit.jsonl",
                "ALPHADIANA_PI_FIRING_LOG": "/logs/agent/mechanism-firings.jsonl",
            },
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        output_file = self.logs_dir / self._OUTPUT_FILENAME
        if not output_file.exists():
            return
        totals = {"input": 0, "output": 0, "cacheRead": 0, "cost": 0.0}
        for line in output_file.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "message_end":
                continue
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            usage = message.get("usage") or {}
            totals["input"] += int(usage.get("input", 0) or 0)
            totals["output"] += int(usage.get("output", 0) or 0)
            totals["cacheRead"] += int(usage.get("cacheRead", 0) or 0)
            totals["cost"] += float((usage.get("cost") or {}).get("total", 0.0) or 0.0)
        context.n_input_tokens = totals["input"] + totals["cacheRead"]
        context.n_output_tokens = totals["output"]
        context.n_cache_tokens = totals["cacheRead"]
        context.cost_usd = totals["cost"] or None
