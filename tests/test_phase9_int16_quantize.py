"""Phase 9: int16 logprob quantisation helper + DirectLLMAgent wiring."""
from __future__ import annotations

import math

import pytest

from alphadiana.agent._logprobs import (
    INT16_PROB_SCALE,
    _prob_to_i16,
    quantize_record_int16,
    quantize_records_int16,
)


class TestProbToI16:
    def test_zero(self):
        assert _prob_to_i16(0.0) == 0

    def test_one(self):
        assert _prob_to_i16(1.0) == INT16_PROB_SCALE

    def test_mid(self):
        assert _prob_to_i16(0.5) == round(0.5 * INT16_PROB_SCALE)

    def test_above_one_clamps(self):
        assert _prob_to_i16(1.5) == INT16_PROB_SCALE

    def test_below_zero_clamps(self):
        assert _prob_to_i16(-0.2) == 0


class TestQuantizeRecord:
    def _record(self, logprobs):
        return {
            "token_index": 3,
            "token": "the",
            "logprob": logprobs[0],
            "top_logprobs": [{"token": f"t{i}", "logprob": lp} for i, lp in enumerate(logprobs)],
        }

    def test_confident_token_entropy_near_zero(self):
        # One candidate dominates -> entropy close to 0.
        rec = self._record([-0.0001] + [-20.0] * 19)
        q = quantize_record_int16(rec)
        assert q["token_index"] == 3
        assert q["token"] == "the"
        assert len(q["top20"]) == 20
        # Top candidate gets ~full 32767 mass.
        assert q["top20"][0]["prob_i16"] == INT16_PROB_SCALE
        # Tail gets floored to 0.
        assert q["top20"][-1]["prob_i16"] == 0
        assert q["entropy_nats"] < 1e-5

    def test_uniform_top_k_matches_log_k(self):
        # 20 identical logprobs -> uniform dist -> entropy = log(20).
        rec = self._record([-1.0] * 20)
        q = quantize_record_int16(rec)
        assert q["entropy_nats"] == pytest.approx(math.log(20), abs=1e-9)
        # All candidates get the same (rounded) prob_i16.
        codes = {e["prob_i16"] for e in q["top20"]}
        assert len(codes) == 1
        # Should be ~32767/20 = 1638.35 -> 1638 after rounding.
        assert q["top20"][0]["prob_i16"] == round(INT16_PROB_SCALE / 20)

    def test_top_k_truncation(self):
        rec = self._record([-1.0] * 25)
        q = quantize_record_int16(rec, top_k=20)
        assert len(q["top20"]) == 20

    def test_empty_top_logprobs(self):
        rec = {"token_index": 0, "token": "x", "logprob": -0.1, "top_logprobs": []}
        q = quantize_record_int16(rec)
        assert q["top20"] == []
        assert q["entropy_nats"] == 0.0


class TestQuantizeMany:
    def test_applies_to_each_record(self):
        recs = [
            {"token_index": i, "token": f"t{i}",
             "logprob": -0.1, "top_logprobs": [{"token": "a", "logprob": -0.01},
                                               {"token": "b", "logprob": -10.0}]}
            for i in range(3)
        ]
        out = quantize_records_int16(recs)
        assert len(out) == 3
        for i, q in enumerate(out):
            assert q["token_index"] == i
            assert {"token", "prob_i16"} == set(q["top20"][0].keys())


class TestParityWithStandaloneScript:
    """Framework quantiser output must match the standalone phase9 script's
    output for the same input — otherwise switching between the two paths
    would silently change the on-disk JSONL."""

    def _sdk_like(self, token: str, logprob: float, top_pairs: list[tuple[str, float]]):
        """Minimal duck-type that matches the OpenAI SDK's ChoiceLogprob object."""
        class _Entry:
            def __init__(self, t, lp):
                self.token = t
                self.logprob = lp
        return _Entry(token, logprob), [_Entry(t, lp) for t, lp in top_pairs]

    def test_confident_record_matches_script(self):
        from scripts.phase9_gpqa_top20_int16_entropy import top_logprob_to_int16_record
        top_pairs = [("the", -0.0001), ("a", -12.0), ("an", -13.5)] + [
            (f"x{i}", -20.0 - i * 0.1) for i in range(17)
        ]
        tok, top = self._sdk_like("the", -0.0001, top_pairs)
        # Give the SDK-like object a `.top_logprobs` attribute.
        tok.top_logprobs = top

        script_out = top_logprob_to_int16_record(tok, token_index=7)
        framework_out = quantize_record_int16({
            "token_index": 7,
            "token": "the",
            "logprob": -0.0001,
            "top_logprobs": [{"token": t, "logprob": lp} for t, lp in top_pairs],
        })
        assert script_out == framework_out

    def test_uniform_record_matches_script(self):
        from scripts.phase9_gpqa_top20_int16_entropy import top_logprob_to_int16_record
        top_pairs = [(f"t{i}", -1.0) for i in range(20)]
        tok, top = self._sdk_like("t0", -1.0, top_pairs)
        tok.top_logprobs = top

        script_out = top_logprob_to_int16_record(tok, token_index=0)
        framework_out = quantize_record_int16({
            "token_index": 0,
            "token": "t0",
            "logprob": -1.0,
            "top_logprobs": [{"token": t, "logprob": lp} for t, lp in top_pairs],
        })
        assert script_out == framework_out

    def test_empty_top_matches_script(self):
        from scripts.phase9_gpqa_top20_int16_entropy import top_logprob_to_int16_record

        class _Tok:
            token = "x"
            top_logprobs = []
        script_out = top_logprob_to_int16_record(_Tok(), token_index=3)
        framework_out = quantize_record_int16({
            "token_index": 3, "token": "x", "logprob": 0.0, "top_logprobs": [],
        })
        assert script_out == framework_out


class TestDirectLLMConfig:
    def _agent(self, **kwargs):
        from alphadiana.agent.direct_llm import DirectLLMAgent
        cfg = {
            "model": "m", "api_base": "http://x/v1", "api_key": "EMPTY",
            "capture_logprobs": True, "top_logprobs": 20,
        }
        cfg.update(kwargs)
        a = DirectLLMAgent()
        try:
            a.setup(cfg)
        except ImportError:
            pytest.skip("openai not installed")
        return a

    def test_default_format_is_int16(self):
        a = self._agent()
        assert a._logprobs_format == "int16"

    def test_float_format_accepted(self):
        a = self._agent(logprobs_format="float")
        assert a._logprobs_format == "float"

    def test_int16_format_accepted(self):
        a = self._agent(logprobs_format="int16")
        assert a._logprobs_format == "int16"

    def test_invalid_format_raises(self):
        from alphadiana.agent.direct_llm import DirectLLMAgent
        a = DirectLLMAgent()
        with pytest.raises(ValueError):
            a.setup({
                "model": "m", "api_base": "http://x/v1", "api_key": "EMPTY",
                "capture_logprobs": True, "logprobs_format": "float64",
            })
