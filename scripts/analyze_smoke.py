"""Export persisted smoke evidence, optionally including real LLM annotations."""

import argparse
from contextlib import ExitStack
import os
from pathlib import Path

import alphadiana as ad
from alphadiana.analysis.action_annotations import ActionAnnotations
from alphadiana.analysis.analyzer import ExecutionMetrics
from alphadiana.analysis.entropy_analysis import EntropyAnalysis
from alphadiana.analysis.failure_annotations import FailureAnnotations
from alphadiana.analysis.process_entropy import RequestEntropy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path, nargs="+")
    parser.add_argument("--provider", choices=["qwen", "hkbu"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Make billable LLM action/failure annotation calls",
    )
    args = parser.parse_args()
    with ExitStack() as stack:
        plugins = [ExecutionMetrics(), RequestEntropy(), EntropyAnalysis()]
        if args.judge:
            options = dict(
                model="gpt-6-luna" if args.provider == "hkbu" else "Qwen/Qwen3.5-27B",
                base_url=os.environ["AD_ACCEPTANCE_API_BASE"],
                api_key=os.environ["AD_ACCEPTANCE_API_KEY"],
            )
            if args.provider == "hkbu":
                options.update(
                    temperature=None, token_limit_parameter="max_completion_tokens"
                )
            else:
                options["extra_body"] = {
                    "chat_template_kwargs": {"enable_thinking": False}
                }
            cache = args.output.parent / (args.output.name + "-annotations")
            for cls, name in [
                (ActionAnnotations, "actions"),
                (FailureAnnotations, "failures"),
            ]:
                plugins.append(
                    stack.enter_context(cls(cache_dir=cache / name, **options))
                )
        ad.analyzer(args.artifacts, plugins=plugins).export(
            args.output, formats=["pdf", "svg", "png"]
        )
    print(args.output)


if __name__ == "__main__":
    main()
