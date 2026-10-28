# Structural Patterns from AlphaApollo

These examples were imported from AlphaApollo commit
`6d1be0e1fe3b5235b10554b0ab2ad5fef06e8aae`. All `alphaapollo/` paths and
API names below refer to that source repository, not AlphaDiana. They illustrate
patterns, not a required Diana layout or an Apollo runtime dependency. Inspect
the target repository's current owners and contracts before applying a pattern.

## Stable facade over a private implementation package

Use this pattern only when the module has grown several responsibilities and
its import path is a supported public contract or a named consumer requires
compatibility. Existing spelling alone is not such evidence. For migratable
internal paths, move callers, tests, and docs to the canonical owner and remove
the old path instead. The tree below illustrates required compatibility; it is
not the default layout for every split.

```text
alphaapollo/common/execution/
├── workspace.py             # 40-line facade: imports + __all__, nothing else
└── _workspace/
    ├── store.py             # content-addressed artifact storage
    ├── lease.py             # workspace lease and provider lifecycle
    ├── snapshot.py          # deterministic snapshot and restore
    ├── export.py            # completed-run export bundles and retention
    └── verifier.py          # disposable verifier staging
```

Rules that make it work:

- The facade contains only re-exports, `__all__`, and a docstring saying where
  the implementations live and that this module is the supported import surface.
- The private package is named `_<facade>` and sits beside the facade, so the
  ownership is obvious from the directory listing.
- Each implementation module owns one lifecycle or security boundary. Do not
  split one boundary across two modules to hit a line target.
- Implementations move intact. The commit is mechanical; the existing suite is
  the evidence that it was.
- Concrete classes now report a different `__module__`. Search for serialized or
  reflection-based consumers before moving them.

The same shape is applied at `execution/session.py` → `_session/`,
`execution/backends/podman.py` → `_podman/`,
`execution/tools/builtins/files.py` → `_files/`,
`execution/tools/builtins/shell.py` → `_shell/`, and
`trajectory/recorder.py` → `_recorder/`.

### The identity test that pins it

One test per facade, named `test_*_layout.py`, placed beside the other tests for
that area. It asserts the public name *is* the split implementation, so a later
edit cannot leave a second definition behind the facade:

```python
"""Compatibility tests for the split workspace implementation."""

from alphaapollo.common.execution._workspace.store import ArtifactStore as SplitArtifactStore
from alphaapollo.common.execution.workspace import ArtifactStore


def test_workspace_facade_reexports_split_implementations() -> None:
    assert ArtifactStore is SplitArtifactStore
```

### The README entry that makes it findable

Record every facade in the owning package's README under a module map, stating
which module is the facade and what each internal module owns. See the
`## Module map` section of `alphaapollo/common/README.md`.

## Domain layering inside a shared package

`alphaapollo/common/grader/` separates what a layer is allowed to know:

- `text.py` is domain-free: find the `\boxed` marker, resolve which span of the
  output to read, strip presentation. It knows nothing about what an answer means.
- `math/` holds everything that assumes an answer is a number or an expression:
  LaTeX normalization, extraction, equivalence, and the graders themselves.
- `registry.py` resolves the `grader_id` that each prepared task already carries,
  so a dataset declares how it is scored and an unknown grader fails before any
  cell runs.
- A `Grader` owns both halves of its domain—`extract` and `grade`—so a caller
  dispatches on `grader_id` instead of hard-coding one benchmark's extractor.

`declared.py` stays a separate extractor on purpose: it scans the whole text for
answer markers because voting groups branches with it, while `math/extract`
confines its fallbacks to the tail. They share the scanning primitive and nothing
else, and the reason is recorded at the code. Merging them would change how
branches group, which has to be its own change.

## Package layout documented at the owner

`alphaapollo/workflows/README.md` is the reference form: a layout block with one
line per module saying what it owns, followed by the main dependency paths and
the invariant that matters (public input reaches the executor; private gold joins
only after execution returns). A reader answers "where does this happen" and
"what must not flow where" without opening a file.

