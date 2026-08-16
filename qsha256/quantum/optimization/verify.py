"""Equivalence checking for reversible circuits.

Automated optimization is only worth anything if the optimized circuit still
computes the right function.  This module is the safety net: every rewritten or
searched circuit is checked against a reference before its numbers are allowed
into a report.

Three levels of assurance, and the code always says which one it used:

``EXHAUSTIVE``
    Every input in the space was tried.  Available when the circuit is narrow
    enough (``<= max_exhaustive_bits`` free inputs).  This is a *proof* for
    permutation circuits: agreeing on every computational basis state means the
    two circuits implement the same permutation, and since both are built from
    real permutation gates there is no phase freedom left to hide a difference.

``RANDOMIZED``
    A sample of basis states agreed.  Not a proof.  It is nonetheless strong
    evidence for structured circuits like these, where a construction error
    almost never produces a function that agrees on random inputs -- and it is
    the only option at 32-bit width.

``STRUCTURAL``
    The circuits are gate-for-gate identical after normalisation.  Cheap and
    conclusive when it applies.

The distinction matters and is propagated into reports: "verified exhaustively"
and "verified on 200 random inputs" are different claims.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Sequence

from qiskit import QuantumCircuit

from ...validation.basis_sim import BasisSimulator, UnsupportedGate
from ..registers import Word

__all__ = ["Assurance", "EquivalenceResult", "check_equivalence", "verify_against_classical"]


class Assurance:
    EXHAUSTIVE = "EXHAUSTIVE"
    RANDOMIZED = "RANDOMIZED"
    STRUCTURAL = "STRUCTURAL"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass
class EquivalenceResult:
    equivalent: bool
    assurance: str
    trials: int
    detail: str = ""
    counterexample: dict | None = None
    failures: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.equivalent

    def __str__(self) -> str:
        verdict = "EQUIVALENT" if self.equivalent else "NOT EQUIVALENT"
        return f"{verdict} [{self.assurance}, {self.trials} trial(s)] {self.detail}".strip()


def check_equivalence(
    reference: QuantumCircuit,
    candidate: QuantumCircuit,
    free_qubits: Sequence | None = None,
    trials: int = 128,
    max_exhaustive_bits: int = 16,
    seed: int = 0,
) -> EquivalenceResult:
    """Check that two circuits implement the same map on computational basis states.

    Both circuits must be over the same qubits (the rewriter preserves them).
    ``free_qubits`` restricts which inputs are varied; the rest start at ``|0>``,
    which is the right model for ancillas and lets a wide circuit still be
    checked exhaustively over its genuine inputs.
    """
    if reference.num_qubits != candidate.num_qubits:
        return EquivalenceResult(
            False, Assurance.STRUCTURAL, 0, "different qubit counts"
        )

    try:
        ref_sim = BasisSimulator(reference)
        cand_sim = BasisSimulator(candidate)
    except UnsupportedGate as exc:
        return EquivalenceResult(False, Assurance.UNSUPPORTED, 0, str(exc))

    free = list(free_qubits) if free_qubits is not None else list(reference.qubits)
    indices = [ref_sim.index_of(q) for q in free]
    n = len(indices)

    if n <= max_exhaustive_bits:
        inputs = range(1 << n)
        assurance = Assurance.EXHAUSTIVE
    else:
        rng = random.Random(seed)
        inputs = [rng.getrandbits(n) for _ in range(trials)]
        # Always include the corner cases a random sample is likely to miss.
        inputs = [0, (1 << n) - 1, 1, (1 << (n - 1))] + list(inputs)
        assurance = Assurance.RANDOMIZED

    count = 0
    for value in inputs:
        count += 1
        state = [0] * reference.num_qubits
        for bit, idx in enumerate(indices):
            state[idx] = (value >> bit) & 1
        ref_out, ref_phase = ref_sim.run(state)
        cand_out, cand_phase = cand_sim.run(state)
        if ref_out != cand_out or ref_phase != cand_phase:
            return EquivalenceResult(
                False,
                assurance,
                count,
                "outputs differ",
                counterexample={
                    "input": value,
                    "reference_phase": ref_phase,
                    "candidate_phase": cand_phase,
                    "differing_qubits": [
                        i for i, (a, b) in enumerate(zip(ref_out, cand_out)) if a != b
                    ],
                },
            )

    return EquivalenceResult(True, assurance, count, f"{n} free input qubits")


def verify_against_classical(
    circuit: QuantumCircuit,
    inputs: dict[Word, int] | Callable[[random.Random], dict[Word, int]],
    expected: Callable[[dict[Word, int]], dict[Word, int]],
    clean: Sequence[Word] = (),
    allow_dirty: Sequence[Word] = (),
    ancillas: Sequence = (),
    trials: int = 32,
    seed: int = 0,
) -> EquivalenceResult:
    """Check a circuit against a classical reference function.

    ``inputs`` either fixes one assignment or draws one from an RNG; ``expected``
    maps an input assignment to the register values the circuit should produce;
    ``clean`` names registers that must end in ``|0>``.  Ancilla hygiene is
    checked too: any qubit outside the named registers that ends set is a leak,
    and a leak inside a Grover oracle is a correctness bug, not an inefficiency.

    ``allow_dirty`` names registers that are *permitted* to hold leftover state.
    A forward-only circuit legitimately leaves its working registers and
    materialised schedule words populated -- that is precisely what "not
    garbage-free" means -- so flagging it would be wrong.

    ``ancillas`` names the recycled work qubits.  These must *always* come back
    to ``|0>``, in every design, garbage-free or not: the pool hands the same
    qubits out repeatedly, so a borrower that fails to uncompute corrupts
    whatever borrows them next.  When given, this is checked directly and takes
    precedence over the coarser ``allow_dirty`` accounting.
    """
    try:
        sim = BasisSimulator(circuit)
    except UnsupportedGate as exc:
        return EquivalenceResult(False, Assurance.UNSUPPORTED, 0, str(exc))

    rng = random.Random(seed)
    failures: list[str] = []

    for trial in range(trials):
        assignment = inputs(rng) if callable(inputs) else inputs
        out, _ = sim.run(sim.load(assignment))

        for word, want in expected(assignment).items():
            got = sim.read(out, word)
            if got != want:
                failures.append(f"trial {trial}: {word.label} = {got}, expected {want}")

        for word in clean:
            got = sim.read(out, word)
            if got != 0:
                failures.append(f"trial {trial}: {word.label} not returned to |0> (= {got})")

        if ancillas:
            leaked = [q for q in ancillas if out[sim.index_of(q)]]
        else:
            touched = (
                list(expected(assignment))
                + list(clean)
                + list(allow_dirty)
                + list(assignment)
            )
            leaked = sim.nonzero_indices(out, exclude=touched)
        if leaked:
            failures.append(
                f"trial {trial}: {len(leaked)} recycled work qubit(s) not returned to |0>"
            )

        if failures:
            break
        if not callable(inputs):
            break

    return EquivalenceResult(
        equivalent=not failures,
        assurance=Assurance.RANDOMIZED,
        trials=trials,
        detail="classical reference comparison",
        failures=failures,
    )
