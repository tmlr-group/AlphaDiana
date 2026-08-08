---
sidebar_position: 3
---

# Registries

AlphaDiana has four class registries: benchmarks, agents, sandboxes, and scorers. Configuration selects a key; `Runner.setup()` imports built-in modules, resolves each key, instantiates the class, and calls `setup()`.

Registration is import-triggered, not discovered by scanning the filesystem. A new implementation normally requires no orchestration-logic change, but its module must be imported by the runner so registration executes.

## Live inventory

The tables below reflect the registrations imported by `Runner.setup()` in the current source tree.

### Benchmarks

| Key | Purpose |
| --- | --- |
| `aime` | AIME math tasks |
| `custom` | User-provided task data |
| `gpqa_diamond` | GPQA Diamond |
| `hle` | Humanity's Last Exam |
| `imo_answerbench` | IMO AnswerBench |
| `mmmu_pro` | MMMU-Pro multimodal tasks |
| `swe_bench` | SWE-bench |
| `swebench_pro_os` | SWE-bench Pro open-source split |
| `terminal_bench2` | TerminalBench 2 |

### Agents

The first four are generic harness families. The remaining keys are benchmark-specific adapters and should not be conflated with additional general-purpose harness families.

| Key | Scope |
| --- | --- |
| `direct_llm` | Generic direct provider baseline |
| `openclaw` | Generic OpenClaw gateway and local-memory harness |
| `opencode` | Generic OpenCode CLI harness |
| `zeroclaw` | Generic ZeroClaw CLI harness |
| `swebench_docker` | SWE-bench task-container adapter |
| `terminal_bench2_docker` | TerminalBench 2 baseline adapter |
| `terminal_bench2_openclaw` | TerminalBench 2 OpenClaw adapter |
| `terminal_bench2_opencode` | TerminalBench 2 OpenCode adapter |
| `terminal_bench2_zeroclaw` | TerminalBench 2 ZeroClaw adapter |

### Sandboxes

| Key | Scope |
| --- | --- |
| `local` | Restricted local execution; not a general substitute for shell-heavy harness commands |
| `podman` | Rootless Podman session |
| `rock` | Remote/container sandbox through ROCK |
| `swebench_container` | Task-bound SWE-bench container |

### Scorers

| Key | Scope |
| --- | --- |
| `exact_match` | Normalized exact comparison |
| `numeric` | Numeric answer comparison |
| `math_verify` | Mathematical equivalence verification |
| `llm_judge` | Model-based judging |
| `imo_verify` | Required verifier for `imo_answerbench` |
| `swe_bench` | Official SWE-bench evaluation adapter |
| `swebench_pro` | SWE-bench Pro evaluator |
| `terminal_bench2` | Verifier reward scorer |

## Registration forms

Agents and many benchmarks call their registry directly:

```python
AgentRegistry.register("my_agent", MyAgent)
```

Sandboxes and scorers commonly use decorators:

```python
@register_sandbox("my_sandbox")
class MySandbox(Sandbox):
    ...

@register_scorer("my_scorer")
class MyScorer(Scorer):
    ...
```

Both forms mutate a process-local registry. Duplicate names raise instead of silently replacing an implementation.

## Adding a component

1. Implement the appropriate base class.
2. Register a stable, lowercase config key in that module.
3. Add the module import to the relevant import block in `Runner.setup()`.
4. Add validation for any cross-component constraint.
5. Add focused registry/setup tests and one real smoke before claiming runtime support.
6. Document the new key in the relevant inventory and runbook.

Keeping the import list explicit makes supported built-ins reviewable, but it also means a class can exist in the repository without being selectable until the import is added.

## Related pages

- [Engine & Runner](./engine-and-runner)
- [Sandboxes & Isolation](./sandboxes)
- [Scoring & Results](./scoring-and-results)
- [Harnesses Overview](../harnesses/)
