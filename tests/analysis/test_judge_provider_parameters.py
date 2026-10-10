import json
from types import SimpleNamespace

from alphadiana.analysis.annotations.judge import CachedJudge


def test_provider_controls_are_explicit_and_part_of_cache_identity(tmp_path):
    with CachedJudge(
        cache_dir=tmp_path,
        model="provider-model",
        api_key="test",
        base_url="http://127.0.0.1:1/v1",
        temperature=None,
        token_limit_parameter="max_completion_tokens",
    ) as judge:
        requests = []

        def create(**kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content='{"ok":true}'),
                    )
                ],
                model="provider-model",
                id="test-response",
                usage=None,
            )

        judge.client.chat.completions.create = create
        answer, digest = judge.judge(
            {}, prompt="Return JSON.", validate=lambda value: None
        )
        assert answer == {"ok": True}
        assert "temperature" not in requests[0]
        assert "max_tokens" not in requests[0]
        assert requests[0]["max_completion_tokens"] == 2048
        assert requests[0]["messages"][1]["content"].startswith("Respond with a JSON object.\n")
        cached = json.loads((tmp_path / (digest + ".json")).read_text())
        assert "temperature" not in cached["request"]
        assert cached["request"]["max_completion_tokens"] == 2048
