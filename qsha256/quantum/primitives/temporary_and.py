"""Gidney's temporary AND: measurement-based uncomputation.

The single largest T-count reduction available to this project, and it comes
from noticing that reversible circuits almost never need a *general* Toffoli.

A Toffoli is expensive because it must work for any target state. But nearly
every Toffoli in a reversible construction writes into a **clean ancilla** and
later **uncomputes** it. That pair is far cheaper than two Toffolis:

**Compute** (``|x> |y> |0>  ->  |x> |y> |x AND y>``) needs only **4 T gates**,
because the target being ``|0>`` removes the constraint that makes the general
Toffoli cost 7.

**Uncompute** needs **zero T gates**. Instead of running a circuit backwards,
measure the target in the X basis and apply a Clifford correction::

    H(target); measure target -> m; if m == 1: CZ(x, y)

The measurement collapses the target, and the ``(-1)^(m * (x AND y))`` phase it
leaves behind is exactly what ``CZ(x, y)`` cancels. Both are Clifford, so the
whole uncomputation is free of non-Clifford resources.

A compute/uncompute pair therefore costs **4 T instead of 14** -- a 3.5x
reduction on the dominant cost driver.

The catch, stated plainly
-------------------------

The uncomputation is **not a unitary circuit**. It requires mid-circuit
measurement and a classically-controlled correction, so:

* it needs hardware with fast measurement and feedforward;
* no unitary transpilation can reproduce it, which means a transpiled T-count
  will *overcount* it. The analyzer detects these gates and switches to the
  analytical Gidney model, saying so in the report;
* a circuit containing it cannot be inverted by naive gate reversal -- reversing
  an ``and_g`` must produce an ``and_g_dg``, which
  :meth:`~qsha256.quantum.registers.CircuitBuilder.append_reversed` handles
  explicitly.

The precondition matters
------------------------

``and_g`` is only equal to a Toffoli when its target starts in ``|0>``, and
``and_g_dg`` only returns the target to ``|0>`` when it holds exactly
``x AND y``. Violate either and the circuit is silently wrong. The basis-state
simulator therefore has a strict mode that checks both preconditions on every
such gate, and the test suite runs with it enabled.

Reference
---------
C. Gidney, "Halving the cost of quantum addition", Quantum 2, 74 (2018),
arXiv:1709.06648.
"""

from __future__ import annotations

from qiskit.circuit import ClassicalRegister, Gate, QuantumCircuit, QuantumRegister

__all__ = [
    "AND_GATE_NAMES",
    "GIDNEY_AND_T_COUNT",
    "GIDNEY_AND_T_DEPTH",
    "AndDgGate",
    "AndGate",
    "and_g",
    "and_g_dg",
    "gidney_and_circuit",
    "gidney_uncompute_circuit",
]

#: T gates in the compute half. Verified against the expansion in the tests.
GIDNEY_AND_T_COUNT = 4

#: T-depth of the concrete expansion below. Gidney describes a T-depth-1 variant
#: using magic-state injection and extra ancillas; this implementation uses the
#: straightforward serial form, and reports its real depth rather than the
#: paper's best case.
GIDNEY_AND_T_DEPTH = 4

AND_GATE_NAMES = frozenset({"and_g", "and_g_dg"})


def gidney_and_circuit() -> QuantumCircuit:
    """The exact Clifford+T circuit for the compute half.

    Qubit order ``(x, y, target)``. Verified in the test suite to map
    ``|x,y,0> -> |x,y,x AND y>`` with **no relative and no global phase**, using
    exactly four T gates.
    """
    qc = QuantumCircuit(3, name="and_g")
    x, y, t = 0, 1, 2
    qc.h(t)
    qc.t(t)
    qc.cx(y, t)
    qc.tdg(t)
    qc.cx(x, t)
    qc.t(t)
    qc.cx(y, t)
    qc.tdg(t)
    qc.h(t)
    qc.sdg(t)
    return qc


def gidney_uncompute_circuit() -> QuantumCircuit:
    """The **real** measurement-based uncomputation, with a classical bit.

    This is what a fault-tolerant implementation actually runs. It is not what
    :class:`AndDgGate` emits into the analysed circuit -- that carries a unitary
    stand-in so the circuit stays simulable and transpilable -- but it is what
    the cost model charges for, and it is exercised by the test suite.

    Zero T gates: an H, a measurement, and a classically-controlled CZ.
    """
    qr = QuantumRegister(3, "q")
    cr = ClassicalRegister(1, "m")
    qc = QuantumCircuit(qr, cr, name="and_g_dg")
    x, y, t = qr[0], qr[1], qr[2]
    qc.h(t)
    qc.measure(t, cr[0])
    with qc.if_test((cr, 1)):
        qc.cz(x, y)
        qc.x(t)  # return the measured qubit to |0> so the pool can recycle it
    return qc


class AndGate(Gate):
    """``target ^= x AND y`` where the target is guaranteed to be ``|0>``.

    Costed as 4 T gates. Its ``definition`` is the exact Clifford+T expansion, so
    transpilation and statevector simulation both agree with the cost model.
    """

    def __init__(self):
        super().__init__("and_g", 3, [])

    def _define(self):
        self.definition = gidney_and_circuit()

    def inverse(self, annotated: bool = False):
        return AndDgGate()


class AndDgGate(Gate):
    """Uncomputes an :class:`AndGate` whose target still holds ``x AND y``.

    Costed as **0 T gates and one measurement** (see the module docstring). The
    ``definition`` here is the unitary inverse of the compute circuit -- a
    stand-in that keeps the circuit simulable, and the reason a *transpiled*
    T-count overcounts this gate while the analytical Gidney model does not.
    """

    def __init__(self):
        super().__init__("and_g_dg", 3, [])

    def _define(self):
        self.definition = gidney_and_circuit().inverse()

    def inverse(self, annotated: bool = False):
        return AndGate()


def and_g(builder, x, y, target) -> None:
    """Emit a Gidney AND.  ``target`` **must** be ``|0>``."""
    builder.circuit.append(AndGate(), [x, y, target])


def and_g_dg(builder, x, y, target) -> None:
    """Emit a Gidney AND uncomputation.  ``target`` **must** hold ``x AND y``."""
    builder.circuit.append(AndDgGate(), [x, y, target])
