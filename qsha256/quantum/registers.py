"""Word registers, ancilla management, and the circuit builder.

Two ideas here carry most of the project's weight.

**Words are wiring, not storage.**  A :class:`Word` is an ordered *view* onto
qubits, least-significant bit first.  Rotating a word therefore costs **zero
quantum gates**: it is a relabelling of which physical qubit plays the role of
which bit.  A logical right shift is the same relabelling with the vacated
high positions bound to the constant ``None`` (logical zero) rather than to a
qubit.  This is why ``ROTR`` and ``SHR`` never appear in the gate counts.

**Ancillas are borrowed, not allocated.**  :class:`AncillaPool` hands out clean
``|0>`` qubits and takes them back when the caller has uncomputed them.  Reuse
is what makes the qubit-minimising strategies actually minimise qubits, and the
pool records the high-water mark so ``max live qubits`` is a measured quantity
rather than an estimate.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from qiskit import QuantumCircuit, QuantumRegister
from qiskit.circuit import Qubit

__all__ = ["Word", "AncillaPool", "CircuitBuilder", "Section"]


class Word:
    """An LSB-first view of ``n`` bit positions.

    Each position holds either a :class:`~qiskit.circuit.Qubit` or ``None``,
    where ``None`` denotes a bit that is known to be constant zero (produced by
    :meth:`shr`).  Constant-zero positions are skipped by every gate-emitting
    routine, which is exactly the right semantics: XORing zero into a target is
    a no-op, and it costs nothing.
    """

    __slots__ = ("bits", "label")

    def __init__(self, bits: Sequence[Qubit | None], label: str = ""):
        self.bits: tuple[Qubit | None, ...] = tuple(bits)
        self.label = label

    def __len__(self) -> int:
        return len(self.bits)

    def __getitem__(self, i: int) -> Qubit | None:
        return self.bits[i]

    def __iter__(self) -> Iterator[Qubit | None]:
        return iter(self.bits)

    def __repr__(self) -> str:
        return f"Word({self.label!r}, {len(self.bits)} bits)"

    @property
    def qubits(self) -> list[Qubit]:
        """The non-constant qubits backing this word, in LSB-first order."""
        return [q for q in self.bits if q is not None]

    def rotr(self, n: int) -> Word:
        """``ROTR^n`` as pure wiring: result bit ``i`` is source bit ``(i + n) mod w``.

        Costs zero gates.  Callers that treat this as free must be honest that
        the freeness comes from renaming wires, which is only legitimate because
        the rotated word is *consumed* (read from), never written back in place.
        """
        w = len(self.bits)
        n %= w
        return Word(self.bits[n:] + self.bits[:n], f"rotr{n}({self.label})")

    def shr(self, n: int) -> Word:
        """``SHR^n`` as wiring: result bit ``i`` is source bit ``i + n``, else zero.

        The discarded low bits are *not* destroyed -- they remain in the source
        register, untouched.  Only this *view* forgets them.  That is what makes
        an irreversible classical shift usable inside a reversible circuit: the
        information still exists, we simply do not read it here.
        """
        w = len(self.bits)
        if not 0 <= n < w:
            raise ValueError(f"shift {n} out of range for {w}-bit word")
        return Word(self.bits[n:] + (None,) * n, f"shr{n}({self.label})")

    def slice(self, start: int, stop: int) -> Word:
        return Word(self.bits[start:stop], f"{self.label}[{start}:{stop}]")


@dataclass
class Section:
    """A named span of the instruction list, used to attribute cost to components."""

    name: str
    start: int
    end: int = -1
    depth: int = 0
    children: list[Section] = field(default_factory=list)


class AncillaPool:
    """Allocates and recycles clean ``|0>`` work qubits.

    Contract: a borrower must return every qubit to ``|0>`` before releasing it.
    The pool cannot verify that -- the test suite does, by simulating circuits
    and asserting the ancilla register ends in the all-zero state.
    """

    def __init__(self, builder: CircuitBuilder, name: str = "anc"):
        self._builder = builder
        self._name = name
        self._free: list[Qubit] = []
        #: Every qubit the pool has ever handed out, in allocation order.  The
        #: verifier asserts all of these end in |0>: that is the pool's contract,
        #: and it is checked independently of whatever the data registers hold.
        self.all: list[Qubit] = []
        self.total = 0
        self.live = 0
        self.peak_live = 0

    def _fresh(self, n: int) -> list[Qubit]:
        reg = QuantumRegister(n, f"{self._name}{self.total}")
        self._builder._register_names.add(reg.name)
        self._builder.circuit.add_register(reg)
        self.total += n
        self.all.extend(reg)
        return list(reg)

    def acquire(self, n: int, label: str = "") -> Word:
        """Take ``n`` clean qubits, reusing recycled ones where possible."""
        take = min(n, len(self._free))
        qubits = [self._free.pop() for _ in range(take)]
        if take < n:
            qubits += self._fresh(n - take)
        self.live += n
        self.peak_live = max(self.peak_live, self.live)
        return Word(qubits, label or f"{self._name}[{n}]")

    def release(self, word: Word) -> None:
        """Return qubits to the pool.  They must already be back in ``|0>``."""
        qubits = word.qubits
        self._free.extend(qubits)
        self.live -= len(qubits)

    @contextmanager
    def borrow(self, n: int, label: str = "") -> Iterator[Word]:
        """Scoped :meth:`acquire` / :meth:`release`."""
        word = self.acquire(n, label)
        try:
            yield word
        finally:
            self.release(word)


class CircuitBuilder:
    """Thin, gate-level wrapper around :class:`~qiskit.QuantumCircuit`.

    Circuits are built **flat** -- only ``x``, ``cx``, ``ccx``, ``mcx``, ``swap``
    and (for the QFT adder) phase gates are emitted, never opaque composite
    instructions.  Flatness costs a little construction time but buys three
    things that matter more: ``count_ops`` and ``depth`` are directly meaningful
    without decomposition, the fast basis-state simulator can execute the
    circuit exactly, and per-component cost attribution is just an index range.
    """

    def __init__(self, name: str = "qsha256"):
        self.circuit = QuantumCircuit(name=name)
        self.ancillas = AncillaPool(self)
        self.sections: list[Section] = []
        self._stack: list[Section] = []
        self._data_qubits = 0
        self._register_names: set[str] = set()

    # -- registers ---------------------------------------------------------

    def add_word(self, bits: int, name: str) -> Word:
        """Allocate a named *data* register (counted separately from ancillas).

        Names are made unique on collision, so the same construction can be
        emitted more than once into one circuit (as Grover does with its oracle).
        """
        unique = name
        if unique in self._register_names:
            suffix = 1
            while f"{name}_{suffix}" in self._register_names:
                suffix += 1
            unique = f"{name}_{suffix}"
        self._register_names.add(unique)
        reg = QuantumRegister(bits, unique)
        self.circuit.add_register(reg)
        self._data_qubits += bits
        return Word(list(reg), unique)

    def add_words(self, count: int, bits: int, prefix: str) -> list[Word]:
        return [self.add_word(bits, f"{prefix}{i}") for i in range(count)]

    @property
    def data_qubits(self) -> int:
        return self._data_qubits

    @property
    def ancilla_qubits(self) -> int:
        return self.ancillas.total

    @property
    def peak_ancillas(self) -> int:
        return self.ancillas.peak_live

    # -- sections ----------------------------------------------------------

    @contextmanager
    def section(self, name: str) -> Iterator[Section]:
        """Record a named span of instructions for per-component cost attribution."""
        sec = Section(name=name, start=len(self.circuit.data), depth=len(self._stack))
        (self._stack[-1].children if self._stack else self.sections).append(sec)
        self._stack.append(sec)
        try:
            yield sec
        finally:
            self._stack.pop()
            sec.end = len(self.circuit.data)

    # -- gates -------------------------------------------------------------

    def x(self, q: Qubit) -> None:
        self.circuit.x(q)

    def cx(self, control: Qubit, target: Qubit) -> None:
        self.circuit.cx(control, target)

    def ccx(self, c0: Qubit, c1: Qubit, target: Qubit) -> None:
        self.circuit.ccx(c0, c1, target)

    def swap(self, a: Qubit, b: Qubit) -> None:
        self.circuit.swap(a, b)

    def z(self, q: Qubit) -> None:
        """Diagonal phase flip.  Not a permutation gate, but the basis-state
        simulator tracks it exactly as a sign -- which is what makes a phase
        oracle checkable without a statevector."""
        self.circuit.z(q)

    def h(self, q: Qubit) -> None:
        self.circuit.h(q)

    def mcx(self, controls: Sequence[Qubit], target: Qubit, ancillas: Sequence[Qubit]) -> None:
        """Multi-controlled X built from a balanced AND tree (see ``primitives.boolean``)."""
        from .primitives.boolean import and_tree_mcx

        and_tree_mcx(self, list(controls), target, list(ancillas))

    def append_reversed(self, start: int, end: int) -> None:
        """Append the inverse of instructions ``[start:end)`` -- i.e. uncompute them.

        Valid because the builder only emits self-inverse permutation gates.
        :meth:`inverse_of` guards that invariant.
        """
        block = list(self.circuit.data[start:end])
        for inst in reversed(block):
            name = inst.operation.name
            if name not in _SELF_INVERSE:
                raise ValueError(
                    f"cannot reverse {name!r} by replay: it is not self-inverse. "
                    "Use QuantumCircuit.inverse() for this sub-circuit instead."
                )
            self.circuit.append(inst.operation, inst.qubits, inst.clbits)


#: Gates the builder emits that are their own inverse, so a reversed replay of
#: an instruction span uncomputes it exactly.
_SELF_INVERSE = frozenset({"x", "cx", "ccx", "swap", "cz", "ccz", "h", "z", "mcx"})


def iter_qubits(words: Iterable[Word]) -> list[Qubit]:
    out: list[Qubit] = []
    for w in words:
        out.extend(w.qubits)
    return out
