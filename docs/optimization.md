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

**Phase-polynomial optimization.** T-par (Amy et al.) merges T gates across gate
boundaries and reaches a T-count well below `7 x (Toffoli count)`. qSHA256 does
not do this, and it is the clearest place where prior work beats this project:
228,992 T against our 326,144. See `docs/leaderboard.md`.

**Measurement-based AND uncomputation.** Gidney's temporary-AND construction
computes an AND with 4 T gates and *uncomputes* it with none, using measurement
and classical feedforward. Since qSHA256's adders and Boolean primitives are full
of compute/uncompute AND pairs, this is the single most promising unexplored
reduction. It requires tracking AND-pair structure through the builder and a
non-unitary gate model; documented as future work rather than half-implemented.
