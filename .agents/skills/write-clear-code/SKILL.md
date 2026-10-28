---
name: write-clear-code
description: Guides AlphaDiana implementation and refactoring toward code, files, configuration, and control flow that are simple, cohesive, and easy for humans and coding agents to navigate, and toward changes that fail loudly instead of publishing a broken run as a result. Use when creating or reorganizing modules, splitting an oversized file, centralizing shared configuration or resources, extracting or reusing logic, removing dead code or unread fields, choosing file or directory boundaries, adding or bounding a dependency, or simplifying an existing implementation without changing its contract.
---

# Write Clear Code

When reused from a parent repository such as AlphaHebe, target the user's named
repository and its guidance, base/head, commands, and contracts. Skill location
does not select the GitHub destination or authorize changes to dependencies.

Implement the requested behavior with the smallest design that explains the current requirements. Optimize for correctness first, then for a reader's ability to locate ownership, follow data flow, and change the code safely.

Read [references/patterns.md](references/patterns.md) for the repository's established structural patterns and worked examples.

## 1. Establish the contract

1. Read `AGENTS.md` and any nearer guidance.
2. Identify the public APIs, schemas, configuration sources, side effects, producers, consumers, subclasses, and tests that constrain the change.
3. State the requested outcome and explicit non-goals. Preserve behavior that is outside the request.
4. Search for the canonical owner of the concept before adding a new implementation.

Do not treat similar spelling as equivalent behavior. Reuse code only when inputs, outputs, failure behavior, lifecycle, and ownership match.

Before moving shared behavior or resources, inventory every producer and consumer. Centralization is incomplete while a consumer retains a parallel implementation or override path.

For every field, flag, or option you add or keep, name who reads it. A producer with no consumer is not a contract.

## 2. Apply Occam's razor

Choose the least powerful structure that keeps the behavior clear. Escalate only when the simpler level creates concrete duplication, coupling, or test difficulty:

1. edit existing straight-line code;
2. extract a local private helper;
3. reuse an existing canonical abstraction;
4. add a shared abstraction;
5. add a file, directory, registry, configuration layer, or framework.

For every escalation, identify the present requirement it solves. Do not add extension points, wrappers, factories, aliases, generic utilities, registries, or configuration for hypothetical future use. A lookup table for what is presently one entry buys a loader, a validator, and a packaging entry in exchange for a few lines.

Prefer a little obvious repetition over an abstraction that merges different concepts. Remove repetition when one implementation can remain the clear canonical owner without mode flags, branching adapters, or leaky parameters.

Judge simplicity by the number of concepts, authorities, and navigation hops a reader must hold in mind, not by line count alone.

## 3. Shape files and directories

- Give each concept one discoverable owner. Place code beside the data or lifecycle it governs.
- Use concrete, domain-specific names. Avoid vague buckets such as `utils`, `helpers`, `misc`, `base`, or `manager` unless the repository already gives that name one precise meaning.
- Name a module after its job, not after its history or the moment it runs. Rename when the name has stopped describing the contents.
- Group by who runs a thing, not by file type. Two sibling directories with the same generic name, or a directory sharing a package name while holding something unrelated, are navigation defects.
- Keep directory depth proportional to real domain boundaries. Do not create a package for one small implementation or mirror a hierarchy without adding ownership value.
- Preserve import paths that are an established public contract or have a named consumer requiring compatibility. Distinguish those from internal paths that can migrate with their callers. A path existing in the repository is not sufficient reason to add or retain a forwarding module. When external use is unknown, state that uncertainty; do not claim absence from repository-only search. Treat re-exports as compatibility conveniences, not competing ownership declarations.
- When a public domain identity must remain, keep a thin subclass or adapter over the canonical core. Do not copy its validation, defaults, or state transitions into the compatibility surface.
- Keep related implementation, tests, schemas, and resource files predictably adjacent according to repository convention. Move a test that patches private internals to the module that now owns them.
- Respect the layering: `common` owns primitives and must not import product packages, and a lower layer must not import a higher-level implementation to reach a primitive it needs. Extract the primitive downward instead.
- When a boundary guard lists required files, update it deliberately with the reason recorded, rather than working around it.

Before presenting or implementing a proposed file tree, review sibling names together without their comments. A reader should be able to locate concrete tasks from the names and containment alone. Use actual tasks from the current request, such as computing entropy, counting valid samples, loading saved evidence, or rendering an action report. If several names are equally plausible, the proposal is not ready: clarify ownership, merge a redundant boundary, or choose a more specific domain name. Similar spelling does not require a merge when contracts differ; different spelling does not prove separate ownership.

