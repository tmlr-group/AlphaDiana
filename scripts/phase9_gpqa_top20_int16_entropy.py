#!/usr/bin/env python3
"""Run GPQA-Diamond directly against an existing vLLM server with Int16 logprobs.

This is intentionally independent of the AlphaDiana runner. It directly:

1. loads GPQA-Diamond tasks,
2. calls an OpenAI-compatible vLLM `/v1/chat/completions` endpoint,
3. requests `logprobs=True` and `top_logprobs=20`,
4. stores trajectory/artifacts, and
5. writes top-20 probabilities quantized to non-negative Int16 codes plus
   per-token entropy in nats.

Example:

    python scripts/phase9_gpqa_top20_int16_entropy.py \
      --api-base http://127.0.0.1:8011/v1 \
      --model Qwen/Qwen3.5-27B \
      --max-tasks 1
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
from openai import OpenAI

from alphadiana.agent.direct_llm import (
    _extract_answer,
    _extract_reasoning_from_model_extra,
    _split_think_tags,
)
from alphadiana.benchmark.gpqa import GPQADiamondBenchmark

INT16_PROB_SCALE = 32767
TOP_LOGPROBS = 20

SYSTEM_PROMPT = """You are solving expert-level science multiple-choice questions.
Read the question carefully, reason step by step, and choose the single best option.

