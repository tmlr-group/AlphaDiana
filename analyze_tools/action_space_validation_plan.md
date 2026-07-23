# Six-Action Trajectory Clustering Plan

This note defines the compact action space used to cluster model-generated
trajectory turns and summarizes the validation plan for applying it to the
macro-analysis artifacts described in `analyze_tools/MACRO_ANALYSIS_NOTES.md`.

## Trajectory States

The intended state transition is:

```text
Analyze and Plan
  -> Execute Plan and Tool Calling
  -> Verify Results
  -> Summarize and Final Answer
```

If verification fails, the trajectory returns to analysis and planning, then
re-enters execution and verification. The loop continues until the current
result is verified as acceptable or the trajectory ends with a limitation.

## Action Space

Each model-generated turn receives exactly one primary action label. The action
labels are intentionally coarse, non-overlapping, and tied to trajectory
function rather than surface keywords.

| Action | State | Definition |
|---|---|---|
| Problem Framing | Analyze and Plan | Extracts the goal, givens, constraints, options, and answer format. |
| Plan Formation | Analyze and Plan | Decomposes the task or chooses the next solving route. |
| Plan Execution | Execute Plan | Performs internal solving: reasoning, derivation, comparison, or calculation. |
| Tool Execution | Execute Plan and Tool Calling | Calls a tool or uses returned tool evidence to update the solution. |
| Result Auditing | Verify Results | Checks, diagnoses, corrects, rejects, or confirms the current result. |
| Answer Delivery | Summarize and Final Answer | States the decisive basis and emits the final answer or terminal limitation. |

## Classification Unit

The unit of classification is one assistant-generated turn. Tool-result rows are
not model actions. They are retained as observations only when a following
assistant turn explicitly uses them.

Exclude user prompts, system instructions, scaffold text, and observation-only
rows from action-rate denominators, or mark them with an auxiliary flag.

## Mapping From Earlier Labels

The earlier fine-grained labels in the macro notes map to the six actions as
follows:

| Earlier Label | Six-Action Label |
|---|---|
| `parse_task` | Problem Framing |
| `decompose`, `plan_strategy` | Plan Formation |
| `derive`, `compute`, `compare_options` | Solution Execution |
| `consult_tool`, `integrate_observation` | Tool Grounding |
| `validate_candidate`, `revise`, `detect_error`, `confirm_readiness` | Result Auditing |
| `commit_answer`, `format_answer`, `summarize_basis`, `report_limitation` | Answer Delivery |

## Tie-Break Rules

When one turn contains multiple signals, assign the label by the most
trajectory-specific state transition it performs:

1. Answer Delivery
2. Tool Grounding
3. Result Auditing
4. Solution Execution
5. Plan Formation
6. Problem Framing

Examples:

| Turn Pattern | Label |
|---|---|
| Says "let me check" and then performs arithmetic | Solution Execution |
| Checks option-letter mapping or rejects a prior result | Result Auditing |
| Calls a tool or explicitly uses returned search, shell, or code output | Tool Grounding |
| Emits terminal boxed answer, option letter, numeric value, or limitation | Answer Delivery |

## Validation Plan

The validation question is whether the six-action space can classify all
relevant model-generated turns without systematic ambiguity.

1. Extract assistant-generated turns from each trajectory.
2. Exclude user, system, scaffold, and observation-only rows from the primary denominator.
3. Attach local context to each turn: previous action, next action, whether a tool result is available, whether a candidate result already exists, and whether the turn is terminal.
4. Apply the six-action priority rules.
5. Check state-transition validity.
6. Flag suspicious cases.
7. Manually audit representative correct and wrong trajectories for each harness and benchmark covered by the macro notes.
8. Report action rates with caveats.

The normal path is:

```text
Problem Framing
  -> Plan Formation
  -> Solution Execution / Tool Grounding
  -> Result Auditing
  -> Answer Delivery
```

Failed verification may loop back:

```text
Result Auditing
  -> Problem Framing / Plan Formation
  -> Solution Execution / Tool Grounding
  -> Result Auditing
```

Suspicious cases to flag:

| Suspicious Case | Why It Matters |
|---|---|
| Answer Delivery followed by more solving | Finality boundary may be wrong. |
| Tool Grounding without a tool call or used observation | Tool label may be keyword-driven rather than structural. |
| Result Auditing before any candidate or evidence exists | Verification stage may be misidentified. |
| Repeated low-progress turns | The useful action may be absent, even if surface text matches a label. |

## Expected Outliers

The compact action space should classify useful model-generated content, but
several outlier types should be tracked separately:

| Outlier | Handling |
|---|---|
| Observation-only tool-result rows | Do not classify as model actions. |
| Scaffold or instruction text included in trajectories | Exclude or mark `scaffold_text=true`. |
| Long DirectLLM responses containing several stages | Segment before action clustering. |
| Low-progress loops or repeated reasoning | Mark `low_progress=true`; classify only if state role is clear. |
| HLE keyword false positives | Use trajectory function and context, not keywords alone. |

## Conclusion

The six-action space is suitable for macro trajectory analysis because each
action corresponds to a distinct trajectory function: frame the task, form a
plan, execute internally, ground through tools, audit the result, or deliver the
answer.

It is simpler and less overlapping than the earlier
`plan` / `reason` / `tool_use` / `verify` / `recover` / `answer` set. The main
requirement is to classify assistant turns with trajectory context, not by
keywords alone.