For each retained boundary, identify its input, output, canonical decision, and a concrete change that belongs there rather than in its sibling. Do not retain a proposed set of modules merely because it is a familiar pipeline. Choose boundaries before choosing names; a synonym change or uniform noun/verb style does not establish clearer responsibility. Preserve established names that already identify their job; do not rename them solely for stylistic uniformity. Then check the whole tree for repeated owners, aliases, and extra navigation hops. File-count reductions are supporting measurements, not acceptance criteria.

Make large files navigable in place when splitting would add indirection: document why related responsibilities remain together, use a precise module docstring, keep a consistent declaration order, and add sparse section markers for stable boundaries.

## 4. Split a module without breaking its callers

Do not split a file because of line count. Line count is a symptom worth investigating; the criterion is whether the parts change for different reasons and can expose a smaller stable boundary. When they do:

- Split along the lines the code already follows, and let the resulting names read as the pipeline they are.
- Separate the decision from the effect: keep pure planning, binding, and expansion apart from the one module that reaches a model, a network, or a disk.
- Separate measuring from rendering, and format projection from execution, so a change to how a result is displayed cannot quietly change what was measured.
- Keep a thin stable facade only when the supported public import contract or a named consumer requires it. Otherwise migrate the internal callers, tests, and docs together and remove the old path. See the conditional compatibility example in [references/patterns.md](references/patterns.md).
- When a facade is required, pin it with an identity test and distinguish the supported import surface from implementation ownership in the package README.
- Move code intact. A split is mechanical and the existing suite is its evidence; any behavior change made alongside is named and justified separately.
- Before moving a class, check for consumers of its `__module__`, of pickles, or of reflection.

Aim for one reason to change per module. A package where no file exceeds a few hundred lines is the outcome of that, not the goal.

## 5. Keep logic direct

- Put validation before side effects and keep the happy path visible.
- Prefer explicit data flow and ordinary control flow over hidden mutation, callbacks, registries, metaprogramming, or boolean mode switches.
- Keep functions focused on one level of abstraction. Extract a helper only when it names a complete decision, lifecycle, transformation, or invariant—not merely several lines.
- Centralize lifecycle and cleanup rules with the resource owner. Make failure paths and state transitions as deliberate as the success path. A flag such as `keep_workspaces` must mean one thing under every configuration.
- Use existing domain types and contracts instead of parallel dictionaries, strings, or near-duplicate models.
- When several signals can answer one question, resolve *where* to look before *what* to look for, order the layers by how deliberate each signal is, and keep a loose layer from outranking a deliberate one. Make a permissive layer strict enough that a false positive cannot be scored as a real answer.
- Delete dead branches, stale compatibility code, historical labels, and comments that only restate syntax when the contract allows it.
- Write comments for constraints, invariants, security boundaries, and non-obvious reasons. Do not narrate edit history.

Moving code changes more than layout. Before relocating logic, check override dispatch and monkeypatch targets; exception timing and cleanup order; which thread or process it now runs on (a signal-based timeout arms only on the main thread, and `multiprocessing` spawn re-imports the parent `__main__`); whether a spawned child stays reachable to kill, so a timeout kills the process group rather than orphaning what it started; and whether a stray `sys.path` bootstrap now makes one module importable under two names, which is one class as two objects.

Concurrency assumptions travel with the resource. If one session owns one container, serve its calls one at a time instead of letting a caller's default parallelism race for it.

## 6. Give configuration and shared resources one authority

- Give each semantic unit one authoritative source. Two schemas claiming one configuration key is a defect even when each reads correctly on its own.
- A capability that lands in one of two entry points is an incomplete change. Every entry point must read one recipe the same way, or they will disagree about something that matters.
- If two inputs can define the same behavior, reject the combination before any side effect and name both sides. Never resolve deliberately declared inputs by precedence or silent override order.
- Reject at construction, parse, or composition time rather than at first use. Discovering an unusable configuration after a run has been paid for is the failure this prevents.
- Accepting an option and quietly not honouring it is worse than refusing it: the configuration reads as honoured while doing nothing.
- When a key moves, raise an error naming its new home instead of dropping it silently.
- Prefer properties enforced by construction over properties maintained by convention. Synthesize the canonical record rather than relaxing a contract or faking a component to satisfy it.
- Move the complete unit of behavior. Do not centralize stored text or data while leaving rendering, defaults, validation, or override semantics duplicated across consumers.
- Route every consumer through the same resolver or contract while allowing the consumer to retain its domain-specific orchestration.
- Do not pin an identity in source that a caller should state or a build should resolve. Resolve it at build time and record it in the output, so the artifact names exactly what it read.
- Preserve enough provenance to identify the resolved resource and verify result-affecting content. State explicitly what a version, digest, or reference does and does not cover, and where a digest is evidence rather than an enforced check.
- Validate packaged, generated, or embedded artifacts at their representation boundary: load resources, render declared variants, compile embedded code, and reject unsupported variables or shapes early.
- Test registry completeness in both directions: every packaged entry has a consumer contract, and every declared consumer reference resolves to an entry.

