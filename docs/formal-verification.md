# Formal verification

Randomised basis-state testing checks a few thousand of `2^768` inputs. That is
strong evidence and it is not a proof. This layer turns the important claims
into proofs.

## How it works

Every compute circuit here is a permutation circuit: only X, CNOT, Toffoli and
the Gidney AND gates, all of which act classically on the computational basis.
So each qubit's value at any point is a **Boolean function of the inputs**, and
the circuit can be executed symbolically, propagating functions instead of bits.

Those functions live in an **And-Inverter Graph**: a DAG of two-input AND nodes
with inverted edges, structurally hashed so identical subfunctions are shared.
Tseitin-encoding the graph into CNF turns questions about the circuit into
questions for a SAT solver.

Two query shapes cover everything:

- **Is this literal ever true?** Assert it and ask for a model. UNSAT means
  "false for every input" — used for ancilla cleanliness.
- **Do these two vectors ever differ?** XOR them pairwise, OR the results,
  assert it. UNSAT means the two functions are identical on every input.

UNSAT is a proof. SAT hands back a concrete counterexample, decoded into
readable register values.

## What is proved

| Property | Meaning |
|---|---|
| functional equivalence | the circuit computes the specification, on all inputs |
| ancilla cleanliness | every recycled work qubit returns to `\|0>`, on all inputs |
| Gidney AND preconditions | every `and_g` target is `\|0>`; every `and_g_dg` target holds exactly `x AND y` |

Proved at full 32-bit width: all four adders, Ch, Maj, all four sigma
functions, the compression round in every layout, both message-schedule
strategies, copy-in and chaining.

## Independence

The specification is written **twice**, by different code. `classical/sha256.py`
works on Python integers; `formal/spec_aig.py` builds the same functions as
Boolean formulas and shares nothing with it. Comparing a circuit against a
specification that shares its helpers can hide a bug living in the shared part.

Note especially that the specification's addition is a textbook ripple-carry
over formulas, structurally unlike CDKM, VBE or Gidney — so proving them equal
is a real check rather than a tautology.

## Why the whole circuit is proved compositionally

A single monolithic miter over a full compression does not scale, and the reason
is worth stating: the circuit's adder and the specification's adder represent
carries completely differently, so the solver must reconcile two unrelated
encodings afresh at every one of ~450 additions.

Measured: a full 32-bit **round** proves in about a second; a 4-bit one-round
**compression** miter takes over a minute.

So the proof is compositional, which is what industrial equivalence checkers do
and is not a weakening — each component is proved **universally quantified over
its own inputs**:

- `prove_round`: *for every* state and message word, the round circuit computes
  `round_step(state, W, K[t])`.
- `prove_schedule_step`: *for every* window contents, one advance computes the
  recurrence.
- `prove_copy_in`, `prove_chaining`: the framing operations.

Chaining universally-quantified components is sound by induction on the round
index. The remaining gap — "we proved the parts" versus "the circuit is their
composition" — is closed structurally by `prove_structure`, which checks the
instruction spans partition the circuit exactly, with nothing left over.

## The XOR-awareness tradeoff

SHA-256 is overwhelmingly XOR, and a plain AIG cannot see that `a XOR b XOR a`
is `b` — the operands hash to different nodes. Tracking each literal's
decomposition into a parity of atoms makes that cancellation structural, so a
correct compute/uncompute pair collapses to constant false with no solver call.

But it is a genuine tradeoff, measured rather than assumed: canonicalisation
grows the graph from 10,671 nodes to 15,917 on a SHA-256 round, and a bigger
graph makes an equivalence miter harder — taking that proof from 1.3 seconds to
39. So the ancilla guard turns it **on** and the equivalence prover turns it
**off**.

## The borrow checker

`CircuitBuilder(guard_ancillas=True)` runs the same symbolic execution
*alongside* construction. Releasing an ancilla that is not provably `|0>` raises
at the **release site**, so the exception points at the code that failed to
uncompute rather than at a symptom thousands of gates later. With XOR-awareness
on, correct code proves itself by constant folding alone — zero solver calls.

## Running it

```bash
qsha256 prove --scope standard     # primitives, sigmas, rounds
qsha256 prove --scope full         # adds schedule and compressions
```

## Limits

Proofs are bounded by an AND-node budget, an XOR atom-set cap and a SAT timeout,
so an over-ambitious query fails cleanly rather than exhausting memory. **A
timeout is never reported as a proof.**

AIGER export is provided so external tools (ABC and anything else reading AIGER)
can re-check independently, but nothing here depends on an external binary.
