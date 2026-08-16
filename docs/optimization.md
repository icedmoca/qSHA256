# Automated search, rewriting, and verification

qSHA256 does not ship one circuit. It ships a *space* of correct circuits and the
machinery to search it.

## Two layers of optimization

**Architectural** — the `Strategy` axes in `qsha256/quantum/strategies.py`. A
strategy is a small discrete parameter vector, so the space can be enumerated
exhaustively, and every point in it is correct by construction. Search never has
to invent a circuit and hope it works.

**Gate-level** — the rewriter in `qsha256/quantum/optimization/rewrite.py`,
working on the emitted instruction list for reductions no architectural choice
can express.

## The rewrite passes

### `cancel` — commutation-aware involution cancellation

Adjacent self-inverse gates annihilate. The useful part is commutation
awareness: identical gates rarely end up adjacent, but they are often separated
only by gates that commute with them. The pass slides a gate forward through
everything it *provably* commutes with and cancels if it meets its twin. This
catches the compute/uncompute seams that reversible construction produces
everywhere.

The commutation relation is deliberately conservative — it returns `False`
whenever unsure, so the pass may miss opportunities but never invents one:

- disjoint qubits always commute;
- two X-type gates commute unless one's target is the other's control (sharing
  only a target is fine: X and X commute);
- diagonal gates commute with each other;
- a diagonal gate commutes with an X-type gate unless the X-type gate's target is
  one of its qubits;
- anything unrecognised does not commute.

### `constfold` — constant propagation from `|0>`

Every ancilla starts in a known state, and much of what the circuit does to them
is knowable at compile time. A CNOT with a provably-`|0>` control is the
identity and disappears; a Toffoli with a provably-`|1>` control degrades to a
CNOT; and so on.

This produces a result worth stating on its own. The `const_add="load"` strategy
materialises the round constant `K[t]` in a register and runs a *general* adder
against it. Constant folding then specialises that adder to the classical bits —
**automatically deriving what `const_add="vbe_const"` hard-codes by hand.** On
toy4 both strategies converge to exactly 584 Toffolis after rewriting. That is
the optimizer rediscovering a human optimization, and it is asserted as a test.

Measured on the full 64-round circuit: 46,592 -> 45,392 Toffoli (-2.6%),
verified equivalent.

### `phasefold` — phase-polynomial folding

The core of T-par. Any region built only from CNOT, X and diagonal phase gates
(everything between Hadamards) factors as `U = L . D`, where `L` is a linear map
over GF(2) and `D` applies a phase depending only on *linear functions of the
inputs*. Two phase gates acting on the **same** linear function therefore
combine, however far apart they are:

```text
T . T   -> S        two T gates become a Clifford
T . Tdg -> nothing  they cancel outright
4 x T   -> Z
8 x T   -> identity
```

Only an *odd* total angle still needs a T gate.

Reversible construction is full of compute/uncompute pairs — `Ch` and `Maj` are
computed then uncomputed, every ripple-carry `MAJ` is undone by a matching `UMA`
— and the two halves apply T gates to the same linear functions. Amy et al. made
exactly this observation about their adders.

**Measured on the full 64-round circuit: 326,144 T → 181,568 T (−44.3%).**

It merges phases onto the earliest point where each function is live and leaves
the CNOT skeleton untouched, gate for gate. It does **not** re-synthesise the
linear part, which full T-par also does to expose further merges and to reduce
T-depth. Not re-synthesising means the pass can never make a circuit worse, and
keeps it cheap to verify — but it also means this captures only part of T-par.

One honest caveat: folding **raises** non-Clifford depth (38,528 → 149,312),
because merging phases onto a single point serialises them. It buys T-count with
depth.

### Gidney temporary ANDs

Not a rewrite pass but an architectural option (`--adder gidney`), and the
largest single lever in the project. Nearly every Toffoli in a reversible circuit
writes into a clean ancilla and later uncomputes it; such a pair costs far less
than two Toffolis:

* **compute** `|x>|y>|0> -> |x>|y>|x AND y>` in **4 T gates**;
* **uncompute** in **zero T gates** — measure the target in the X basis and
  apply a `CZ(x, y)` correction when the outcome is 1.

A compute/uncompute pair costs 4 T instead of 14. A 32-bit addition drops from
**448 T to 124 T**, and the full compression from 326,144 T to **131,744 T**,
while *halving* non-Clifford depth because the uncomputation is Clifford.

The cost is a hardware assumption: mid-circuit measurement with classical
feedforward. No unitary transpilation can reproduce the uncomputation, so the
analyzer refuses to transpile such circuits and uses the analytical Gidney model
instead, saying so in the report.

Combining both reaches **107,168 T**.

## Verification

An optimized circuit that is wrong is not a result, so nothing enters a report
unverified. `qsha256/quantum/optimization/verify.py` reports its assurance level
rather than implying one:

| Level | Meaning |
|---|---|
| `EXHAUSTIVE` | every input tried — a *proof* for permutation circuits |
| `RANDOMIZED` | a sample agreed; strong evidence, not proof |
| `STRUCTURAL` | gate-for-gate identical after normalisation |
| `UNSUPPORTED` | the circuit leaves the computational basis (QFT adder) |

Search additionally checks each design against the classical reference model, and
asserts that the recycled ancilla pool comes back to `|0>` — a leak there is a
correctness bug, not an inefficiency, because the pool hands the same qubits out
repeatedly.

## Pareto search

Minimising qubits and minimising T-depth pull in opposite directions, so there is
no single best circuit and `search_designs` does not pretend otherwise. It
returns the **Pareto front**: designs not beaten on *every* objective at once.

```bash
qsha256 search --spec toy4 --rounds 8
```

Output includes quantified trade statements generated from measured circuits:

```text
cdkm/vbe_const/rolling/wide/rewritten: t_count -20.7%, toffoli_depth -38.5%
    at the cost of qubits +17.6%
```

## Choosing among the front

The Pareto front says which designs are defensible, not which to use — that
depends on what the machine is short of.
`qsha256/quantum/optimization/hardware.py` ranks designs by **spacetime volume**
(physical qubits x runtime) under an explicit hardware model, because occupying a
million qubits for an hour and a thousand for a thousand hours are comparably
expensive.

The winning design changes with the machine. Ranking by logical gate count alone
can pick the wrong one.

## What is not implemented

**Full T-par.** The folding pass merges phases but does not re-synthesise the
CNOT network, which full T-par does to expose additional merges and to reduce
T-depth via matroid partitioning. That is the remaining gap.

**Gidney ANDs in `Ch` and `Maj`.** The adders now use temporary ANDs, but `Ch`
and `Maj` still emit plain Toffolis, because their AND result is XORed into an
accumulator rather than written to a dedicated clean ancilla. Restructuring them
to use a separate AND ancilla would convert the remaining 8,192 Toffolis, at the
cost of one extra ancilla per bit.

**T-depth-optimal AND.** The implemented Gidney AND expansion has T-depth 4
(its four T gates are serial on the target). Gidney describes a T-depth-1 variant
using magic-state injection and extra ancillas; not implemented.
