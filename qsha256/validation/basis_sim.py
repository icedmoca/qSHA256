"""Exact simulation of reversible circuits on computational basis states.

Every circuit qSHA256 builds for the *compute* part of SHA-256 is a permutation
circuit: it uses only X, CNOT, Toffoli and SWAP.  Such a circuit maps each
computational basis state to exactly one other basis state, so simulating it on
a basis input requires no statevector at all -- just classical bit propagation
through the gate list, in ``O(gates)`` time and ``O(qubits)`` memory.

That is the single most important tool in this repository.  It means the
**real 32-bit, 64-round SHA-256 circuit** -- thousands of qubits, hundreds of
thousands of gates, utterly beyond statevector simulation -- can still be
executed exactly and checked against ``hashlib``.  Correctness of the reversible
construction is therefore a *verified* property, not an extrapolated one.

What this does **not** do is exercise superposition or phase.  A permutation
circuit's action on basis states determines it completely up to global phase, so
for the compute circuits nothing is lost.  For the Grover oracle, where relative
phase is the entire point, the phase-tracking mode below covers diagonal gates
(Z/CZ/CCZ), and genuine superposition behaviour is checked with Qiskit's
:class:`~qiskit.quantum_info.Statevector` on toy instances instead.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from qiskit import QuantumCircuit
from qiskit.circuit import Qubit

from ..quantum.registers import Word

__all__ = [
    "BasisSimulator",
    "PreconditionViolated",
    "UnsupportedGate",
    "set_word_value",
    "simulate_basis",
    "word_value",
]


class UnsupportedGate(Exception):
    """Raised when a circuit contains a gate outside the permutation+diagonal set."""


class PreconditionViolated(Exception):
    """Raised in strict mode when a Gidney AND gate's precondition does not hold."""


# opcode -> (n_control_qubits, kind)
_X_LIKE = {"x": 0, "cx": 1, "ccx": 2, "mcx": -1, "and_g": 2, "and_g_dg": 2}
_Z_LIKE = {"z": 0, "cz": 1, "ccz": 2}

#: Gidney AND gates equal a Toffoli only while their preconditions hold, so the
#: simulator can optionally verify them (see ``strict`` below).
_AND_COMPUTE, _AND_UNCOMPUTE = "and_g", "and_g_dg"


