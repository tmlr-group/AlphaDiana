---
name: advanced-maths
description: Use when a question involves numerical computation, algebra, calculus, linear algebra, probability, or symbolic manipulation. Forces explicit step-by-step computation with unit and dimensional checks. Adapted from rand/cc-polymath/skills/math.
---

# Advanced Maths — Disciplined Symbolic + Numeric Reasoning

For any question with a quantitative or symbolic step, follow this protocol. Do **not** estimate or guess values.

## 1. Restate the Math

- What quantity is being asked? (a value / an option / a relation)
- What inputs are given? (values, units, constraints)
- What domain is the math in? (algebra / calculus / probability / linear algebra / number theory / discrete)

## 2. Pick the Right Tool

- **Algebra**: solve symbolically before plugging in numbers.
- **Calculus**: identify if the question wants a limit / derivative / integral / extremum.
- **Probability**: identify the sample space first; then the event; then P(event).
- **Linear algebra**: track dimensions; (m×n) · (n×p) = (m×p).
- **Number theory**: state the modular structure or divisibility condition.

## 3. Show Every Step

Each step has the form:
```
<expr_n>   [reason: <rule applied>]
```

Do not skip steps. If a step would normally be done by a calculator, write the intermediate value explicitly so it can be checked.

## 4. Unit and Dimension Audit

After each step that produces a quantity with units:
- State the units.
- Check they are consistent with the next step's expected input.

## 5. Sanity-Check the Magnitude

- Is the result the right order of magnitude given the inputs?
- Does it have the right sign?
- For probability: is it in [0, 1]?
- For physical quantities: does it satisfy known limits / conservation?

## 6. Verify Through a Second Route

Compute the answer through one alternative method:
- Limiting case (set a parameter to 0 or ∞)
- Dimensional analysis
- Plug answer back into the original equation
- For multiple-choice: rule out the other options by elimination

If the second route disagrees, redo the work — do NOT pick whichever you prefer.

## 7. Commit

State the final answer in the requested format.
For multiple-choice: `\boxed{X}`.

## Common Failure Modes (avoid)

- Decimal-place slip when extracting from `(1+r)^5 = 2`
- Skipping unit conversion (cm vs m, mol vs mmol)
- Missing constraint ("integer", "positive", "modulo")
- Confusing rate of change with absolute change