Your final answer must be on the last line in exactly one of these forms:
$$\\boxed{A}$$
$$\\boxed{B}$$
$$\\boxed{C}$$
$$\\boxed{D}$$
"""


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _strip_raw_logprobs(value: Any) -> Any:
    """Remove provider float logprobs from artifacts; Int16 JSONL is canonical."""
    if isinstance(value, dict):
        return {
            k: _strip_raw_logprobs(v)
            for k, v in value.items()
            if k != "logprobs" and k != "top_logprobs"
        }
    if isinstance(value, list):
        return [_strip_raw_logprobs(v) for v in value]
    return value


def _softmax_from_logprobs(top_logprobs: list[dict]) -> list[float]:
    if not top_logprobs:
        return []
    logps = [float(entry["logprob"]) for entry in top_logprobs]
    max_logp = max(logps)
    weights = [math.exp(lp - max_logp) for lp in logps]
    total = sum(weights)
    if total <= 0.0:
        return []
    return [w / total for w in weights]


def _entropy_nats(probs: Iterable[float]) -> float:
    return -sum(p * math.log(p) for p in probs if p > 0.0)


def _prob_to_i16(prob: float) -> int:
    code = int(round(prob * INT16_PROB_SCALE))
    return max(0, min(INT16_PROB_SCALE, code))


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * p)
    return sorted_values[min(idx, len(sorted_values) - 1)]


def top_logprob_to_int16_record(token_logprob: Any, token_index: int) -> dict:
    """Convert one SDK token-logprob object into compact Int16 probability form."""
    top_entries = [
        {"token": item.token, "logprob": float(item.logprob)}
        for item in (getattr(token_logprob, "top_logprobs", None) or [])[:TOP_LOGPROBS]
    ]
    probs = _softmax_from_logprobs(top_entries)
    return {
        "token_index": token_index,
        "token": getattr(token_logprob, "token", ""),
        "top20": [
            {"token": entry["token"], "prob_i16": _prob_to_i16(prob)}
            for entry, prob in zip(top_entries, probs)
        ],
        "entropy_nats": _entropy_nats(probs),
    }


def entropy_stats(records: list[dict]) -> dict:
    entropies = [float(r.get("entropy_nats") or 0.0) for r in records]
    if not entropies:
        return {"mean": 0.0, "max": 0.0, "p50": 0.0, "p90": 0.0, "n_tokens": 0}
    sorted_e = sorted(entropies)
    return {
        "mean": sum(entropies) / len(entropies),
        "max": max(entropies),
        "p50": _percentile(sorted_e, 0.5),
        "p90": _percentile(sorted_e, 0.9),
        "n_tokens": len(entropies),
    }


def build_messages(problem: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": problem},
    ]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_logprobs_int16(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def call_vllm(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    top_p: float | None,
    max_tokens: int,
) -> tuple[str, str, dict, dict, list[dict]]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "logprobs": True,
        "top_logprobs": TOP_LOGPROBS,
    }
    if top_p is not None:
        request_kwargs["top_p"] = top_p

    response = client.chat.completions.create(**request_kwargs)
    choice = response.choices[0]
    raw_output = choice.message.content or ""
    raw_reasoning = _extract_reasoning_from_model_extra(choice.message)
    if not raw_reasoning and "<think>" in raw_output:
        raw_reasoning, raw_output = _split_think_tags(raw_output)

    logprob_records: list[dict] = []
    choice_logprobs = getattr(choice, "logprobs", None)
    if choice_logprobs is not None and getattr(choice_logprobs, "content", None):
        for idx, tlp in enumerate(choice_logprobs.content):
            logprob_records.append(top_logprob_to_int16_record(tlp, idx))

    usage = _jsonable(response.usage) if getattr(response, "usage", None) else {}
    response_json = _strip_raw_logprobs(_jsonable(response))
    if raw_reasoning:
        response_json.setdefault("alphadiana_extracted_reasoning", raw_reasoning)
    return raw_output, raw_reasoning, usage, response_json, logprob_records


def _score(predicted: str, ground_truth: str) -> dict:
    correct = predicted == ground_truth
    return {
        "correct": correct,
        "score": 1.0 if correct else 0.0,
        "expected": ground_truth,
        "predicted": predicted,
    }


def run_task(
    client: OpenAI,
    task: Any,
    *,
    run_dir: Path,
    run_id: str,
    model: str,
    temperature: float,
    top_p: float | None,
    max_tokens: int,
) -> dict:
    messages = build_messages(task.problem)
    started = time.time()
    raw_output, raw_reasoning, usage, response_json, logprob_records = call_vllm(
        client,
        model=model,
        messages=messages,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )
    wall_time = time.time() - started
    predicted = _extract_answer(raw_output) if raw_output else ""
    if not predicted and raw_reasoning:
        predicted = _extract_answer(raw_reasoning)

    stats = entropy_stats(logprob_records)
    logprobs_rel = f"logprobs_int16/{task.task_id}.jsonl"
    artifact_dir = run_dir / "artifacts" / task.task_id
    trajectory = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task.problem},
        {"role": "assistant", "content": raw_output, **({"thinking": raw_reasoning} if raw_reasoning else {})},
    ]

    _write_json(artifact_dir / "request_messages.json", messages)
    _write_json(artifact_dir / "response.json", response_json)
    (artifact_dir / "raw_output.txt").write_text(raw_output, encoding="utf-8")
    if raw_reasoning:
        (artifact_dir / "reasoning.txt").write_text(raw_reasoning, encoding="utf-8")
    _write_json(run_dir / "trajectories" / f"{task.task_id}.json", trajectory)
    _write_logprobs_int16(run_dir / logprobs_rel, logprob_records)

    score = _score(predicted, str(task.ground_truth))
    record = {
        "task_id": task.task_id,
        "run_id": run_id,
        "problem": task.problem,
        "ground_truth": task.ground_truth,
        "task_metadata": task.metadata,
        "predicted": predicted,
        **score,
        "trajectory": trajectory,
        "raw_output": raw_output,
        "request_messages_path": f"artifacts/{task.task_id}/request_messages.json",
        "response_json_path": f"artifacts/{task.task_id}/response.json",
        "raw_output_path": f"artifacts/{task.task_id}/raw_output.txt",
        "logprobs_int16_path": logprobs_rel,
        "token_entropy_stats": stats,
        "token_usage": usage,
        "wall_time_sec": wall_time,
        "model": model,
        "top_logprobs": TOP_LOGPROBS,
        "int16_probability_scale": INT16_PROB_SCALE,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(run_dir / "tasks" / f"{task.task_id}.json", [record])
    return record


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://127.0.0.1:8011/v1")
    parser.add_argument("--api-key", default="EMPTY")
    parser.add_argument("--model", default="Qwen/Qwen3.5-27B")
    parser.add_argument("--run-id", default="phase9_gpqa_direct_vllm_top20_int16")
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--dataset", default="fingertap/GPQA-Diamond")
    parser.add_argument("--split", default="test")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-tasks", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--max-tokens", type=int, default=32768)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    run_dir = args.output_dir / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    benchmark = GPQADiamondBenchmark()
    tasks = benchmark.load_tasks({
        "dataset": args.dataset,
        "split": args.split,
        "seed": args.seed,
        "max_tasks": args.max_tasks,
    })

    client = OpenAI(
        base_url=args.api_base,
        api_key=args.api_key,
        http_client=httpx.Client(trust_env=False),
    )

    records: list[dict] = []
    jsonl_path = run_dir / f"{args.run_id}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as jsonl:
        for task in tasks:
            record = run_task(
                client,
                task,
                run_dir=run_dir,
                run_id=args.run_id,
                model=args.model,
                temperature=args.temperature,
                top_p=args.top_p,
                max_tokens=args.max_tokens,
            )
            records.append(record)
            jsonl.write(json.dumps(record, ensure_ascii=False) + "\n")
            jsonl.flush()
            print(
                f"{task.task_id}: score={record['score']} "
                f"entropy_tokens={record['token_entropy_stats']['n_tokens']} "
                f"logprobs={record['logprobs_int16_path']}",
                flush=True,
            )

    summary = {
        "run_id": args.run_id,
        "run_dir": str(run_dir),
        "tasks": len(records),
        "correct": sum(1 for r in records if r["correct"]),
        "top_logprobs": TOP_LOGPROBS,
        "int16_probability_scale": INT16_PROB_SCALE,
        "jsonl_path": str(jsonl_path),
    }
    _write_json(run_dir / "summary.json", summary)
    print(f"Wrote {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
