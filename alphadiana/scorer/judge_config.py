"""Explicit provider configuration shared by scoring and trace annotation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class JudgeConfig:
    model: str
    base_url: str
    api_key: str = field(repr=False)

    @classmethod
    def resolve(cls, *, model=None, base_url=None, api_key=None, analysis=False):
        if analysis:
            model = (
                model if model is not None else (os.getenv("ANALYSIS_MODEL") or None)
            )
            base_url = (
                base_url
                if base_url is not None
                else (os.getenv("ANALYSIS_BASE_URL") or None)
            )
            api_key = (
                api_key
                if api_key is not None
                else (os.getenv("ANALYSIS_API_KEY") or None)
            )
        values = {
            "OPENAI_MODEL": model
            if model is not None
            else (
                os.getenv("OPENAI_MODEL")
                or os.getenv("OPENAI_MODEL_NAME")
                or os.getenv("JUDGE_MODEL")
            ),
            "OPENAI_BASE_URL": base_url
            if base_url is not None
            else (os.getenv("OPENAI_BASE_URL") or os.getenv("JUDGE_API_BASE")),
            "OPENAI_API_KEY": api_key
            if api_key is not None
            else os.getenv("OPENAI_API_KEY"),
        }
        missing = [
            key
            for key, value in values.items()
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            raise ValueError("Configure the judge explicitly: " + ", ".join(missing))
        endpoint = values["OPENAI_BASE_URL"].strip().rstrip("/")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "OPENAI_BASE_URL must be an HTTP(S) API root without credentials, query or fragment"
            )
        return cls(values["OPENAI_MODEL"].strip(), endpoint, values["OPENAI_API_KEY"])
