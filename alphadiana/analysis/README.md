# Saved-run analysis

Use `ad.analyzer(...)` to compose measurements and export their results.

| Owner | Job |
| --- | --- |
| `pipeline.py` | Compose Analyzer plugins, validate their output and manage judge lifetimes |
| `measurements.py` | Define measurement/figure contracts; compute execution resources and token/request entropy |
| `export.py` | Project canonical sample statistics into run reports; export declared tables and figures |
| `judge_client.py` | Resolve model settings, call the judge and cache validated responses |
| `failure_annotations.py` | Annotate failure evidence with the failure taxonomy |
| `actions/llm_annotations.py` | Segment and label full saved trajectories with an LLM |
| `actions/protocol.py`, `actions/action_prompt.md` | Share action labels and the explicit fixed-target audit contract |
| `actions/keyword_baseline.py` | Run the historical keyword classification method explicitly |
| `actions/features.py` | Extract record features and cluster their views |
| `actions/trajectories.py` | Compute action-sequence motifs/outcomes and select representative cases |
| `actions/action_space_report.py` | Export action-space views with their explicit data-view labels |
| `actions/trajectory_report.py` | Export trajectory discussion and LaTeX |
| `insights/` | Assemble comparison corpora, claims, case anchors and insight reports |
| `scripts/` | Published `python -m` analysis entry points |

Persisted records, traces, score status and latest-sample statistics live in `alphadiana/results/`. Raw/Int16 logprob conversion remains in `alphadiana/results/logprobs.py`, shared by execution and measurement.

Default action annotations use LLM segmentation of the full trajectory. Fixed-target audit and historical custom spans require explicit protocols. Keyword classification never substitutes for an unsuccessful LLM annotation.

## Import migration

Public `ad.runner(...)`, `ad.analyzer(...)` and `Artifacts` behavior remains. Package-level documented analysis functions load their actual owner lazily. The former internal forwarding files are removed; migrate deep imports:

| Previous path | Current owner |
| --- | --- |
| `analyzer` | `pipeline` for Analyzer/TableAnalysis; `measurements` for plugins and contracts |
| `entropy`, `entropy_analysis`, `resource_usage` | `measurements` |
| `report`, `run_summary`, `figures` | `export` for ReportGenerator/RunSummary/rendering; `results.statistics` for RunStatistics/reliability/selection |
| `result_reader`, `io/*` | `results.reader`, `results.store`, `results.trace`, `results.status` |
| `logprobs`, `io/logprob_artifacts` | `alphadiana.results.logprobs` |
| `action_annotations`, `annotations/actions`, `annotations/segmentation`, `legacy/character_spans` | `actions.llm_annotations` |
| `annotations/failures`, `annotations/judge` | `failure_annotations`, `judge_client` |
| `action_events`, `actions/events` | `actions.keyword_baseline` |
| `action_report`, `reports`, `actions/report` | `actions.action_space_report` |
| `actions/clustering` | `actions.features` |
| `actions/metrics`, `actions/cases` | `actions.trajectories` |
| `actions/trajectory_reports` | `actions.trajectory_report` |
| `insights/measurements` | `insights.claims` |
| `action_protocol`, `action_prompt.md` | `actions/protocol`, `actions/action_prompt.md` |
| `paper_archive.PaperArchive` | Source-checkout operator `scripts.paper_analysis.load_figure_tables.PaperFigureTables` |

The paper input loader preserves its historical output identity `paper_archive` and schema. It is used explicitly from a source checkout, outside the installed runtime package. Archived inputs and figures stay unchanged.

Human audits created after this migration hash the relocated protocol files. Existing audits retain their original source revision and hashes.

## Report language

Offline report writers generate English reports. Behavior insight exports contain one Markdown report, `behavior_insights.md`. Trajectory exports contain `gpqa_trajectory_appendix.tex` and `gpqa_trajectory_discussion.md`; the latter uses the `discussion` output key and `render_trajectory_discussion` public renderer.

The former `render_behavior_insights_zh` and `render_chinese_discussion` exports are removed. Use `render_behavior_insights_markdown` and `render_trajectory_discussion`, respectively. Consumers of `markdown_zh` or `chinese_discussion` output keys should use `markdown` or `discussion`. New exports do not write `*_zh.md` reports.

Report and corpus helpers use their purpose as the API name: `write_behavior_insight_outputs`, `write_trajectory_outputs`, and `load_insight_corpus_specs`. These replace `write_phase15_outputs`, `write_phase14_outputs`, and `load_phase15_corpus_specs`. The output filename map is `BEHAVIOR_INSIGHT_OUTPUT_FILENAMES`. Historical result directories and environment variable names remain valid.
