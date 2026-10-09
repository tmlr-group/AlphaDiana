"""Cached, strict JSON judgments with explicit provider provenance."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from alphadiana.scorer.judge_config import JudgeConfig


class CachedJudge:
    def __init__(
        self,
        *,
        cache_dir,
        model=None,
        model_version=None,
        base_url=None,
        api_key=None,
        extra_body=None,
        response_format=None,
    ):
        config = JudgeConfig.resolve(
            model=model, base_url=base_url, api_key=api_key, analysis=True
        )
        from openai import OpenAI

        self.model = config.model
        self.model_version = (
            model_version
            or os.getenv("ANALYSIS_MODEL_VERSION")
            or os.getenv("OPENAI_MODEL_VERSION")
            or config.model
        )
        self.base_url = config.base_url
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.extra_body = deepcopy(extra_body or {})
        self.response_format = deepcopy(response_format or {"type": "json_object"})
        self.client = OpenAI(
            base_url=config.base_url, api_key=config.api_key, max_retries=0, timeout=180
        )

    def judge(self, evidence, *, prompt, validate, max_tokens=2048):
        request = {
            "model": self.model,
            "model_version": self.model_version,
            "base_url": self.base_url,
            "prompt": prompt,
            "evidence": evidence,
            "temperature": 0,
            "max_tokens": max_tokens,
            "extra_body": self.extra_body,
            "response_format": self.response_format,
        }
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True).encode()
        ).hexdigest()
        path = self.cache / f"{digest}.json"
        if path.exists():
            stored = json.loads(path.read_text())
            if stored.get("request") != request:
                raise ValueError(f"Annotation cache provenance mismatch: {path}")
            answer = stored["answer"]
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=max_tokens,
                response_format=self.response_format,
                extra_body=self.extra_body,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(evidence)},
                ],
            )
            try:
                if not response.choices or response.choices[0].finish_reason != "stop":
                    raise ValueError(
                        "Annotation did not finish with a complete response"
                    )
                answer = json.loads(response.choices[0].message.content or "")
                validate(answer)
            except (ValueError, TypeError, KeyError) as exc:
                # Failed evidence must not look like a reusable valid result.
                attempts = self.cache / "failed-attempts"
                attempts.mkdir(exist_ok=True)
                (attempts / f"{digest}-{uuid4().hex}.json").write_text(
                    json.dumps(
                        {
                            "request": request,
                            "response_id": response.id,
                            "response_model": response.model,
                            "choices": [
                                {
                                    "finish_reason": c.finish_reason,
                                    "content": c.message.content,
                                }
                                for c in response.choices
                            ],
                            "usage": (
                                response.usage.model_dump() if response.usage else None
                            ),
                            "error": str(exc),
                        },
                        indent=2,
                    ),
                    encoding="utf8",
                )
                raise
            stored = {
                "request": request,
                "answer": answer,
                "response_model": response.model,
                "response_id": response.id,
                "usage": response.usage.model_dump() if response.usage else None,
            }
            temporary = path.with_suffix(f".{uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(stored, indent=2))
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        validate(answer)
        return answer, digest

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
