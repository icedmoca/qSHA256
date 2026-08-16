"""Automated circuit rewriting: commutation-aware cancellation and constant folding.

The strategy axes explore *architectures*.  This module works one level down, on
the emitted gate list, looking for reductions no architectural choice can express.
Two passes are implemented, both of which are sound by construction and are
checked empirically by :mod:`qsha256.quantum.optimization.verify`.

``cancel``
    Adjacent self-inverse gates annihilate.  The useful part is *commutation
    awareness*: two identical gates rarely end up adjacent, but they are often
    separated only by gates that commute with them.  The pass slides a gate
    forward through everything it provably commutes with, and cancels if it
    meets its twin.  This catches the compute/uncompute seams that reversible
    construction produces everywhere -- the uncompute of one sub-expression
    followed immediately by the compute of the next.

``constfold``
    Constant propagation over the ``|0>``-initialised ancillas.  Every ancilla
    starts in a known state, and much of what the circuit does to them is
    knowable at compile time.  A CNOT whose control is provably ``|0>`` is the
    identity and disappears; a Toffoli with a provably-``|0>`` control likewise;
    a Toffoli with a provably-``|1>`` control degrades to a CNOT, and a CNOT
    with a provably-``|1>`` control degrades to an X.

    This pass is what makes ``const_add="load"`` competitive: that strategy
    materialises the round constant ``K[t]`` in a fresh register and runs a
    general adder against it, and constant folding then specialises that adder
    to the classical bits -- automatically deriving what ``const_add="vbe_const"``
    hard-codes by hand.

Soundness
---------
Each rewrite is a local identity.  Commutation uses a deliberately conservative
relation (see :func:`commutes`): it returns ``False`` whenever it is unsure, so
the pass may miss opportunities but never invents one.  Constant folding only
acts on qubits whose value is *proven* by forward propagation from a known
initial state, and abandons a qubit to ``UNKNOWN`` the moment anything
non-deterministic touches it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from qiskit import QuantumCircuit
from qiskit.circuit import Qubit

from ..registers import CircuitBuilder

__all__ = [
    "REWRITE_PASSES",
    "RewriteResult",
    "apply_rewrites",
    "cancel_involutions",
    "commutes",
    "constant_fold",
]

#: Gates whose action is "X on the target, conditioned on the controls".
_X_TYPE = {"x": 0, "cx": 1, "ccx": 2}
#: Diagonal gates: they never change which basis state you are in.
_Z_TYPE = {"z": 0, "cz": 1, "ccz": 2}

ZERO, ONE, UNKNOWN = 0, 1, -1


@dataclass
class RewriteResult:
    """A rewritten circuit plus a record of what changed."""

    circuit: QuantumCircuit
    passes: list[str] = field(default_factory=list)
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)

    @property
    def removed(self) -> int:
        return sum(self.before.values()) - sum(self.after.values())

    def delta(self, gate: str) -> int:
        return self.after.get(gate, 0) - self.before.get(gate, 0)

    def summary(self) -> str:
        total_before = sum(self.before.values())
        # `removed` is positive when gates were eliminated, so the reported
        # change must be negated to read as a reduction.
        pct = -100 * self.removed / total_before if total_before else 0.0
        return (
            f"{'+'.join(self.passes) or 'none'}: "
            f"{total_before:,} -> {sum(self.after.values()):,} gates "
            f"({pct:+.1f}%), ccx {self.before.get('ccx', 0):,} -> {self.after.get('ccx', 0):,}"
        )


def _decompose(inst) -> tuple[str, tuple[Qubit, ...], tuple[Qubit, ...]] | None:
    """Return ``(kind, controls, targets)`` for a gate we understand, else ``None``."""
    name = inst.operation.name
    qs = tuple(inst.qubits)
    if name in _X_TYPE:
        n = _X_TYPE[name]
        return "X", qs[:n], qs[n:]
    if name in _Z_TYPE:
        return "Z", qs, ()
    return None


def commutes(a, b) -> bool:
    """Conservative commutation test for the gate set this project emits.

    * Gates on disjoint qubits always commute.
    * Two X-type gates commute unless one's target is the other's control.
      (Two X-type gates sharing only a target commute: X and X commute.)
    * Two diagonal gates always commute.
    * A diagonal gate commutes with an X-type gate unless the X-type gate's
      target is one of the diagonal gate's qubits.
    * Anything else -- including any gate this module does not recognise --
      is assumed **not** to commute.
    """
    da, db = _decompose(a), _decompose(b)
    if da is None or db is None:
        return False

    kind_a, ctrl_a, tgt_a = da
    kind_b, ctrl_b, tgt_b = db
    qa, qb = set(ctrl_a) | set(tgt_a), set(ctrl_b) | set(tgt_b)
    if qa.isdisjoint(qb):
        return True

    if kind_a == "Z" and kind_b == "Z":
        return True
    if kind_a == "X" and kind_b == "X":
        return not (set(tgt_a) & set(ctrl_b)) and not (set(tgt_b) & set(ctrl_a))
    # One diagonal, one X-type.
    if kind_a == "Z":
        return not (set(tgt_b) & qa)
    return not (set(tgt_a) & qb)


def _same_gate(a, b) -> bool:
    return (
        a.operation.name == b.operation.name
        and tuple(a.qubits) == tuple(b.qubits)
        and not getattr(a.operation, "params", None)
    )


def cancel_involutions(circuit: QuantumCircuit, max_lookahead: int = 64) -> QuantumCircuit:
    """Cancel self-inverse gate pairs separated only by commuting gates.

    ``max_lookahead`` bounds how far a gate is allowed to slide, keeping the
    pass linear on circuits with hundreds of thousands of instructions.
    """
    data = list(circuit.data)
    n = len(data)
    dead = bytearray(n)

    for i in range(n):
        if dead[i]:
            continue
        gate = data[i]
        if _decompose(gate) is None:
            continue
        limit = min(n, i + 1 + max_lookahead)
        for j in range(i + 1, limit):
            if dead[j]:
                continue
            other = data[j]
            if _same_gate(gate, other):
                dead[i] = dead[j] = 1
                break
            if not commutes(gate, other):
                break

    out = circuit.copy_empty_like()
    for i, inst in enumerate(data):
        if not dead[i]:
            out.append(inst.operation, inst.qubits, inst.clbits)
    return out


def constant_fold(
    circuit: QuantumCircuit,
    known_zero: set[Qubit] | None = None,
) -> QuantumCircuit:
    """Propagate known ``|0>`` values forward, specialising or deleting gates.

    ``known_zero`` names the qubits guaranteed to start in ``|0>``; everything
    else is treated as an unknown input.  When called through
    :func:`apply_rewrites` on a :class:`~qsha256.quantum.registers.CircuitBuilder`
    this is exactly the ancilla set, which the builder tracks precisely.
    """
    if known_zero is None:
        known_zero = set(circuit.qubits)

    value: dict[Qubit, int] = {q: (ZERO if q in known_zero else UNKNOWN) for q in circuit.qubits}
    out = circuit.copy_empty_like()

    for inst in circuit.data:
        decomposed = _decompose(inst)
        if decomposed is None:
            # Unrecognised gate: everything it touches becomes unknown.
            for q in inst.qubits:
                value[q] = UNKNOWN
            out.append(inst.operation, inst.qubits, inst.clbits)
            continue

        kind, controls, targets = decomposed

        if kind == "Z":
            # Diagonal: a phase flip conditioned on controls. If any control is
            # provably |0> the gate is the identity and can go.
            if any(value[q] == ZERO for q in controls):
                continue
            out.append(inst.operation, inst.qubits, inst.clbits)
            continue

        target = targets[0]
        states = [value[q] for q in controls]

        if any(s == ZERO for s in states):
            continue  # a provably-|0> control makes the gate the identity

        live = [q for q in controls if value[q] != ONE]
        if len(live) == len(controls):
            # No control resolved: emit unchanged.
            if controls:
                value[target] = UNKNOWN
            else:
                value[target] = ONE - value[target] if value[target] != UNKNOWN else UNKNOWN
            out.append(inst.operation, inst.qubits, inst.clbits)
            continue

        # At least one control is provably |1>: drop it and re-emit a smaller gate.
        if not live:
            out.x(target)
            value[target] = ONE - value[target] if value[target] != UNKNOWN else UNKNOWN
        elif len(live) == 1:
            out.cx(live[0], target)
            value[target] = UNKNOWN
        else:
            out.ccx(live[0], live[1], target)
            value[target] = UNKNOWN

    return out


REWRITE_PASSES: dict[str, Callable[..., QuantumCircuit]] = {
    "cancel": cancel_involutions,
    "constfold": constant_fold,
}


def apply_rewrites(
    source: CircuitBuilder | QuantumCircuit,
    passes: tuple[str, ...] = ("constfold", "cancel"),
    rounds: int = 2,
) -> RewriteResult:
    """Run rewrite passes to a fixed point (or ``rounds`` iterations).

    Passes interact: constant folding turns Toffolis into CNOTs, which creates
    new cancellation opportunities, which can expose further constants.  Running
    the sequence more than once is usually worth it, and the loop stops early
    once a full sweep changes nothing.
    """
    if isinstance(source, CircuitBuilder):
        circuit = source.circuit
        ancillas = {q for reg in circuit.qregs if reg.name.startswith("anc") for q in reg}
    else:
        circuit = source
        ancillas = set()

    before = dict(circuit.count_ops())
    applied: list[str] = []

    for _ in range(rounds):
        size = len(circuit.data)
        for name in passes:
            fn = REWRITE_PASSES[name]
            circuit = fn(circuit, ancillas) if name == "constfold" else fn(circuit)
            applied.append(name)
        if len(circuit.data) == size:
            break

    return RewriteResult(
        circuit=circuit,
        passes=list(dict.fromkeys(applied)),
        before=before,
        after=dict(circuit.count_ops()),
    )