class BasisSimulator:
    """Compiles a circuit once, then executes it on many basis states quickly."""

    def __init__(self, circuit: QuantumCircuit, strict: bool = False):
        """``strict`` verifies the Gidney AND preconditions on every gate.

        ``and_g`` is only a Toffoli while its target is ``|0>``, and ``and_g_dg``
        only clears the target while it holds ``x AND y``. Both are silent
        correctness traps, so the test suite enables this.
        """
        self.circuit = circuit
        self.strict = strict
        self.num_qubits = circuit.num_qubits
        self._index: dict[Qubit, int] = {q: i for i, q in enumerate(circuit.qubits)}
        self._program: list[tuple[int, tuple[int, ...]]] = []
        self._compile()

    # opcodes
    _OP_X, _OP_SWAP, _OP_Z, _OP_AND, _OP_AND_DG = 0, 1, 2, 3, 4

    def _compile(self) -> None:
        idx = self._index
        prog = self._program
        for inst in self.circuit.data:
            name = inst.operation.name
            qs = tuple(idx[q] for q in inst.qubits)
            if name == _AND_COMPUTE and self.strict:
                prog.append((self._OP_AND, qs))
            elif name == _AND_UNCOMPUTE and self.strict:
                prog.append((self._OP_AND_DG, qs))
            elif name in _X_LIKE:
                prog.append((self._OP_X, qs))
            elif name in _Z_LIKE:
                prog.append((self._OP_Z, qs))
            elif name == "swap":
                prog.append((self._OP_SWAP, qs))
            elif name in ("id", "barrier"):
                continue
            else:
                raise UnsupportedGate(
                    f"gate {name!r} is not a permutation or diagonal gate; "
                    "this circuit cannot be simulated in the computational basis "
                    "(use qiskit.quantum_info.Statevector for it)"
                )

    def run(self, bits: Sequence[int]) -> tuple[list[int], int]:
        """Execute on an input bit string.  Returns ``(output_bits, phase_sign)``.

        ``phase_sign`` is ``+1`` or ``-1``, accumulated from diagonal gates. It is
        the amplitude the input basis state picks up -- exactly the quantity a
        Grover phase oracle is supposed to flip.
        """
        if len(bits) != self.num_qubits:
            raise ValueError(f"expected {self.num_qubits} bits, got {len(bits)}")
        state = list(bits)
        phase = 1
        OP_X, OP_Z, OP_SWAP = self._OP_X, self._OP_Z, self._OP_SWAP
        OP_AND, OP_AND_DG = self._OP_AND, self._OP_AND_DG
        for op, qs in self._program:
            if op == OP_X:
                # last qubit is the target; all others are controls
                for c in qs[:-1]:
                    if not state[c]:
                        break
                else:
                    state[qs[-1]] ^= 1
            elif op == OP_Z:
                for c in qs:
                    if not state[c]:
                        break
                else:
                    phase = -phase
            elif op == OP_SWAP:
                a, c = qs
                state[a], state[c] = state[c], state[a]
            elif op == OP_AND:
                x, y, t = qs
                if state[t]:
                    raise PreconditionViolated(
                        "and_g requires its target to be |0>, but it held 1. "
                        "The Gidney AND is not a general Toffoli."
                    )
                state[t] = state[x] & state[y]
            elif op == OP_AND_DG:
                x, y, t = qs
                if state[t] != (state[x] & state[y]):
                    raise PreconditionViolated(
                        "and_g_dg requires its target to hold exactly x AND y "
                        f"(target={state[t]}, x AND y={state[x] & state[y]}); "
                        "the uncomputation would not clear it."
                    )
                state[t] = 0
            else:  # pragma: no cover - every opcode above is exhaustive
                raise UnsupportedGate(f"unhandled opcode {op}")
        return state, phase

    # -- convenience -------------------------------------------------------

    def zero_state(self) -> list[int]:
        return [0] * self.num_qubits

    def load(self, assignments: Mapping[Word, int], bits: Sequence[int] | None = None) -> list[int]:
        """Build an input bit string from ``{word: integer value}``."""
        state = list(bits) if bits is not None else self.zero_state()
        for word, value in assignments.items():
            set_word_value(state, self._index, word, value)
        return state

    def read(self, state: Sequence[int], word: Word) -> int:
        return word_value(state, self._index, word)

    def index_of(self, qubit: Qubit) -> int:
        return self._index[qubit]

    def nonzero_indices(self, state: Sequence[int], exclude: Iterable[Word] = ()) -> list[int]:
        """Indices of set qubits, ignoring the given words -- used to assert clean ancillas."""
        skip = set()
        for w in exclude:
            skip.update(self._index[q] for q in w.qubits)
        return [i for i, v in enumerate(state) if v and i not in skip]


def word_value(state: Sequence[int], index: Mapping[Qubit, int], word: Word) -> int:
    """Read an LSB-first :class:`Word` out of a bit-string state."""
    value = 0
    for i, q in enumerate(word):
        if q is not None and state[index[q]]:
            value |= 1 << i
    return value


def set_word_value(state: list[int], index: Mapping[Qubit, int], word: Word, value: int) -> None:
    """Write an integer into an LSB-first :class:`Word` of a bit-string state."""
    for i, q in enumerate(word):
        if q is None:
            if (value >> i) & 1:
                raise ValueError("cannot set a bit at a constant-zero position")
            continue
        state[index[q]] = (value >> i) & 1


def simulate_basis(
    circuit: QuantumCircuit, assignments: Mapping[Word, int]
) -> tuple[BasisSimulator, list[int]]:
    """One-shot helper: build a simulator, load ``assignments``, run from ``|0>``."""
    sim = BasisSimulator(circuit)
    out, _ = sim.run(sim.load(assignments))
    return sim, out
