"""Formal proofs about qSHA256 circuits.

Three properties, each discharged by a SAT call whose UNSAT answer is a proof
over *every* input, not a sample:

**Functional correctness.**  The circuit's output functions and the
specification's output functions are miter-equivalent.  UNSAT means they agree
on all ``2^n`` inputs.

**Ancilla cleanliness.**  Every recycled work qubit's output function is
identically false.  This is the property the whole reversible construction rests
on -- the pool hands the same qubits out repeatedly, so a borrower that fails to
uncompute silently corrupts whatever borrows them next -- and until now it was
only ever *sampled* by the test suite.

**Gidney AND preconditions.**  ``and_g`` equals a Toffoli only while its target
is ``|0>``, and ``and_g_dg`` clears its target only while that target holds
exactly ``x AND y``.  Both are checked here as universal statements rather than
per-input assertions.

A SAT answer is equally useful: the model is decoded back into concrete register
values, giving a minimal reproducer for the bug.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from qiskit import QuantumCircuit
from qiskit.circuit import Qubit

from ..quantum.registers import Word as QWord
from .aig import AIG, CONST_FALSE, Lit, symbolic_execute
from .cnf import DEFAULT_SOLVER, CNFEncoder, model_assignment, solve

__all__ = [
    "CircuitProof",
    "Proof",
    "prove_ancillas_clean",
    "prove_and_preconditions",
    "prove_circuit",
    "prove_equivalent",
]


@dataclass
class Proof:
    """One proof obligation and its outcome."""

    name: str
    proved: bool
    detail: str = ""
    counterexample: dict[str, Any] | None = None
    num_vars: int = 0
    num_clauses: int = 0
    aig_nodes: int = 0
    solver: str = ""
    seconds: float = 0.0

    def __bool__(self) -> bool:
        return self.proved

    def __str__(self) -> str:
        verdict = "PROVED" if self.proved else "REFUTED"
        size = f"{self.aig_nodes:,} AND nodes, {self.num_clauses:,} clauses"
        return f"[{verdict}] {self.name}  ({size}, {self.seconds:.2f}s)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "proved": self.proved,
            "detail": self.detail,
            "counterexample": self.counterexample,
            "aig_nodes": self.aig_nodes,
            "cnf_vars": self.num_vars,
            "cnf_clauses": self.num_clauses,
            "solver": self.solver,
            "seconds": round(self.seconds, 3),
        }


def _decode(encoder: CNFEncoder, model, aig: AIG, named: dict[str, list[Lit]]) -> dict[str, Any]:
    """Turn a SAT model into readable register values."""
    bits = model_assignment(encoder, model, aig)
    out: dict[str, Any] = {"input_bits": "".join(str(b) for b in bits)}
    for label, lits in named.items():
        values = aig.evaluate(lits, bits)
        out[label] = sum(v << i for i, v in enumerate(values))
    return out


def prove_equivalent(
    aig: AIG,
    circuit_outputs: Sequence[Lit],
    spec_outputs: Sequence[Lit],
    name: str = "functional equivalence",
    solver: str = DEFAULT_SOLVER,
    named: dict[str, list[Lit]] | None = None,
) -> Proof:
    """Prove two literal vectors compute the same function on every input."""
    started = time.time()
    encoder = CNFEncoder(aig)
    encoder.miter(list(circuit_outputs), list(spec_outputs))
    result = solve(encoder, solver)
    counterexample = None
    if result.satisfiable:
        counterexample = _decode(encoder, result.model, aig, named or {})
        counterexample["circuit"] = _bits_to_int(aig, circuit_outputs, encoder, result.model)
        counterexample["spec"] = _bits_to_int(aig, spec_outputs, encoder, result.model)
    detail = "outputs differ"
    if result.proved:
        detail = (
            "identical by AIG structural hashing; no SAT call needed"
            if result.structural
            else "outputs identical on all inputs (SAT: UNSAT)"
        )
    return Proof(
        name=name,
        proved=result.proved,
        detail=detail,
        counterexample=counterexample,
        num_vars=result.num_vars,
        num_clauses=result.num_clauses,
        aig_nodes=aig.num_ands,
        solver=result.solver,
        seconds=time.time() - started,
    )


def _bits_to_int(aig: AIG, lits: Sequence[Lit], encoder: CNFEncoder, model) -> int:
    bits = aig.evaluate(list(lits), model_assignment(encoder, model, aig))
    return sum(v << i for i, v in enumerate(bits))


def prove_ancillas_clean(
    aig: AIG,
    ancilla_lits: Sequence[Lit],
    name: str = "ancilla cleanliness",
    solver: str = DEFAULT_SOLVER,
) -> Proof:
    """Prove every ancilla returns to ``|0>`` for every input.

    Encoded as: "is any ancilla output ever true?"  UNSAT proves none is.
    """
    started = time.time()
    live = [lit for lit in ancilla_lits if lit != CONST_FALSE]
    if not live:
        # Constant folding already reduced every ancilla to false: that is
        # itself a proof, and a stronger one than the solver would give.
        return Proof(
            name=name,
            proved=True,
            detail=(
                f"all {len(ancilla_lits)} ancilla functions folded to constant "
                "false during symbolic execution; no SAT call needed"
            ),
            aig_nodes=aig.num_ands,
            seconds=time.time() - started,
        )
    encoder = CNFEncoder(aig)
    encoder.any_true(live)
    result = solve(encoder, solver)
    counterexample = None
    if result.satisfiable:
        bits = model_assignment(encoder, result.model, aig)
        dirty = [i for i, lit in enumerate(ancilla_lits) if aig.evaluate([lit], bits)[0]]
        counterexample = {
            "input_bits": "".join(str(b) for b in bits),
            "dirty_ancilla_indices": dirty[:32],
            "dirty_count": len(dirty),
        }
    return Proof(
        name=name,
        proved=result.proved,
        detail=(
            (
                f"{len(live)} of {len(ancilla_lits)} ancilla functions reached the "
                "solver; all provably zero"
                if not result.structural
                else f"all {len(ancilla_lits)} folded to false structurally"
            )
            if result.proved
            else "at least one ancilla can be left non-zero"
        ),
        counterexample=counterexample,
        num_vars=result.num_vars,
        num_clauses=result.num_clauses,
        aig_nodes=aig.num_ands,
        solver=result.solver,
        seconds=time.time() - started,
    )


def prove_and_preconditions(
    aig: AIG,
    preconditions: Sequence[tuple[str, Lit]],
    name: str = "Gidney AND preconditions",
    solver: str = DEFAULT_SOLVER,
) -> Proof:
    """Prove every Gidney AND precondition holds on every input."""
    started = time.time()
    if not preconditions:
        return Proof(name=name, proved=True, detail="circuit contains no Gidney AND gates")
    live = [(label, lit) for label, lit in preconditions if lit != CONST_FALSE]
    if not live:
        return Proof(
            name=name,
            proved=True,
            detail=(
                f"all {len(preconditions)} preconditions folded to constant false "
                "during symbolic execution; no SAT call needed"
            ),
            aig_nodes=aig.num_ands,
            seconds=time.time() - started,
        )
    encoder = CNFEncoder(aig)
    encoder.any_true([lit for _, lit in live])
    result = solve(encoder, solver)
    counterexample = None
    if result.satisfiable:
        bits = model_assignment(encoder, result.model, aig)
        violated = [label for label, lit in live if aig.evaluate([lit], bits)[0]]
        counterexample = {
            "input_bits": "".join(str(b) for b in bits),
            "violated": violated[:8],
            "violated_count": len(violated),
        }
    return Proof(
        name=name,
        proved=result.proved,
        detail=(
            f"{len(preconditions)} preconditions, {len(live)} needed solving; all hold"
            if result.proved
            else "a precondition can be violated"
        ),
        counterexample=counterexample,
        num_vars=result.num_vars,
        num_clauses=result.num_clauses,
        aig_nodes=aig.num_ands,
        solver=result.solver,
        seconds=time.time() - started,
    )


@dataclass
class CircuitProof:
    """All proof obligations for one circuit."""

    target: str
    proofs: list[Proof] = field(default_factory=list)
    aig_nodes: int = 0
    seconds: float = 0.0

    @property
    def proved(self) -> bool:
        return all(p.proved for p in self.proofs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "proved": self.proved,
            "aig_nodes": self.aig_nodes,
            "seconds": round(self.seconds, 3),
            "proofs": [p.to_dict() for p in self.proofs],
        }

    def __str__(self) -> str:
        head = f"{self.target}: {'ALL PROVED' if self.proved else 'REFUTED'}"
        return "\n".join([head] + [f"  {p}" for p in self.proofs])


def prove_circuit(
    circuit: QuantumCircuit,
    free_qubits: Sequence[Qubit],
    build_spec: Callable[[AIG, dict[Qubit, Lit]], Sequence[Lit]],
    output_qubits: Sequence[Qubit],
    ancilla_qubits: Sequence[Qubit] = (),
    target: str = "circuit",
    solver: str = DEFAULT_SOLVER,
    named: dict[str, list[Lit]] | None = None,
) -> CircuitProof:
    """Symbolically execute a circuit and discharge all three obligations.

    ``build_spec`` receives the shared AIG and the map from input qubit to its
    input literal, and returns the specification's output literals.  Sharing the
    AIG lets both sides refer to the same input variables while still being
    built by independent code.
    """
    started = time.time()
    state = symbolic_execute(circuit, free_qubits=free_qubits)
    spec_outputs = build_spec(state.aig, state.inputs)
    circuit_outputs = [state.values[q] for q in output_qubits]

    proofs = [
        prove_equivalent(
            state.aig,
            circuit_outputs,
            spec_outputs,
            name="functional equivalence with the classical specification",
            solver=solver,
            named=named,
        ),
        prove_ancillas_clean(
            state.aig,
            [state.values[q] for q in ancilla_qubits],
            solver=solver,
        ),
        prove_and_preconditions(state.aig, state.and_preconditions, solver=solver),
    ]
    return CircuitProof(
        target=target,
        proofs=proofs,
        aig_nodes=state.aig.num_ands,
        seconds=time.time() - started,
    )


def word_lits(state, word: QWord) -> list[Lit]:
    """Literals of a :class:`~qsha256.quantum.registers.Word` after execution."""
    return [CONST_FALSE if q is None else state.values[q] for q in word]
