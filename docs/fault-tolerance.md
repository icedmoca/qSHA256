# From logical to physical

This is the step where quantum resource estimates most often go wrong, so
qSHA256 quarantines it in one module
(`qsha256/quantum/resources/physical.py`) and never lets its outputs leak into
the logical reports.

## Why the two cannot be conflated

A logical qubit is not a physical qubit. Running a 1,313-logical-qubit circuit
does not need 1,313 physical qubits; it needs each logical qubit encoded in an
error-correcting code, plus factories manufacturing the magic states every T gate
consumes. The multiplier is not a constant — it depends on the physical error
rate, the target failure probability, and how long the computation runs.

## The model

Surface code, following Fowler et al. (2012) with the accounting style of Gidney
& Ekera (2019).

**Logical error rate** per logical qubit per code cycle, for distance `d` and
physical error rate `p` below threshold `p_th`:

```text
p_L(d) = 0.1 * (p / p_th) ** ((d + 1) / 2)
```

**Code distance**: the smallest odd `d` with

```text
p_L(d) * (logical qubits) * (code cycles) <= target failure probability
```

Solved self-consistently, since runtime depends on `d` and the required `d`
depends on runtime.

**Physical qubits**: `routing_factor * d^2` per logical qubit (default 2), plus
the magic-state factory footprint.

**Runtime**: the larger of

- *reaction-limited*: `toffoli_depth * d` code cycles — a floor set by the
  circuit's serial structure;
- *distillation-limited*: `T_count / factories * factory_cycles` — set by how
  fast magic states can be produced.

## Above threshold

If `p >= p_th` the estimator returns **not achievable** rather than a number.
Above threshold, increasing the code distance makes things *worse*; there is no
`d` that helps, and reporting a large figure would misrepresent that.

## Everything is a parameter

Published magic-state factory designs vary by more than an order of magnitude, so
factory footprint and throughput are **inputs** on a named `HardwareModel`, never
silent constants. Three models ship (`superconducting`, `optimistic`,
`conservative`); the first uses round numbers chosen to be recognisable, not
measurements of any specific device. All of this appears in the `Assumptions`
block of every estimate.

## What is deliberately not modelled

- **Routing overhead.** The logical depth assumes all-to-all connectivity. Real
  2D-local hardware pays more. The runtime figures are therefore optimistic, and
  say so.
- **Classical control cost.** Syndrome decoding is a substantial classical
  workload; Amy et al. (2016) argue it belongs in any honest comparison against
  classical attacks.
- **Layout and compilation.** Turning a logical circuit into a surface-code
  layout is its own research problem.

These are stated as limitations rather than estimated. If there is insufficient
basis to compute something correctly, qSHA256 documents it as future work rather
than inventing a number.