## 7. Make failure and absence visible

- A run in which nothing succeeded must never exit successfully with a normal-looking report. A broken configuration wearing the shape of a result is the worst available outcome, because nobody re-reads the log beside a number.
- Distinguish measured zero from nothing ran, and absent from zero, in the exit code and at the top of whatever a reader sees first. An undefined rate must read as absent in both the numbers and the rendering.
- Scope such a change to the command boundary. Programmatic callers keep the return contract they had.
- Distinguish claims that are not the same claim. Passing is not certifying; a wrong answer certified as proven needs its own counter, or it reads as ordinary error.
- Malformed input fails loudly. Well-formed input you do not recognize is counted in metadata, not dropped and not invented as observations.
- When projecting a foreign source onto a canonical record, represent its divergences honestly: record what the source could not do, split a combined record into the parts the contract requires, and never let a projection imply a guarantee the source did not give.
- Prefer a failure that names the offending inputs over one that surfaces as a derived value the caller cannot trace back.

## 8. Delete deliberately

- A module whose only importer is its own test has a coverage number and no user. Delete it—but first move any signal it alone measured onto the production path.
- An unread field is either removed, or kept with its named future stated at the definition and pinned by a test, so the next sweep of unread fields does not delete it. A field that reads as configuration but changes nothing is worse than an absent one.
- Removing or reshaping a persisted field bumps the schema version, so no earlier artifact is silently reused against the new shape.
- Delete an example or script that nothing references. A broken example costs a reader time before it fails. If ownership must be decided first, quarantine it in a clearly marked holding area whose README states what has to happen before a file leaves.
- State a breaking rename explicitly and show that no in-repository caller, script, or doc used the old form.

## 9. Reuse without obscuring ownership

Before deduplicating:

1. compare the complete semantics of both paths;
2. select the smallest existing contract that is genuinely canonical;
3. delegate directly, retaining a thin domain type or adapter only when its public identity matters;
4. preserve exception types, ordering, defaults, and observable hooks;
5. delete the superseded implementation and replace tests that encode only its removed internals;
6. retain or add contract tests at the shared boundary and regression tests for each caller's distinct behavior.

Do not create a third abstraction merely to connect two small implementations. If reuse requires many flags or conditionals, keep the paths separate and share only the genuinely identical primitive.

A merge that would change semantics is its own change with its own justification, never a side effect of a move. When two similar paths classify or group differently, keep them separate, share the identical primitive, and record the reason where a future reader will find it.

## 10. Depend deliberately

- One tool, one version, everywhere it runs. Pin a formatter exactly across CI, pre-commit, and the development extra; a range is what produces the split it is meant to prevent.
- Bound a dependency to the majors the code is actually built and tested on, with both ends measured rather than guessed. An open range promises a compatibility surface nothing has tested.
- Supporting two incompatible majors at once means two code paths for one contract. Make the newer one its own migration.
- Refuse a dependency whose transitive pins conflict with an existing base dependency, and implement the subset you need locally instead.
- Put a new optional capability behind an extra that nothing imports eagerly, and keep the module needing it separable, so composition can name and validate the feature without it installed.

## 11. Verify against reality

Review the diff as a new reader before running checks:

- Can a reader locate each concrete requested task from the proposed sibling names without comments? If multiple modules are plausible, has the ownership or naming been corrected?
- Can a human or agent find the canonical owner from names and imports?
- Can the main execution path be followed without jumping through unnecessary layers?
- Does every new file, abstraction, option, field, and comment earn its cost now?
- Is the implementation smaller in concepts, not merely fewer lines?
- Does each behavior have one authority, with conflicting override paths refused rather than resolved?
- Do shared resources have complete consumer coverage and useful provenance?
- Did simplification preserve public behavior, subclass behavior, failure timing, and cleanup?
- Would a reader of the output be able to tell a real result from a run that never ran?

Then produce evidence rather than confidence:

- Run it. The defects worth finding in this repository were found by executing the path—building the dataset, calling the live server, driving the real CLI—not by reading it. Reading finds inconsistencies; running finds the ones that matter.
- Prefer a real captured fixture to a constructed one; a constructed fixture can only confirm what you already believed. When no capture is possible, mark it as constructed and say what it does not cover.
- Record the measurement beside the constant it justifies, and state the relation a future editor will break. Correct a comment whose reasoning you discover is wrong instead of leaving it beside the corrected value.
- Move a test that pinned the old value along with the contract it states, rather than deleting the assertion.
- Run the narrowest relevant tests and static checks first, then broader repository checks in proportion to the affected boundaries.
- Report what changed, what passed, what was skipped and why, and what remains unverified. Never claim a check that did not run in this workspace.
