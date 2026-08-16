"""Phase-polynomial folding: T-par-style T-count reduction.

The largest gap between qSHA256 and published work was that our T-count sat at
exactly ``7 x`` the Toffoli count, while Amy et al. reach far lower by running
**T-par**, a phase-polynomial optimizer. This module implements the core of that
idea.

The observation
---------------

Take any circuit region built only from ``CNOT``, ``X`` and diagonal phase gates
(``T``, ``S``, ``Z`` and their inverses) -- that is, everything between Hadamard
gates. Such a region always factors as::

    U = L . D

where ``L`` is a linear reversible map over GF(2) (the CNOT network) and ``D`` is
**diagonal**: it applies a phase that depends only on the *values of linear
functions of the inputs*. Concretely, every qubit at every moment carries some
``f(x) = x_{i1} XOR x_{i2} XOR ...``, and a ``T`` gate applied there contributes
``omega^{f(x)}`` to ``D``, with ``omega = e^{i pi / 4}``.

Because ``D`` is a *sum over linear functions*, two phase gates acting on the
**same** linear function -- however far apart, and however many CNOTs lie
between them -- combine into one::

    T . T   ->  S      (2 x pi/4 = pi/2)   : two T gates become a Clifford
    T . Tdg ->  nothing                    : they cancel outright
    4 x T   ->  Z                          : a Clifford
    8 x T   ->  identity

Only an *odd* total angle still needs a T gate. So the reduction is real and
sometimes dramatic: every pair of T gates that lands on the same linear function
is a T gate saved.

Why SHA-256 benefits
--------------------

Reversible construction is full of compute/uncompute pairs -- ``Ch`` and ``Maj``
are computed then uncomputed, and every ripple-carry adder's ``MAJ`` is undone by
a matching ``UMA``. The two halves apply T gates to the *same* linear functions,
so folding cancels them wholesale. Amy et al. observed exactly this: "due to the
construction of the adders every Toffoli gate shares two controls with another,
[which] allows T-par to remove a large number of T-gates".

What this implementation does and does not do
---------------------------------------------

It **merges** phases onto the earliest point where each linear function is live,
leaving the CNOT skeleton untouched. That is sound by the factorisation above:
``D`` is the same operator wherever its factors are applied, provided the
function is live there.

It does **not** re-synthesise the CNOT network. Full T-par additionally rebuilds
the linear part to expose more merges and to reduce T-depth. That is a harder
problem (and an active research area); leaving the skeleton alone means this pass
can never make the circuit worse, and keeps it verifiable.

Correctness
-----------

Every rewrite is a phase-gate move justified by the factorisation. The CNOT/X/H
skeleton is preserved gate for gate, which is checked by the test suite, and full
unitary equivalence (up to global phase, which is tracked explicitly) is verified
against Qiskit's :class:`~qiskit.quantum_info.Operator` on small circuits.

Reference
---------
M. Amy, D. Maslov, M. Mosca, "Polynomial-time T-depth optimization of
Clifford+T circuits via matroid partitioning", IEEE TCAD 33(10), 2014,
arXiv:1303.2042.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from qiskit import QuantumCircuit

__all__ = [
    "PHASE_ANGLES",
    "PhaseFoldResult",
    "phase_fold",
    "to_clifford_t",
]

#: Diagonal gates we understand, in units of pi/4.
PHASE_ANGLES = {"t": 1, "tdg": -1, "s": 2, "sdg": -2, "z": 4}

#: Gates that act linearly on the computational basis and so preserve the
#: phase-polynomial structure.
_LINEAR = {"cx", "x", "id", "barrier"}


@dataclass
class PhaseFoldResult:
    """A folded circuit plus what the fold achieved."""

    circuit: QuantumCircuit
    t_before: int = 0
    t_after: int = 0
    merged_functions: int = 0
    distinct_functions: int = 0
    regions: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def t_removed(self) -> int:
        return self.t_before - self.t_after

    @property
    def reduction(self) -> float:
        return (self.t_removed / self.t_before) if self.t_before else 0.0

    def summary(self) -> str:
        return (
            f"phase folding: T-count {self.t_before:,} -> {self.t_after:,} "
            f"({-100 * self.reduction:+.1f}%), "
            f"{self.distinct_functions:,} distinct linear functions, "
            f"{self.merged_functions:,} of them carried more than one phase gate"
        )


#: The standard exact Toffoli decomposition (Nielsen & Chuang Fig. 4.9): 7 T,
#: 6 CNOT, 2 H, no ancilla. Applied by hand rather than through the transpiler so
#: the expansion is deterministic, independent of the Qiskit version, and agrees
#: *by construction* with the "standard" model in
#: :mod:`qsha256.quantum.resources.clifford_t`. Verified exactly (including
#: global phase) against ``ccx`` in the test suite.
def _expand_ccx(out: QuantumCircuit, a, b, c) -> None:
    out.h(c)
    out.cx(b, c)
    out.tdg(c)
    out.cx(a, c)
    out.t(c)
    out.cx(b, c)
    out.tdg(c)
    out.cx(a, c)
    out.t(b)
    out.t(c)
    out.h(c)
    out.cx(a, b)
    out.t(a)
    out.tdg(b)
    out.cx(a, b)


def to_clifford_t(circuit: QuantumCircuit, optimization_level: int = 0) -> QuantumCircuit:
    """Expand a circuit into the Clifford+T basis so it can be phase-folded.

    Toffolis are expanded with :func:`_expand_ccx`; everything else is copied
    through untouched.

    Gidney temporary ANDs are deliberately left **opaque**. Expanding them would
    replace the measurement-based uncomputation with its unitary stand-in and
    throw away the entire Gidney saving; leaving them intact lets the two
    optimizations compose, with folding cleaning up the Clifford+T regions
    around the ANDs. (Qiskit's transpiler cannot express this: it refuses custom
    gates in ``basis_gates``, and would expand them.)
    """
    out = circuit.copy_empty_like()
    for inst in circuit.data:
        if inst.operation.name == "ccx":
            _expand_ccx(out, *inst.qubits)
        else:
            out.append(inst.operation, inst.qubits, inst.clbits)
    return out


def phase_fold(circuit: QuantumCircuit, already_clifford_t: bool = False) -> PhaseFoldResult:
    """Merge phase gates acting on identical linear functions.

    ``circuit`` is expanded into Clifford+T first unless ``already_clifford_t``.
    """
    if not already_clifford_t:
        circuit = to_clifford_t(circuit)

    index = {q: i for i, q in enumerate(circuit.qubits)}
    n = circuit.num_qubits

    # Each qubit carries an affine function of "variables": a frozenset of
    # variable ids (the GF(2) linear part) plus a constant bit.
    lin: list[frozenset[int]] = [frozenset({i}) for i in range(n)]
    const: list[int] = [0] * n
    next_var = n
    regions = 1

    # -- pass 1: accumulate ------------------------------------------------
    totals: dict[frozenset[int], int] = {}
    occurrences: dict[frozenset[int], int] = {}
    # Phase the *original* circuit carries beyond the per-key linear terms.
    phase_in_quarters = 0
    # Phase the *rebuilt* circuit will introduce; subtracted at the end so the
    # two operators agree exactly, not merely up to a global phase.
    phase_out_quarters = 0
    sites: list[tuple[int, frozenset[int], int]] = []  # (instruction index, key, const)

    for position, inst in enumerate(circuit.data):
        name = inst.operation.name
        qubits = [index[q] for q in inst.qubits]

        if name in PHASE_ANGLES:
            q = qubits[0]
            key, c = lin[q], const[q]
            angle = PHASE_ANGLES[name]
            if not key:
                # The qubit holds a constant, so this is pure global phase.
                phase_in_quarters += angle * c
                sites.append((position, key, c))
                continue
            # A constant offset flips the sign of the angle on the linear part
            # and contributes a global phase: omega^(k(1 XOR f)) = omega^k . omega^(-k f).
            if c:
                phase_in_quarters += angle
                angle = -angle
            totals[key] = (totals.get(key, 0) + angle) % 8
            occurrences[key] = occurrences.get(key, 0) + 1
            sites.append((position, key, c))
        elif name == "cx":
            c_q, t_q = qubits
            lin[t_q] = lin[t_q] ^ lin[c_q]
            const[t_q] ^= const[c_q]
        elif name == "x":
            const[qubits[0]] ^= 1
        elif name in ("id", "barrier"):
            continue
        else:
            # Anything else (H, and any gate we do not model) destroys what we
            # know about the qubits it touches: give each a fresh variable.
            for q in qubits:
                lin[q] = frozenset({next_var})
                const[q] = 0
                next_var += 1
            if name == "h":
                regions += 1

    # -- pass 2: rebuild ---------------------------------------------------
    emitted: set[frozenset[int]] = set()
    site_map = {position: (key, c) for position, key, c in sites}

    out = circuit.copy_empty_like()
    for position, inst in enumerate(circuit.data):
        if position not in site_map:
            out.append(inst.operation, inst.qubits, inst.clbits)
            continue

        key, c = site_map[position]
        if not key or key in emitted:
            continue  # already accounted for, or pure global phase
        emitted.add(key)

        total = totals.get(key, 0) % 8
        if total == 0:
            continue
        # Realise omega^(total . f) on a qubit currently holding f XOR c.
        angle = total if not c else (-total) % 8
        if c:
            phase_out_quarters += angle
        _emit_phase(out, inst.qubits[0], angle)

    out.global_phase = circuit.global_phase + (phase_in_quarters - phase_out_quarters) * math.pi / 4

    before = sum(1 for inst in circuit.data if inst.operation.name in ("t", "tdg"))
    after = sum(1 for inst in out.data if inst.operation.name in ("t", "tdg"))
    return PhaseFoldResult(
        circuit=out,
        t_before=before,
        t_after=after,
        merged_functions=sum(1 for k, v in occurrences.items() if v > 1),
        distinct_functions=len(occurrences),
        regions=regions,
        notes=[
            "CNOT/X/H skeleton preserved exactly; only diagonal phase gates moved.",
            "Full T-par additionally re-synthesises the linear part; this pass "
            "does not, so it can never make a circuit worse.",
        ],
    )


def _emit_phase(circuit: QuantumCircuit, qubit, angle: int) -> None:
    """Emit ``omega^angle`` on ``qubit`` using as few T gates as possible.

    Only odd angles need a T gate at all; even ones are Clifford.
    """
    angle %= 8
    if angle == 0:
        return
    if angle >= 4:
        circuit.z(qubit)
        angle -= 4
    if angle >= 2:
        circuit.s(qubit)
        angle -= 2
    if angle == 1:
        circuit.t(qubit)
    elif angle == -1:
        circuit.tdg(qubit)
