"""Run AIME through the Fig. 4 API, or inspect an existing run without a model.

Install: python -m pip install -e '.[agents,benchmarks,analysis]'
Live: python examples/fig4_demo.py --base-url http://localhost:18960/v1
Offline: python examples/fig4_demo.py --artifacts /path/to/run.jsonl
"""

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

import alphadiana as ad
from alphadiana.artifacts import Artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts", type=Path, help="Reopen a saved run; no model calls"
    )
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL"))
    parser.add_argument("--model", default="Qwen/Qwen3.5-27B")
    parser.add_argument(
        "--tasks", type=int, default=3, help="AIME questions, one sample each"
    )
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument(
        "--logprobs",
        action="store_true",
        help="Request top-K probabilities if supported",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="New directory; defaults to results/fig4-<unique ID>",
    )
    args = parser.parse_args()
    if args.tasks < 1 or args.max_tokens < 1:
        parser.error("--tasks and --max-tokens must be positive")
    if not args.artifacts and not args.base_url:
        parser.error(
            "Live execution requires --base-url or OPENAI_BASE_URL; use --artifacts for offline analysis"
        )
    output = (args.output or Path("results") / f"fig4-{uuid4().hex[:12]}").resolve()
    output.mkdir(parents=True, exist_ok=False)

    if args.artifacts:
        artifacts = Artifacts(args.artifacts.resolve())
        print("Reopening saved evidence; no new model execution.")
    else:
        # The composition matches Fig. 4; the direct harness needs no sandbox.
        data = ad.dataset(
            "MathArena/aime_2026", benchmark="aime", split="train", max_tasks=args.tasks
        )
        agent = ad.agent(
            "direct_llm",
            version="1.0",
            model=args.model,
            api_base=args.base_url,
            api_key=os.getenv("OPENAI_API_KEY", "EMPTY"),
            temperature=0.0,
            max_tokens=args.max_tokens,
            capture_logprobs=args.logprobs,
            top_logprobs=20,
            system_prompt=r"Solve carefully. End with the final integer in $$\boxed{answer}$$.",
        )
        scorer = ad.scorer.create("numeric", tolerance=1e-6)
        runner = ad.runner(
            run_id="aime-demo",
            output_dir=str(output / "runs"),
            num_samples=1,
            max_concurrent=1,
            task_retries=0,
            strict_report=True,
        )
        artifacts = runner.compute(agent=agent, data=data, scorer=scorer)

    # Reopening is the same whether compute() ran here or in another process.
    saved = Artifacts(artifacts.path)
    rows = saved.records()
    valid = sum(row.get("score_status") == "valid_scored" for row in rows)
    print(f"Artifacts: {saved.path}")
    print(f"Valid scored records: {valid}/{len(rows)} (valid does not mean correct)")
    if not rows or valid != len(rows):
        raise RuntimeError("Incomplete/failed run; inspect the retained artifacts")

    report = ad.analyzer(saved)
    exported = report.export(output / "figures", formats=("pdf", "svg", "png"))
    measured = json.loads((output / "figures" / "report.json").read_text())
    print(
        json.dumps(measured["analyses"]["execution"]["tables"]["performance"], indent=2)
    )
    print(f"Figures and plotted data: {output / 'figures'}")
    print(f"Exported files: {len(exported['files'])}")
    print(f"Inapplicable or missing-data panels: {json.dumps(exported['skipped'])}")
    print(
        "Coverage and provenance: figures/report.json; output inventory: figures/export.json"
    )


if __name__ == "__main__":
    main()
