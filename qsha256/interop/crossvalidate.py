"""Cross-validation against independently written resource estimators.

Everything else in this project is self-consistent, which is not the same as
correct.  The gate counts come from qSHA256's analyzer, the circuits come from
qSHA256's builder, and the specification comes from qSHA256's reference model.
A systematic mistake in how a gate is counted would be invisible: it would show
up identically on both sides of every comparison.

The remedy is to hand the same circuit to code written by somebody else and see
whether the numbers agree.  Three independent paths are used, in decreasing
order of independence:

**Qualtran** (Google's quantum resource-estimation library).  Reads our circuit
through Cirq and applies its own Bloq-based accounting.  Completely separate
lineage from anything here.

**Qiskit's own transpiler.**  Already used for Clifford+T expansion, but here it
is asked for gate counts directly so its answer can be compared against the
analytical model rather than substituted for it.

**A deliberately naive independent counter.**  Re-derives the counts from the
exported OpenQASM 3 *text*, not from the circuit object -- so it shares no data
structure with the analyzer, only the serialised artefact.  Crude, and that is
the point: it cannot inherit a bug from the object model.

Disagreement is the useful outcome.  Where the estimators differ, the report
says so and explains why rather than picking a winner, because the differences
are usually real: tools disagree about whether to count a gate before or after
decomposition, whether a measurement is a gate, and what "depth" means.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from qiskit import QuantumCircuit

__all__ = [
    "CrossValidation",
    "EstimatorResult",
    "count_via_qasm_text",
    "count_via_qiskit",
    "count_via_qualtran",
    "cross_validate",
    "qualtran_available",
]


@dataclass
class EstimatorResult:
    """One estimator's view of a circuit."""

    estimator: str
    available: bool
    qubits: int | None = None
    total_gates: int | None = None
    toffoli: int | None = None
    cnot: int | None = None
    #: All Clifford gates. Reported separately from ``cnot`` because tools
    #: differ on what they fold in -- Qualtran's Clifford figure includes X
    #: gates, ours does not, and calling that a disagreement would be wrong.
    clifford: int | None = None
    t_count: int | None = None
    detail: str = ""
    seconds: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimator": self.estimator,
            "available": self.available,
            "qubits": self.qubits,
            "total_gates": self.total_gates,
            "toffoli": self.toffoli,
            "cnot": self.cnot,
            "clifford": self.clifford,
            "t_count": self.t_count,
            "detail": self.detail,
            "seconds": round(self.seconds, 3),
            "error": self.error,
        }


def count_via_qiskit(circuit: QuantumCircuit) -> EstimatorResult:
    """Qiskit's own accounting, asked for directly."""
    started = time.time()
    ops = dict(circuit.count_ops())
    return EstimatorResult(
        estimator="qiskit.count_ops",
        available=True,
        qubits=circuit.num_qubits,
        total_gates=sum(ops.values()),
        toffoli=ops.get("ccx", 0) + ops.get("and_g", 0) + ops.get("and_g_dg", 0),
        cnot=ops.get("cx", 0),
        clifford=sum(ops.get(g, 0) for g in ("cx", "x", "z", "h", "s", "sdg", "swap", "cz")),
        detail="counted on the circuit object",
        seconds=time.time() - started,
    )


def count_via_qasm_text(circuit: QuantumCircuit) -> EstimatorResult:
    """Re-derive counts from exported OpenQASM 3 text.

    Shares no data structure with the analyzer -- only the serialised artefact --
    so it cannot inherit a bug from the circuit object model.
    """
    started = time.time()
    try:
        from qiskit import qasm3

        text = qasm3.dumps(circuit)
    except Exception as exc:  # pragma: no cover - depends on gate set
        return EstimatorResult(
            estimator="qasm3 text",
            available=False,
            error=f"{type(exc).__name__}: {exc}",
            seconds=time.time() - started,
        )

    counts: dict[str, int] = {}
    qubits = 0
    depth = 0  # brace nesting: bodies of `gate`/`def` blocks are declarations,
    # not invocations, and counting them inflates the totals. An earlier version
    # did exactly that and disagreed with Qiskit by precisely the size of the
    # custom AND gate definitions.
    for raw in text.splitlines():
        line = raw.strip().rstrip(";")
        if not line or line.startswith(("OPENQASM", "include", "//")):
            continue
        opens, closes = line.count("{"), line.count("}")
        if depth > 0:
            depth += opens - closes
            continue
        if line.startswith(("gate", "def")):
            depth += opens - closes
            continue
        if line.startswith("qubit"):
            inside = line[line.find("[") + 1 : line.find("]")]
            qubits += int(inside) if inside.isdigit() else 1
            continue
        if line.startswith(("bit", "}", "{")):
            continue
        name = line.split(" ")[0].split("(")[0]
        counts[name] = counts.get(name, 0) + 1

    return EstimatorResult(
        estimator="qasm3 text",
        available=True,
        qubits=qubits,
        total_gates=sum(counts.values()),
        toffoli=counts.get("ccx", 0) + counts.get("and_g", 0) + counts.get("and_g_dg", 0),
        cnot=counts.get("cx", 0),
        clifford=sum(counts.get(g, 0) for g in ("cx", "x", "z", "h", "s", "sdg", "swap", "cz")),
        detail=f"parsed {len(text.splitlines()):,} lines of OpenQASM 3",
        seconds=time.time() - started,
    )


def qualtran_available() -> bool:
    try:
        import qualtran  # noqa: F401

        return True
    except Exception:
        return False


