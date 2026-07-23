# Action Frequency Analysis — Discussion

## Finding: Tool-use harnesses induce a structural collapse from Planning to
## Tool Grounding, and agent failures are characterised by a Verification-Reasoning
## loop that prevents Finalization.

### Evidence from the count-rel tables

**1. Planning collapses under tool-use harnesses.**

Across all three benchmarks and both models, the share of Plan Formation actions
in agent harnesses drops substantially relative to the DirectLLM baseline.
For example, on HLE with Qwen3.5-27B, Plan Formation falls from 15.9% (DirectLLM
success) to 6.6% (OpenClaw success) and 7.5% (OpenCode success) — a reduction of
roughly 9 percentage points, or more than half of the planning budget.
The effect is even larger in failure trajectories: HLE Qwen DirectLLM failure
allocates 27.2% of actions to planning, while OpenClaw failure allocates only
5.8% — a 21.4 pp collapse.

This is not merely a substitution effect. The agent is not replacing planning with
tool use one-for-one; rather, tool availability appears to suppress explicit
decomposition behaviour. The model offloads cognitive structure onto the
environment, relying on tool feedback to guide subsequent reasoning rather than
constructing a plan upfront.

**2. Tool Grounding cannibalises Reasoning on failure.**

When tool-use agents fail, Tool Grounding does not simply replace Planning —
it eats into Solution Execution as well. On HLE with Gemma4-31B OpenClaw, the
success trajectory allocates 57.0% to Reasoning and 24.5% to Tool Use.
The failure trajectory inverts this: 44.1% Reasoning, 39.5% Tool Use — a 41.8 pp
collapse in Reasoning offset almost exactly by a 39.5 pp surge in Tool Use.

The same pattern holds on AIME with Gemma4-31B: OpenClaw failure allocates only
22.6% to Reasoning (vs. 77.7% on success) while Tool Use consumes 61.3%
(vs. 9.7% on success). The model enters a tool-calling spiral — invoking
bash, python, or search repeatedly without converging.

**3. The Verification-Reasoning loop traps failing agents.**

ZeroClaw — which reasons without tools — provides the cleanest evidence for a
Verification trap. On HLE with Gemma4-31B ZeroClaw, Result Auditing jumps from
2.6% (DirectLLM success) to 23.5% (ZeroClaw success) and 17.8% (ZeroClaw failure).
On GPQA with Gemma4-31B ZeroClaw failure, Auditing reaches 26.3%.

Critically, these Verification actions do not lead to Finalization. The chord
diagrams confirm that Verification→Reasoning and Reasoning→Verification form
the dominant bidirectional chord pair in failure panels — the model checks,
reasons again, checks again, and never delivers an answer. The trajectory is
stuck in an audit loop.

**4. Gemma4-31B is more susceptible to tool entanglement than Qwen3.5-27B.**

Controlling for benchmark and harness, Gemma4-31B consistently shows larger
shifts toward Tool Use and Verification on failure than Qwen3.5-27B.
For instance, on GPQA OpenClaw success, both models allocate roughly 6-9% to
Tool Use. But on failure, Qwen allocates 0.2% to Tool Use while Gemma allocates
28.6%. This suggests that Gemma4-31B has a stronger tendency to re-invoke tools
when initial tool output is unhelpful, whereas Qwen3.5-27B abandons the tool
strategy more readily and falls back on internal reasoning.

### Summary table

| Phenomenon | Evidence | Magnitude |
|---|---|---|
| Planning collapse | Plan Formation share drops by 9-21 pp in agents vs DirectLLM | 2-3× reduction |
| Tool-Reasoning inversion | Tool Use replaces Reasoning on agent failure | up to 42 pp shift (Gemma HLE OpenClaw) |
| Verification loop | Verification↔Reasoning is the dominant chord pair in failure | Audit reaches 17-26% in ZeroClaw failures |
| Model asymmetry | Gemma more prone to tool entanglement than Qwen | Gemma Tool Use 28.6% vs Qwen 0.2% on GPQA OpenClaw failure |

### Implication for harness design

The data suggests that **unstructured tool access without explicit planning
guardrails produces a bimodal trajectory dynamic**: either the tools work and the
agent proceeds efficiently (low Planning, low Verification, high Finalization),
or the tools produce misleading output and the agent enters a costly
tool-verification spiral. Interventions that enforce a planning step before tool
invocation, or that limit consecutive tool calls without intermediate verification
that converges toward Finalization, may reduce the failure-mode severity.
