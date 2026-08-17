"""A borrow checker for uncomputation.

The single invariant the whole reversible construction rests on is that a
borrowed ancilla is returned to ``|0>``.  The pool hands the same qubits out
over and over, so a borrower that forgets to uncompute does not fail loudly --
it silently corrupts whatever borrows those qubits next, usually thousands of
gates later, and the resulting bug is miserable to localise.

Until now that invariant was checked *after the fact*, by simulating a finished
circuit and inspecting the ancilla register.  This module checks it **at the
moment of release**, so the exception points at the code that failed to
uncompute rather than at some distant symptom.

How it works
------------

Every qubit carries a Boolean function of the circuit's free inputs, maintained
incrementally as gates are emitted -- the same symbolic execution
:mod:`qsha256.formal.aig` performs, but running alongside construction instead
of afterwards.  Ancillas start as the constant ``false``.  On
:meth:`~qsha256.quantum.registers.AncillaPool.release`, the guard asks whether
the qubit's function is still identically false.

Most of the time the answer is free: the AIG's constant folding collapses
``x XOR x`` to ``false`` on the spot, so a correct compute/uncompute pair proves
itself with no search at all.  When folding is not enough the guard falls back
to a SAT call, which either proves cleanliness for all inputs or produces a
concrete input that leaks.

This makes an entire bug class unrepresentable in practice, at the cost of
tracking a Boolean function per qubit, so it is opt-in: pass
``CircuitBuilder(guard_ancillas=True)``.  The test suite turns it on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from qiskit.circuit import Qubit

from ..formal.aig import AIG, CONST_FALSE, Lit

if TYPE_CHECKING:  # pragma: no cover
    from .registers import Word

__all__ = ["AncillaGuard", "AncillaLeak"]


class AncillaLeak(Exception):
    """Raised when an ancilla is released without being uncomputed.

    Carries the label of the offending register and, where the solver produced
    one, an input assignment that demonstrates the leak.
    """

    def __init__(self, message: str, label: str = "", counterexample: dict | None = None):
        super().__init__(message)
        self.label = label
        self.counterexample = counterexample


@dataclass
class AncillaGuard:
    """Tracks each qubit's Boolean function while a circuit is built."""

    aig: AIG = field(default_factory=AIG)
    values: dict[Qubit, Lit] = field(default_factory=dict)
    #: Release sites that needed a SAT call rather than folding alone.
    solver_calls: int = 0
    #: Release sites proved clean purely by constant folding.
    folded_clean: int = 0
    checked: int = 0
    enabled: bool = True

    # -- registration ------------------------------------------------------

    def add_data(self, qubits, prefix: str = "q") -> None:
        """Register data qubits as free Boolean inputs."""
        for i, qubit in enumerate(qubits):
            self.values[qubit] = self.aig.new_input(f"{prefix}[{i}]")

    def add_ancilla(self, qubits) -> None:
        """Register ancillas, which start in ``|0>``."""
        for qubit in qubits:
            self.values[qubit] = CONST_FALSE

    # -- gate tracking -----------------------------------------------------

    def x(self, q: Qubit) -> None:
        if self.enabled:
            self.values[q] = self.aig.xor(self.values[q], 1)

    def cx(self, c: Qubit, t: Qubit) -> None:
        if self.enabled:
            self.values[t] = self.aig.xor(self.values[t], self.values[c])

    def ccx(self, a: Qubit, b: Qubit, t: Qubit) -> None:
        if self.enabled:
            self.values[t] = self.aig.xor(
                self.values[t], self.aig.and_(self.values[a], self.values[b])
            )

    def swap(self, a: Qubit, b: Qubit) -> None:
        if self.enabled:
            self.values[a], self.values[b] = self.values[b], self.values[a]

    def diagonal(self, *qubits: Qubit) -> None:
        """Z/CZ/CCZ change phase, not basis-state values."""

    def opaque(self, *qubits: Qubit) -> None:
        """A gate we cannot model: everything it touches becomes a free input."""
        if not self.enabled:
            return
        for q in qubits:
            self.values[q] = self.aig.new_input("opaque")

    # -- the check ---------------------------------------------------------

    def check_clean(self, word: Word) -> None:
        """Verify every qubit of ``word`` is provably ``|0>``.

        Raises :class:`AncillaLeak` if not.
        """
        if not self.enabled:
            return
        self.checked += 1
        dirty = [(i, q) for i, q in enumerate(word.qubits) if self.values[q] != CONST_FALSE]
        if not dirty:
            self.folded_clean += 1
            return

        # Folding was not enough; ask the solver whether any of them can be 1.
        from ..formal.cnf import CNFEncoder, model_assignment, solve

        self.solver_calls += 1
        encoder = CNFEncoder(self.aig)
        encoder.any_true([self.values[q] for _, q in dirty])
        result = solve(encoder)
        if result.proved:
            return

        bits = model_assignment(encoder, result.model, self.aig) if result.model else []
        leaking = [i for i, q in dirty if bits and self.aig.evaluate([self.values[q]], bits)[0]]
        raise AncillaLeak(
            f"ancilla register {word.label!r} was released without being "
            f"uncomputed: bit(s) {leaking or [i for i, _ in dirty]} can be 1. "
            "The borrow must return every qubit to |0>, because the pool will "
            "hand these same qubits to the next borrower.",
            label=word.label,
            counterexample={"input_bits": "".join(map(str, bits)), "bits": leaking},
        )

    def summary(self) -> str:
        return (
            f"ancilla guard: {self.checked} release site(s) checked, "
            f"{self.folded_clean} proved by constant folding, "
            f"{self.solver_calls} needed a SAT call"
        )
