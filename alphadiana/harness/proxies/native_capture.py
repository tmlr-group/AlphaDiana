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
    def __init__(self, config: dict, directory: Path, *, measure_usage: bool = False):
        self.measure_usage = measure_usage
        self.config = config
        self.directory = directory
        self.capture = resolve_logprob_capture_config(config)
        self.api_base = config["api_base"]
        self.proxy = None
        self.records: list[dict] = []
        self.requests: list[dict] = []
        self.responses: list[dict] = []

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
                if self.measure_usage:
                    self.responses = proxy.drain_response_summaries()
            finally:
                proxy.stop()
                files = {
                    "provider_logprobs.jsonl": self.records,
                    "provider_requests.jsonl": self.requests,
                }
                if self.measure_usage:
                    files["provider_responses.jsonl"] = self.responses
                for name, rows in files.items():
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
        if self.measure_usage:
            usage, provenance = complete_provider_usage(self.requests, self.responses)
            response.token_usage = usage
            response.metadata["token_usage_provenance"] = provenance
            response.workspace_file_contents["provider_responses.jsonl"] = "".join(
                json.dumps(row) + "\n" for row in self.responses
            )
        return response


def complete_provider_usage(
    requests: list[dict], responses: list[dict]
) -> tuple[dict, dict]:
    """Require exactly one successful, fully counted response per captured call.

    A failed/missing request is not imputed as zero. Repeated provider calls with
    distinct capture IDs are real work and count separately.
    """
    provenance = {
        "source": "provider_response_usage",
        "requests": len(requests),
        "responses": len(responses),
        "complete": False,
    }
    request_ids = [row.get("call_id") for row in requests]
    response_ids = [row.get("call_id") for row in responses]
    if (
        not request_ids
        or any(not isinstance(x, str) or not x for x in request_ids)
        or len(set(request_ids)) != len(request_ids)
        or len(set(response_ids)) != len(response_ids)
        or set(request_ids) != set(response_ids)
    ):
        return {}, dict(provenance, missing="incomplete_call_identity")
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for row in responses:
        if (
            type(row.get("status_code")) is not int
            or not 200 <= row["status_code"] < 300
        ):
            return {}, dict(provenance, missing="unsuccessful_provider_response")
        if any(type(row.get(key)) is not int or row[key] < 0 for key in totals):
            return {}, dict(provenance, missing="incomplete_provider_usage")
        if row["total_tokens"] != row["prompt_tokens"] + row["completion_tokens"]:
            return {}, dict(provenance, missing="inconsistent_provider_usage")
        for key in totals:
            totals[key] += row[key]
    return totals, dict(provenance, complete=True)
