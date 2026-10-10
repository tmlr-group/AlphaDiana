"""Exercise the public API using DirectLLM against a local scripted HTTP fixture.

Run with pytest; every result and plot is written inside its temporary directory.
No real model, benchmark download, semantic judge or container is used.
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import alphadiana as ad
from alphadiana.artifacts import Artifacts
from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import BenchmarkRegistry


class DemoTasks(Benchmark):
    def load_tasks(self, config):
        rows = json.loads(Path(config["source_file"]).read_text(encoding="utf-8"))
        return [BenchmarkTask(**row) for row in rows]


class ScriptedProvider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions"
        assert request["model"] == "local-demo-fixture"
        assert not request.get("stream")
        prompt = request["messages"][-1]["content"]
        answer = self.server.answers[prompt]
        self.server.calls.append({"model": request["model"], "prompt": prompt})
        payload = {
            "id": f"fixture-{len(self.server.calls)}",
            "object": "chat.completion",
            "created": 0,
            "model": request["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": answer},
                    "logprobs": {
                        "content": [
                            {
                                "token": answer,
                                "logprob": -0.1,
                                "top_logprobs": [
                                    {"token": answer, "logprob": -0.1},
                                    {"token": "other", "logprob": -2.5},
                                ],
                            }
                        ]
                    },
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        }
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _run_pipeline(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    dataset_path = output / "fixture-tasks.json"
    dataset_path.write_text(
        json.dumps(
            [
                {"task_id": "add", "problem": "What is 1 + 1?", "ground_truth": "2"},
                {
                    "task_id": "multiply",
                    "problem": "What is 3 * 3?",
                    "ground_truth": "9",
                },
                {
                    "task_id": "subtract",
                    "problem": "What is 5 - 2?",
                    "ground_truth": "3",
                },
            ]
        ),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedProvider)
    server.answers = {
        "What is 1 + 1?": "2",
        "What is 3 * 3?": "9",
        "What is 5 - 2?": "4",
    }
    server.calls = []
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        agent = ad.agent(
            "direct_llm",
            version="1.0",
            model="local-demo-fixture",
            api_base=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="local-fixture",
            stream=False,
            max_tokens=16,
            capture_logprobs=True,
            request_timeout=5,
            max_retries=0,
        )
        data = ad.dataset("local_demo_tasks", source_file=str(dataset_path))
        scorer = ad.scorer.create("numeric")
        options = {
            "output_dir": str(output / "results"),
            "run_id": "demo",
            "strict_report": True,
            "metrics": [{"name": "answer_length", "config": {"source": "answer"}}],
        }
        first = ad.runner(**options).compute(agent=agent, data=data, scorer=scorer)
        assert len(server.calls) == 3
        before = first.path.read_bytes()
        original_rows = first.records()
        assert sum(row["correct"] for row in original_rows) == 2
        assert all(row["evaluation_metrics"]["answer_length"]["value"] == 1 for row in original_rows)
        old_files = {
            first.file(row, key): hashlib.sha256(
                first.file(row, key).read_bytes()
            ).hexdigest()
            for row in original_rows
            for key in (
                "normalized_trace",
                "response_json",
                "logprobs_float",
                "logprobs_int16",
            )
        }
        reopened = Artifacts(first.path)
        rescored = reopened.rescore(
            scorer, output=output / "results/demo-rescored.jsonl"
        )
        assert reopened.path.read_bytes() == before
        assert len(server.calls) == 3, "Rescoring unexpectedly called the solver"
        assert sum(row["correct"] for row in rescored.records()) == 2
        server.answers["What is 5 - 2?"] = "3"
        rerun = ad.runner(**options, redo_all=True).compute(
            agent=agent, data=data, scorer=scorer
        )
        assert len(server.calls) == 6
        assert len(rerun.records()) == 3 and len(rerun.records(latest=False)) == 6
        assert all(row["correct"] for row in rerun.records())
        assert all(
            hashlib.sha256(path.read_bytes()).hexdigest() == digest
            for path, digest in old_files.items()
        )
        report = ad.analyzer([rescored, rerun])
        exported = report.export(output / "figures")
        metrics = json.loads(
            (output / "figures/report.json").read_text(encoding="utf-8")
        )
        performance = metrics["analyses"]["execution"]["tables"]["performance"]
        assert [row["accuracy"] for row in performance] == [2 / 3, 1.0]
        for suffix in ("pdf", "svg", "png"):
            assert (output / f"figures/execution-accuracy.{suffix}").is_file()
        receipt = {
            "kind": "scripted local HTTP fixture; not a real model evaluation",
            "synthetic_usage_and_logprobs": True,
            "real_components": [
                "DirectLLMAgent",
                "Runner",
                "NumericScorer",
                "ResultStore",
                "Artifacts",
                "Analyzer",
            ],
            "initial_correct": 2,
            "tasks_per_execution": 3,
            "rerun_correct": 3,
            "solver_http_calls": len(server.calls),
            "rescore_solver_calls": 0,
            "retained_attempts": 6,
            "retained_original_files": len(old_files),
            "original_files_unchanged": True,
            "performance": performance,
            "exported_files": exported["files"],
            "not_validated": [
                "real Qwen",
                "seven benchmark matrix",
                "native containers",
                "SWE/TB2 verifier replay",
                "real semantic judge",
            ],
        }
        (output / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_direct_llm_pipeline_preserves_evidence_and_exports(tmp_path, monkeypatch):
    monkeypatch.setitem(BenchmarkRegistry._registry, "local_demo_tasks", DemoTasks)
    _run_pipeline(tmp_path / "pipeline-demo")