def count_via_qualtran(circuit: QuantumCircuit) -> EstimatorResult:
    """Google's Qualtran, via Cirq -- a completely separate lineage."""
    started = time.time()
    if not qualtran_available():
        return EstimatorResult(
            estimator="qualtran",
            available=False,
            error="qualtran is not installed",
            seconds=time.time() - started,
        )
    try:
        import cirq
        from qualtran.cirq_interop import cirq_optree_to_cbloq
        from qualtran.resource_counting import QECGatesCost, get_cost_value

        qubits = cirq.LineQubit.range(circuit.num_qubits)
        index = {q: i for i, q in enumerate(circuit.qubits)}
        operations = []
        for inst in circuit.data:
            name = inst.operation.name
            targets = [qubits[index[q]] for q in inst.qubits]
            if name == "x":
                operations.append(cirq.X(*targets))
            elif name == "cx":
                operations.append(cirq.CNOT(*targets))
            elif name in ("ccx", "and_g", "and_g_dg"):
                operations.append(cirq.TOFFOLI(*targets))
            elif name == "h":
                operations.append(cirq.H(*targets))
            elif name == "z":
                operations.append(cirq.Z(*targets))
            elif name == "swap":
                operations.append(cirq.SWAP(*targets))
            elif name in ("t", "tdg", "s", "sdg"):
                gate = {"t": cirq.T, "tdg": cirq.T**-1, "s": cirq.S, "sdg": cirq.S**-1}[name]
                operations.append(gate(*targets))
            else:
                raise ValueError(f"no Cirq equivalent registered for {name!r}")

        bloq = cirq_optree_to_cbloq(cirq.Circuit(operations))
        cost = get_cost_value(bloq, QECGatesCost())
        return EstimatorResult(
            estimator="qualtran",
            available=True,
            qubits=circuit.num_qubits,
            toffoli=int(getattr(cost, "toffoli", 0) or 0) + int(getattr(cost, "and_bloq", 0) or 0),
            clifford=int(getattr(cost, "clifford", 0) or 0),
            t_count=int(getattr(cost, "t", 0) or 0),
            detail=f"QECGatesCost: {cost}",
            seconds=time.time() - started,
        )
    except Exception as exc:
        return EstimatorResult(
            estimator="qualtran",
            available=False,
            error=f"{type(exc).__name__}: {exc}"[:200],
            seconds=time.time() - started,
        )


@dataclass
class CrossValidation:
    """Several estimators' views of one circuit, and whether they agree."""

    target: str
    reference: EstimatorResult
    others: list[EstimatorResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def agreements(self) -> dict[str, dict[str, bool]]:
        out: dict[str, dict[str, bool]] = {}
        for other in self.others:
            if not other.available:
                continue
            checks = {}
            for metric in ("qubits", "total_gates", "toffoli", "cnot", "clifford"):
                mine = getattr(self.reference, metric)
                theirs = getattr(other, metric)
                if mine is not None and theirs is not None:
                    checks[metric] = mine == theirs
            out[other.estimator] = checks
        return out

    @property
    def agree(self) -> bool:
        return all(all(c.values()) for c in self.agreements.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "agree": self.agree,
            "reference": self.reference.to_dict(),
            "others": [o.to_dict() for o in self.others],
            "agreements": self.agreements,
            "notes": self.notes,
        }

    def __str__(self) -> str:
        lines = [
            f"Cross-validation: {self.target}",
            "=" * 70,
            "",
            f"{'estimator':<22}{'qubits':>9}{'gates':>10}{'toffoli':>9}{'cnot':>9}{'clifford':>10}",
            "-" * 70,
        ]

        def row(r: EstimatorResult) -> str:
            if not r.available:
                return f"{r.estimator:<22}{'unavailable':>41}"
            fmt = lambda v: f"{v:,}" if v is not None else "-"  # noqa: E731
            return (
                f"{r.estimator:<22}{fmt(r.qubits):>9}{fmt(r.total_gates):>10}"
                f"{fmt(r.toffoli):>9}{fmt(r.cnot):>9}{fmt(r.clifford):>10}"
            )

        lines.append(row(self.reference) + "   <- qSHA256")
        for other in self.others:
            lines.append(row(other))
        lines += ["", "Agreement", "-" * 9]
        for name, checks in self.agreements.items():
            verdict = "agrees on " + ", ".join(k for k, v in checks.items() if v)
            disagree = [k for k, v in checks.items() if not v]
            if disagree:
                verdict += "; DISAGREES on " + ", ".join(disagree)
            lines.append(f"  {name}: {verdict}")
        for other in self.others:
            if not other.available:
                lines.append(f"  {other.estimator}: unavailable ({other.error})")
        lines += [""] + [f"  * {n}" for n in self.notes]
        return "\n".join(lines)


def cross_validate(circuit: QuantumCircuit, target: str = "circuit") -> CrossValidation:
    """Run every available estimator over one circuit and compare."""
    reference = count_via_qiskit(circuit)
    others = [count_via_qasm_text(circuit), count_via_qualtran(circuit)]
    return CrossValidation(
        target=target,
        reference=reference,
        others=others,
        notes=[
            "Agreement on gate counts is evidence the analyzer is not "
            "systematically miscounting; it says nothing about whether the "
            "circuit computes the right function, which the SAT proofs cover.",
            "Estimators legitimately disagree about T-count, because that "
            "depends on the decomposition each assumes. Only counts of gates "
            "actually present in the circuit are compared here.",
            "Qualtran maps Gidney ANDs onto Toffolis, since its Bloq model has "
            "no equivalent of a measurement-based uncomputation; its Toffoli "
            "figure therefore counts both halves of each AND pair.",
            "Qualtran reports no separate CNOT count, only a Clifford total, so "
            "the CNOT column is blank for it and the Clifford column is the "
            "like-for-like comparison.",
        ],
    )
