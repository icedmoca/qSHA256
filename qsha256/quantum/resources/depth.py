"""Circuit depth metrics.

Depth is the most compiler-dependent number this project reports, and the one
most often quoted without qualification.  Every value here is the depth of the
circuit **as qSHA256 constructed it**, under the assumption of all-to-all
connectivity and no routing overhead.  On real hardware with limited
connectivity, routing inserts SWAPs and the depth grows -- sometimes by a large
factor.  That is a layout concern, deliberately outside the logical model, and
it is stated as an assumption in every report.
"""

from __future__ import annotations

from dataclasses import dataclass

from qiskit import QuantumCircuit

__all__ = ["DepthMetrics", "measure_depth"]

_TOFFOLI = frozenset({"ccx", "ccz"})
_T_GATES = frozenset({"t", "tdg"})


@dataclass
class DepthMetrics:
    """Depth measured under several gate filters."""

    total: int
    two_qubit: int
    toffoli: int
    t_depth: int | None = None

    def to_dict(self) -> dict:
        d = {
            "depth": self.total,
            "two_qubit_depth": self.two_qubit,
            "toffoli_depth": self.toffoli,
        }
        if self.t_depth is not None:
            d["t_depth"] = self.t_depth
        return d


def measure_depth(circuit: QuantumCircuit, include_t: bool = False) -> DepthMetrics:
    """Measure logical depth, two-qubit depth and Toffoli depth.

    **Toffoli depth** is the length of the longest chain of dependent Toffoli
    gates.  For a fault-tolerant estimate it is more meaningful than total depth,
    because Clifford layers are cheap relative to magic-state consumption -- it
    is the closest logical proxy for how long the circuit occupies the machine.
    """
    return DepthMetrics(
        total=circuit.depth(),
        two_qubit=circuit.depth(lambda inst: len(inst.qubits) >= 2),
        toffoli=circuit.depth(lambda inst: inst.operation.name in _TOFFOLI),
        t_depth=(circuit.depth(lambda inst: inst.operation.name in _T_GATES) if include_t else None),
    )
