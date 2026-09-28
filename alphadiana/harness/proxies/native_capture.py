"""Optional provider observation around an upstream-owned subprocess.

Only probability request fields are injected. Native messages, generation budgets,
streaming choices, retries and tool execution stay with the upstream runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

from alphadiana.harness.base import AgentResponse
from alphadiana.harness.proxies.logprob_capture import (
    finalize_logprob_capture,
    resolve_logprob_capture_config,
)
from alphadiana.harness.proxies.logprob_proxy import LogprobCaptureProxy


class NativeProviderCapture:
    def __init__(self, config: dict, directory: Path):
        self.config = config
        self.directory = directory
        self.capture = resolve_logprob_capture_config(config)
        self.api_base = config["api_base"]
        self.proxy = None
        self.records: list[dict] = []
        self.requests: list[dict] = []

    def __enter__(self):
        if self.capture["enabled"]:
            self.proxy = LogprobCaptureProxy(
                self.api_base,
                self.capture["top_logprobs"],
                upstream_api_key=self.config.get("api_key", "EMPTY"),
                normalize_system_messages=False,
                capture_request_summary=True,
                client_timeout=float(self.config.get("timeout", 1800)),
            )
            try:
                self.proxy.start()
                self.api_base = self.proxy.proxy_url + "/v1"
            except BaseException:
                self.proxy.stop()
                raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.proxy is not None:
            # The native subprocess has finished (or was terminated). Retain
            # observations received so far, including partial failure evidence.
            proxy = self.proxy
            try:
                self.records = proxy.captured_records()
                self.requests = proxy.drain_request_summaries()
            finally:
                proxy.stop()
                for name, rows in (
                    ("provider_logprobs.jsonl", self.records),
                    ("provider_requests.jsonl", self.requests),
                ):
                    (self.directory / name).write_text(
                        "".join(json.dumps(row) + "\n" for row in rows),
                        encoding="utf-8",
                    )

    def apply(self, response: AgentResponse) -> AgentResponse:
        response.token_entropy_stats, response.metadata = finalize_logprob_capture(
            harness=response.metadata.get("native_runtime", "native"),
            enabled=self.capture["enabled"],
            records=self.records,
            metadata=response.metadata,
        )
        if self.capture["enabled"]:
            response.workspace_file_contents["provider_requests.jsonl"] = "".join(
                json.dumps(row) + "\n" for row in self.requests
            )
        return response