## Registry-shaped extension

`alphaapollo/reasoning/runtime/external/` shows the shape to reach for when a
concept genuinely has several vendors:

```text
external/
├── __init__.py       # the registry the composition root talks to
├── cli.py            # spawning, timeouts, process groups — shared by all agents
├── smoke.py          # the live comparison harness
├── agents/           # one module per vendor, each an ExternalAgentSession
└── bridge/           # serving our own tools to an agent that would use its own
```

The shared skeleton—argument validation, spawning with the prompt on stdin,
timeout handling, exit-code folding—lives once in `cli.py`. A vendor module
supplies only what genuinely differs: its argument vector and its stream parser.
Adding a vendor is one file plus one registry entry.

`bridge/` also draws a dependency line the code already had: only `mcp_server.py`
needs the optional `mcp` extra, so composition can name and validate a bridge
without it installed.

## Honest projection of a foreign source

When adapting an external tool's output into a canonical record, the divergences
are represented, not smoothed:

- A vendor with no separate system-prompt channel gets its role text prepended,
  and the outcome records `system_prompt_inlined`, so no reader assumes it
  received a system message.
- A vendor that reports an action and its result as one record has it split into
  a tool call and a tool result, because a trajectory needs the request and the
  observation separately.
- Non-fatal parse errors from the vendor's own config are counted in metadata
  rather than becoming observations, which would invent turns that never happened.
- Duplicate announcements of one event are de-duplicated by id, first
  announcement wins, so the behavior survives a vendor emitting only one of them.
- Text from an external agent carries `trainable=false` and null token fields by
  construction, so it cannot enter an on-policy batch by oversight.

## Reserved versus dead fields

Three states, each with a required treatment:

| State | Treatment |
| --- | --- |
| Written and read | Ordinary field. |
| Written, unread, no named future | Remove it, and bump `ROW_SCHEMA_VERSION` so no earlier build is reused against the new shape. |
| Written, unread, named future or forensic value | Keep it, state at the definition and in the README who would fill and read it or how it is used by hand, and pin both halves with a test so the next sweep of unread fields does not delete it. |

`answer_space` and `grader_params` were removed under the second rule.
`env_payload`, `reference_solution`, and `raw_digest` are kept under the third:
`raw_digest`'s documentation states plainly that it is never verified—rebuild and
diff the column to find rows an upstream dataset edited silently—next to the
contrast that makes it legible, that the manifest's file digests *are* rehashed
and enforced on every read.

## Quarantine instead of a category

`examples/` groups by who runs a thing. Material reachable only from a docs page
lives in `examples/_unowned/`, whose README states what must happen before a file
leaves: run it, rename it out, add a test, update the doc. The underscore marks a
holding area rather than a category. Anything with no reference at all is deleted
instead of quarantined.

## Where a configuration is refused

Refusals happen as early as the information exists, and each names the conflict:

- The composition root cross-validates runtime and Environment types, so
  declaring an Environment that every referenced runtime ignores is an error
  rather than a silently unused block.
- `parse_run_config` refuses in-loop grading (`environment.options.grader_id`)
  whenever the config carries a `scoring` block, because evaluation's guarantee
  is that only the public projection reaches the process running the model.
  Accepting the key and quietly withholding gold would read as honoured.
- Granting tools *and* declaring the bridge server by hand is refused rather than
  resolved by precedence.
- A capability probe runs where the grants are finally known, so an agent that
  cannot serve the tools it was granted is a `ConfigError` instead of a paid run
  whose tools silently never worked.
- Two schemas once both claimed the `evaluation:` key—one CLI read it as the
  prepared build that owns the private gold, the other popped it as a sweep
  policy—so the two entry points scored against different gold. The fix separated
  the keys and raised an error naming the new home for the moved one, rather than
  dropping it. Prefer refusing a key you no longer own to accepting it silently.
