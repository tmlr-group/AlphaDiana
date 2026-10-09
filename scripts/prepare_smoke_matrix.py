"""Prepare three pinned tasks per reasoning benchmark for either provider."""

import argparse
import copy
import json
from pathlib import Path
import shutil

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["qwen", "hkbu"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use a new or empty configuration directory")
    args.output.mkdir(parents=True, exist_ok=True)
    original = Path(__file__).resolve().parents[1] / "configs/acceptance/api"
    manifest = json.loads((original / "manifest.json").read_text())
    model = "Qwen/Qwen3.5-27B" if args.provider == "qwen" else "gpt-6-luna"
    manifest["model"] = model
    # Native/container cells remain in the full acceptance matrix. This entry
    # verifies the public model -> scorer -> artifacts -> analyzer path first.
    manifest["cells"] = [c for c in manifest["cells"] if c["id"].endswith("_directllm")]
    manifest["expected_cells"] = len(manifest["cells"])
    for cell in manifest["cells"]:
        config = yaml.safe_load((original / cell["config"]).read_text())
        aime = cell["id"].startswith("aime2026_")
        config.update(
            run_id=f"smoke_{args.provider}_{cell['id']}",
            num_samples=4 if aime else 1,
            max_concurrent=4,
        )
        cell["num_samples"] = config["num_samples"]
        ac = config["agent"]["config"]
        ac.update(model=model, request_timeout=10800, stream_total_timeout=10800)
        if args.provider == "qwen":
            ac.update(
                max_tokens=131072,
                temperature=0.6 if aime else 0.0,
                top_p=0.95,
                enable_thinking=True,
                extra_body={
                    "presence_penalty": 1.5,
                    "top_k": -1,
                    "min_p": 0.0,
                    "repetition_penalty": 1.0,
                    "chat_template_kwargs": {"enable_thinking": True},
                },
            )
            differences = "Qwen3.5-27B BF16, no quantization, 262144 context; AIME T=.6/K=4, others T=0/K=1."
        else:
            ac.pop("max_tokens", None)
            ac.pop("enable_thinking", None)
            ac.pop("top_logprobs", None)
            ac.update(
                max_completion_tokens=131072,
                temperature=None,
                top_p=None,
                extra_body={},
                capture_logprobs=False,
            )
            differences = "HKBU rejects temperature, presence_penalty and logprobs. Sampling uses provider defaults; top_p and Qwen-specific fields omitted. Context, dtype and hidden reasoning are provider-controlled."
        config["metadata"].update(
            model_family=model,
            generation_protocol=differences,
            campaign_scope="Three pinned tasks per dataset; functional smoke test, not full benchmark results.",
        )
        (args.output / cell["config"]).write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf8"
        )
    shutil.copyfile(original / "fixtures.json", args.output / "fixtures.json")
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf8"
    )
    print(args.output / "manifest.json")


if __name__ == "__main__":
    main()
