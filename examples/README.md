# Fig. 4 API demo

`fig4_demo.py` shows typed composition, shared execution, durable artifact reopening,
structured analysis and PDF/SVG/PNG export. It uses Direct on AIME to avoid requiring
a container runtime for a first API call. Direct has no tools; post-tool entropy is
inapplicable. Native agent configurations are documented separately in
[`docs/getting-started/python-api.md`](../docs/getting-started/python-api.md).

From a checkout containing this example, install the package into your own environment:

```bash
python -m pip install -e '.[agents,benchmarks,analysis]'
```

## Run a new evaluation

Point to a running OpenAI-compatible model server (the demo does not start one):

```bash
export OPENAI_BASE_URL=http://localhost:18960/v1
export OPENAI_API_KEY=your-key
python examples/fig4_demo.py --model Qwen/Qwen3.5-27B --tasks 3
```

The demo loads the first three AIME26 tasks through its dataset adapter and produces
one sample per task. The first run may download the dataset. The server must offer
the requested model. Add `--logprobs` only if it supports top-K probability output;
without it, entropy panels may be skipped explicitly. Generation is capped at 8,192
output tokens per task; change this with `--max-tokens`. This convenience demo is
not a rerun of the pinned acceptance campaign or its full generation configuration.

The core composition inside the script is:

```python
data = ad.dataset("MathArena/aime_2026", benchmark="aime", split="train", max_tasks=3)
agent = ad.agent("direct_llm", model=model, api_base=base_url, api_key=api_key)
scorer = ad.scorer.create("numeric", tolerance=1e-6)
runner = ad.runner(output_dir="results", run_id="example", num_samples=1)
artifacts = runner.compute(agent=agent, data=data, scorer=scorer)
ad.analyzer(artifacts).export("figures/example")
```

Factories are directly callable on the implementation branch in PR #98; this does
not mean the changes have merged into `main` or appeared in a published release.
The root scorer remains a package, hence `ad.scorer.create` rather than `ad.scorer(...)`.

## Inspect an existing run without a model

```bash
python examples/fig4_demo.py --artifacts /path/to/run.jsonl
```

On the validation machine, use the already available environment and actual saved run:

```bash
cd /data2/weikai/home_moved/projects/alphadiana_issue96_delivery
source /data2/weikai/home_moved/projects/alphadiana_issue96_api/.venv/bin/activate
PYTHONPATH=. python examples/fig4_demo.py \
  --artifacts /data0/weikaihuang/issue96-validation/final-f719bbc3/results/issue96_accept_aime2026_directllm.jsonl \
  --output /data0/weikaihuang/issue96-validation/my-fig4-demo
```

Use a new `--output` directory each time, or omit it for a unique directory under
`results/`. Keep the original run's sidecars next to its JSONL. Offline mode never
calls a model and does not rescore or change the original evidence.

Output includes console accuracy/usage/timing, `figures/report.json` (tables and
coverage), `figures/export.json` (files and skips), plotted-data JSON, and PDF/SVG/PNG.
A nonempty, fully scored run is required; an incorrect answer is a valid score,
while failed/missing records cause the demo to fail with the evidence retained.

Validation: actual three-task saved AIME run reopened successfully, six figures /
18 image files exported, with post-tool entropy explicitly skipped. This demo
smoke test reused existing model evidence; it did not execute new model inference.
