"""The resource analyzer: turning a built circuit into a defensible report.

Everything a report claims is tagged with how it was obtained:

``MEASURED``
    Counted directly off a constructed circuit object.  Qubits, gate counts and
    depths are always measured.

``ANALYTICAL``
    Derived from measured counts through an explicitly documented model -- e.g.
    a T-count obtained by multiplying the measured Toffoli count by a named
    decomposition's T-count.  Reproducible, but model-dependent.

``TRANSPILED``
    Measured off a circuit that a compiler actually rewrote into the target
    basis.  The most faithful, and the most expensive to obtain.  The test suite
    checks TRANSPILED against ANALYTICAL on small circuits so the analytical
    path stays anchored to a real compiler rather than drifting.

``EXTRAPOLATED``
    Projected beyond what was built, always with the scaling rule shown.

The distinction is not decoration.  "SHA-256 needs N T gates" is a different
claim depending on which of these produced N, and conflating them is the single
most common way quantum resource estimates mislead.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import qiskit
from qiskit import QuantumCircuit, transpile

from ...spec import ShaSpec
from ..optimization.strategies import Strategy
from ..registers import CircuitBuilder
from . import clifford_t as ct
from .depth import DepthMetrics, measure_depth
from .gates import GateCounts, aggregate, attribute, count_ops

__all__ = ["ResourceReport", "analyze", "Provenance", "environment_metadata"]


class Provenance:
    MEASURED = "MEASURED"
    ANALYTICAL = "ANALYTICAL"
    TRANSPILED = "TRANSPILED"
    EXTRAPOLATED = "EXTRAPOLATED"
    HYBRID = "HYBRID"


def environment_metadata() -> dict[str, str]:
    """Everything needed to reproduce a benchmark run."""
    from ... import __version__

    return {
        "qsha256_version": __version__,
        "qiskit_version": qiskit.__version__,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@dataclass
class ResourceReport:
    """A complete, self-describing logical resource report."""

    # -- what was analysed -------------------------------------------------
    target: str
    spec_name: str
    word_bits: int
    rounds: int
    strategy: dict[str, Any]

    # -- MEASURED ----------------------------------------------------------
    width: int
    data_qubits: int
    ancilla_qubits: int
    max_live_qubits: int
    gate_counts: dict[str, int]
    total_gates: int
    toffoli_count: int
    cnot_count: int
    two_qubit_count: int
    depth: dict[str, int]

    # -- ANALYTICAL / TRANSPILED ------------------------------------------
    clifford_t: dict[str, Any]
    t_count_provenance: str

    # -- attribution -------------------------------------------------------
    component_costs: dict[str, dict[str, int]] = field(default_factory=dict)

    # -- honesty ------------------------------------------------------------
    provenance: str = Provenance.HYBRID
    simulated: bool = False
    hardware_executed: bool = False
    assumptions: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=environment_metadata)
    reproduce: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def t_count(self) -> int:
        return int(self.clifford_t["t_count"])

    def __str__(self) -> str:
        from .reports import render_text

        return render_text(self)


def analyze(
    source: CircuitBuilder | QuantumCircuit | Any,
    *,
    spec: ShaSpec | None = None,
    strategy: Strategy | None = None,
    rounds: int | None = None,
    target: str = "circuit",
    toffoli_model: str = ct.DEFAULT_MODEL,
    epsilon: float = 1e-10,
    transpile_t: bool | None = None,
    transpile_limit: int = 20_000,
    optimization_level: int = 0,
    simulated: bool = False,
    reproduce: str = "",
) -> ResourceReport:
    """Analyse a circuit and produce a :class:`ResourceReport`.

    Accepts a :class:`~qsha256.quantum.registers.CircuitBuilder`, a raw
    :class:`~qiskit.QuantumCircuit`, or anything with a ``builder`` attribute
    (such as a :class:`~qsha256.quantum.sha256.compression.CompressionCircuit`).

    ``transpile_t`` requests a genuine Clifford+T transpilation to obtain a
    measured T-count and T-depth.  It is expensive -- the decomposed circuit is
    roughly an order of magnitude larger -- so it defaults to running only when
    the circuit has fewer than ``transpile_limit`` instructions.  When it does
    not run, the T-count is analytical and the report says so.
    """
    builder, circuit = _unpack(source)

    counts = count_ops(circuit)
    depth = measure_depth(circuit)

    if builder is not None:
        data_qubits = builder.data_qubits
        ancilla_qubits = builder.ancilla_qubits
        max_live = builder.data_qubits + builder.peak_ancillas
        components = {
            name: gc.to_dict() | {"_total": gc.total, "_ccx": gc.toffoli}
            for name, gc in aggregate(attribute(circuit, builder.sections)).items()
        }
    else:
        data_qubits, ancilla_qubits = circuit.num_qubits, 0
        max_live = circuit.num_qubits
        components = {}

    analytic = ct.clifford_t_cost(counts.counts, toffoli_model, epsilon)

    should_transpile = (
        transpile_t if transpile_t is not None else len(circuit.data) <= transpile_limit
    )
    provenance = Provenance.ANALYTICAL
    if should_transpile:
        measured_ct, tdepth = _transpiled_clifford_t(circuit, optimization_level)
        analytic["t_count_transpiled"] = measured_ct
        analytic["t_depth_transpiled"] = tdepth
        analytic["transpiler"] = f"qiskit {qiskit.__version__} optimization_level={optimization_level}"
        provenance = Provenance.TRANSPILED

    assumptions = _assumptions(analytic, toffoli_model, epsilon, provenance, strategy)

    return ResourceReport(
        target=target,
        spec_name=spec.name if spec else "n/a",
        word_bits=spec.word_bits if spec else 0,
        rounds=rounds if rounds is not None else (spec.rounds if spec else 0),
        strategy=strategy.to_dict() if strategy else {},
        width=circuit.num_qubits,
        data_qubits=data_qubits,
        ancilla_qubits=ancilla_qubits,
        max_live_qubits=max_live,
        gate_counts=counts.to_dict(),
        total_gates=counts.total,
        toffoli_count=counts.toffoli,
        cnot_count=counts.cnot,
        two_qubit_count=counts.two_qubit,
        depth=depth.to_dict(),
        clifford_t=analytic,
        t_count_provenance=provenance,
        component_costs=components,
        provenance=Provenance.HYBRID,
        simulated=simulated,
        hardware_executed=False,
        assumptions=assumptions,
        reproduce=reproduce,
    )


def _unpack(source) -> tuple[CircuitBuilder | None, QuantumCircuit]:
    if isinstance(source, CircuitBuilder):
        return source, source.circuit
    if isinstance(source, QuantumCircuit):
        return None, source
    builder = getattr(source, "builder", None)
    if isinstance(builder, CircuitBuilder):
        return builder, builder.circuit
    raise TypeError(f"cannot analyse {type(source).__name__}")


def _transpiled_clifford_t(circuit: QuantumCircuit, optimization_level: int) -> tuple[int, int]:
    """Actually compile to Clifford+T and count.  Returns ``(t_count, t_depth)``."""
    decomposed = transpile(
        circuit,
        basis_gates=ct.CLIFFORD_T_BASIS,
        optimization_level=optimization_level,
    )
    ops = decomposed.count_ops()
    t_count = ops.get("t", 0) + ops.get("tdg", 0)
    t_depth = decomposed.depth(lambda inst: inst.operation.name in ("t", "tdg"))
    return t_count, t_depth


def _assumptions(
    analytic: dict, model_name: str, epsilon: float, provenance: str, strategy
) -> list[str]:
    model = ct.get_model(model_name)
    out = [
        "Logical resources only: no quantum error correction, no physical qubits, "
        "no magic-state factories are included in these counts.",
        "All-to-all connectivity assumed; no routing or SWAP overhead is charged. "
        "Depth on limited-connectivity hardware will be larger.",
        f"Rotations and shifts are counted as zero-cost wire permutations "
        f"(see qsha256.quantum.primitives.rotate / .shift for why).",
        f"T-count model: {model.describe()}.",
    ]
    if analytic.get("rotation_gates"):
        out.append(
            f"Circuit contains {analytic['rotation_gates']} arbitrary-angle rotations "
            f"(QFT adder). Their T-cost is a Ross-Selinger synthesis estimate at "
            f"epsilon={epsilon:g} ({ct.rz_t_count(epsilon)} T per rotation), not an "
            f"exact decomposition."
        )
    if provenance == Provenance.ANALYTICAL:
        out.append(
            "T-count is ANALYTICAL (measured Toffoli count times the model's T-count); "
            "the circuit was not transpiled at this size. T-depth is a serial upper bound."
        )
    else:
        out.append(
            "T-count and T-depth are TRANSPILED: measured off a circuit the Qiskit "
            "transpiler rewrote into the Clifford+T basis."
        )
    if strategy is not None and getattr(strategy, "uncompute_working", False):
        out.append(
            "Circuit is garbage-free: work registers, message schedule and message "
            "are restored, at the cost of running the forward computation twice."
        )
    else:
        out.append(
            "Circuit is NOT garbage-free: work registers hold intermediate state on "
            "output. This is a forward evaluation only and cannot be used directly "
            "inside a Grover oracle."
        )
    return out
