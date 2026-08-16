"""Clifford+T decomposition models.

A raw Toffoli count is not a fault-tolerant cost.  On an error-corrected machine
Clifford gates are comparatively cheap, while every non-Clifford **T** gate must
be supplied by a magic-state distillation factory -- which dominates both the
qubit footprint and the runtime of essentially every realistic architecture.
So the number that matters is the T-count, and the T-count depends entirely on
*which decomposition you assume*.

This module refuses to hide that dependency.  There is no single "T-count of a
Toffoli"; there are several, and they trade T-count against T-depth, ancillas
and measurement/feedforward capability:

============  =======  =======  =======  ============  ==========================
Model         T-count  T-depth  Ancilla  Measurement   Note
============  =======  =======  =======  ============  ==========================
``standard``  7        4        0        no            textbook exact circuit
``selinger``  7        1        4        no            parallelised, wider
``jones``     4        1        1        yes           needs classical feedback
============  =======  =======  =======  ============  ==========================

Every report states which model produced its numbers.  A T-count quoted without
its decomposition model is not a physical constant and this project never
presents one as such.

Arbitrary-angle rotations (used only by the QFT adder) have no exact Clifford+T
form at all; they must be *approximated* to a chosen precision, and the cost is
governed by the Ross-Selinger synthesis bound.  That introduces an explicit
``epsilon`` parameter, and any report touching it is labelled accordingly.

References
----------
- M. A. Nielsen & I. L. Chuang, *Quantum Computation and Quantum Information*,
  Fig. 4.9 (the standard 7-T Toffoli).
- P. Selinger, "Quantum circuits of T-depth one", Phys. Rev. A 87, 042302 (2013),
  arXiv:1210.0974.
- C. Jones, "Low-overhead constructions for the fault-tolerant Toffoli gate",
  Phys. Rev. A 87, 022328 (2013), arXiv:1212.5069.
- N. J. Ross & P. Selinger, "Optimal ancilla-free Clifford+T approximation of
  z-rotations", Quantum Inf. Comput. 16, 901 (2016), arXiv:1403.2975.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "ToffoliModel",
    "TOFFOLI_MODELS",
    "get_model",
    "DEFAULT_MODEL",
    "rz_t_count",
    "CLIFFORD_T_BASIS",
    "clifford_t_cost",
]

#: Gate basis used when a circuit is actually transpiled rather than costed
#: analytically.  ``t``/``tdg`` are the only non-Clifford elements.
CLIFFORD_T_BASIS = ["h", "s", "sdg", "t", "tdg", "x", "y", "z", "cx", "cz", "swap"]


@dataclass(frozen=True)
class ToffoliModel:
    """A documented way of expressing a Toffoli gate in Clifford+T."""

    name: str
    t_count: int
    t_depth: int
    ancilla: int
    measurements: int
    clifford_count: int
    reference: str
    notes: str

    def describe(self) -> str:
        return (
            f"{self.name}: T-count {self.t_count}, T-depth {self.t_depth}, "
            f"{self.ancilla} ancilla, {self.measurements} measurement(s) "
            f"[{self.reference}]"
        )


TOFFOLI_MODELS: dict[str, ToffoliModel] = {
    "standard": ToffoliModel(
        name="standard",
        t_count=7,
        t_depth=4,
        ancilla=0,
        measurements=0,
        clifford_count=9,  # 7 CNOT + 2 H
        reference="Nielsen & Chuang Fig. 4.9",
        notes=(
            "The textbook exact decomposition. Ancilla-free and measurement-free, "
            "which makes it the safest default, but its T gates lie on a serial "
            "chain so T-depth is 4. Qiskit's own Toffoli translation reproduces "
            "this circuit exactly (7 T/Tdg, 7 CX, 2 H), which the test suite "
            "checks so the analytical model stays anchored to a real compiler."
        ),
    ),
    "selinger": ToffoliModel(
        name="selinger",
        t_count=7,
        t_depth=1,
        ancilla=4,
        measurements=0,
        clifford_count=21,
        reference="Selinger, Phys. Rev. A 87, 042302 (2013), arXiv:1210.0974",
        notes=(
            "Same T-count as standard, but all seven T gates act in parallel on "
            "distinct qubits, giving T-depth 1 at the cost of 4 extra ancillas. "
            "Worth choosing only when magic-state supply, not qubit count, is "
            "the binding constraint."
        ),
    ),
    "jones": ToffoliModel(
        name="jones",
        t_count=4,
        t_depth=1,
        ancilla=1,
        measurements=1,
        clifford_count=12,
        reference="Jones, Phys. Rev. A 87, 022328 (2013), arXiv:1212.5069",
        notes=(
            "The cheapest of the three in T-count, but it is not a unitary "
            "circuit: it requires a mid-circuit measurement and a classically "
            "controlled correction. Only valid on hardware with fast "
            "measurement and feedforward, and the resulting circuit cannot be "
            "inverted by simple gate reversal."
        ),
    ),
}

DEFAULT_MODEL = "standard"


def get_model(name: str) -> ToffoliModel:
    try:
        return TOFFOLI_MODELS[name]
    except KeyError:
        raise KeyError(
            f"unknown Toffoli model {name!r}; available: {sorted(TOFFOLI_MODELS)}"
        ) from None


def rz_t_count(epsilon: float = 1e-10) -> int:
    """T gates to synthesise one arbitrary Z-rotation to accuracy ``epsilon``.

    Uses the Ross-Selinger bound ``T ~ 3 log2(1/eps) + O(log log(1/eps))``, taken
    here in the commonly quoted form ``3 log2(1/eps) + 10``.

    This is an *approximation cost*, unlike the exact Toffoli decompositions. Any
    report that includes it is flagged ``assumption-dependent`` and carries the
    chosen ``epsilon``, because a different precision target gives a different
    answer and there is no precision-free number to quote.
    """
    if not 0 < epsilon < 1:
        raise ValueError("epsilon must lie in (0, 1)")
    return int(math.ceil(3 * math.log2(1 / epsilon) + 10))


def clifford_t_cost(
    counts: dict[str, int],
    model: str | ToffoliModel = DEFAULT_MODEL,
    epsilon: float = 1e-10,
) -> dict[str, object]:
    """Analytically cost a gate histogram in Clifford+T.

    ``counts`` is a ``{gate_name: count}`` histogram as produced by
    :meth:`~qiskit.QuantumCircuit.count_ops`.

    The returned ``t_depth`` is a **serial upper bound**: it charges every
    Toffoli's T-depth as if no two ever ran concurrently.  Real T-depth is lower
    -- often much lower -- because independent Toffolis parallelise.  The
    analyzer reports a measured T-depth from an actual transpilation whenever
    the circuit is small enough, and labels which one it used.
    """
    model = get_model(model) if isinstance(model, str) else model

    toffoli = counts.get("ccx", 0) + counts.get("ccz", 0)
    rotations = counts.get("cp", 0) + counts.get("p", 0) + counts.get("rz", 0)
    clifford_native = sum(
        counts.get(g, 0) for g in ("x", "y", "z", "h", "s", "sdg", "cx", "cz", "swap")
    )

    t_from_toffoli = toffoli * model.t_count
    per_rotation = rz_t_count(epsilon)
    # A controlled-phase gate decomposes into three single-qubit rotations.
    t_from_rotation = rotations * 3 * per_rotation

    return {
        "toffoli_gates": toffoli,
        "rotation_gates": rotations,
        "t_count": t_from_toffoli + t_from_rotation,
        "t_count_from_toffoli": t_from_toffoli,
        "t_count_from_rotations": t_from_rotation,
        "t_depth_serial_bound": toffoli * model.t_depth + rotations * 3 * per_rotation,
        "clifford_count": clifford_native + toffoli * model.clifford_count,
        "decomposition_ancilla": toffoli * model.ancilla,
        "measurements": toffoli * model.measurements,
        "model": model.name,
        "model_reference": model.reference,
        "rotation_epsilon": epsilon if rotations else None,
        "exact": rotations == 0,
    }
