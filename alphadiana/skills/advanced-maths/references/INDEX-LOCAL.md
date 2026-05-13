# Advanced Maths Reference Library — Index

The `references/` folder contains domain-specific math notes adapted from `rand/cc-polymath/skills/math`. The agent uses these as look-up references when solving GPQA / HLE math-heavy questions.

| Domain | File | When to use |
|---|---|---|
| Abstract algebra | abstract-algebra.md | Group theory, ring theory, field extensions |
| Category theory | category-theory-foundations.md | Functor / natural transformation questions |
| Differential equations | differential-equations.md | ODE / PDE setup and standard solutions |
| Linear algebra | linear-algebra-computation.md | Matrix operations, eigenvalues, vector spaces |
| Number theory | number-theory.md | Modular arithmetic, primes, Diophantine |
| Numerical methods | numerical-methods.md | Quadrature, root finding, error propagation |
| Optimization | optimization-algorithms.md | LP / convex / Lagrangian |
| Probability and Statistics | probability-statistics.md | Distributions, expectation, hypothesis testing |
| Set theory | set-theory.md | Cardinalities, axioms, ordinals |
| Topology (algebraic) | topology-algebraic.md | Homology, fundamental group |
| Topology (point-set) | topology-point-set.md | Compactness, connectedness, separation |
| Graph algorithms | graph/ | Graph traversal, shortest path, flow |

## Use Pattern

When the question is identified as math-heavy (Section 1 of SKILL.md), the agent should:
1. Identify the dominant domain
2. Open the relevant reference markdown for canonical formulas / theorems
3. Apply the formula explicitly (do not paraphrase from memory)
4. Verify via the Section 6 alternative-route check

The full set of references is upstream from `rand/cc-polymath` (Apache 2.0 / MIT, see UPSTREAM-DISCOVER-MATH.md).
