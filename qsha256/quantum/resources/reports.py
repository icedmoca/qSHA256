"""Rendering resource reports as text, JSON, CSV and Markdown."""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Iterable, Sequence

from .analyzer import ResourceReport

__all__ = [
    "FORMATS",
    "log2_str",
    "pow2_str",
    "render",
    "render_text",
    "to_csv",
    "to_json",
    "to_markdown",
]


def log2_str(value: float) -> str:
    """``2^x`` notation for quantities too large to print in full."""
    if value <= 0:
        return "0"
    return f"2^{math.log2(value):.1f}"


def pow2_str(value: float, threshold: float = 1e9) -> str:
    """Print exactly when small, in powers of two when astronomically large."""
    if value < threshold:
        return f"{int(value):,}"
    return f"~{log2_str(value)}"


def _fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def render_text(report: ResourceReport) -> str:
    """Human-readable report, in the style the CLI prints."""
    out = io.StringIO()
    w = out.write

    w("qSHA256 - Quantum Resource Analysis\n")
    w("=" * 60 + "\n\n")

    w("Target\n------\n")
    w(f"  Circuit:              {report.target}\n")
    w(f"  Spec:                 {report.spec_name}\n")
    w(f"  Word size:            {report.word_bits} bits\n")
    w(f"  Rounds:               {report.rounds}\n")
    if report.strategy:
        w("\nArchitecture\n------------\n")
        for key in ("adder", "const_add", "schedule", "round_layout", "uncompute_working"):
            if key in report.strategy:
                w(f"  {key + ':':22s}{report.strategy[key]}\n")

    w("\nLogical Resources  [MEASURED]\n")
    w("-" * 30 + "\n")
    w(f"  Circuit width:        {_fmt(report.width)}\n")
    w(f"  Data qubits:          {_fmt(report.data_qubits)}\n")
    w(f"  Ancilla qubits:       {_fmt(report.ancilla_qubits)}\n")
    w(f"  Max live qubits:      {_fmt(report.max_live_qubits)}\n")
    w(f"  Total gates:          {_fmt(report.total_gates)}\n")
    for name, count in sorted(report.gate_counts.items(), key=lambda kv: -kv[1]):
        w(f"    {name + ':':20s}{_fmt(count)}\n")
    w(f"  Toffoli (CCX):        {_fmt(report.toffoli_count)}\n")
    w(f"  Two-qubit gates:      {_fmt(report.two_qubit_count)}\n")
    w(f"  Depth:                {_fmt(report.depth['depth'])}\n")
    w(f"  Two-qubit depth:      {_fmt(report.depth['two_qubit_depth'])}\n")
    w(f"  Toffoli depth:        {_fmt(report.depth['toffoli_depth'])}\n")

    ctd = report.clifford_t
    w(f"\nClifford+T  [{report.t_count_provenance}]\n")
    w("-" * 30 + "\n")
    w(f"  Decomposition model:  {ctd['model']}\n")
    w(f"  Reference:            {ctd['model_reference']}\n")
    w(f"  T-count (analytical): {_fmt(ctd['t_count'])}\n")
    if "t_count_transpiled" in ctd:
        w(f"  T-count (transpiled): {_fmt(ctd['t_count_transpiled'])}\n")
        w(f"  T-depth (transpiled): {_fmt(ctd['t_depth_transpiled'])}\n")
        w(f"  Transpiler:           {ctd['transpiler']}\n")
    else:
        w(f"  T-depth (upper bound):{_fmt(ctd['t_depth_serial_bound'])}\n")
    w(f"  Clifford gates:       {_fmt(ctd['clifford_count'])}\n")
    if ctd.get("decomposition_ancilla"):
        w(f"  Decomposition ancilla:{_fmt(ctd['decomposition_ancilla'])}\n")
    if ctd.get("measurements"):
        w(f"  Measurements:         {_fmt(ctd['measurements'])}\n")

    if report.component_costs:
        w("\nCost by Component  [MEASURED]\n")
        w("-" * 30 + "\n")
        rows = sorted(report.component_costs.items(), key=lambda kv: -kv[1].get("_ccx", 0))
        total_ccx = max(1, report.toffoli_count)
        w(f"  {'component':<34}{'gates':>12}{'ccx':>10}{'ccx %':>8}\n")
        for name, data in rows:
            w(
                f"  {name:<34}{data.get('_total', 0):>12,}{data.get('_ccx', 0):>10,}"
                f"{100 * data.get('_ccx', 0) / total_ccx:>7.1f}%\n"
            )

    w("\nStatus\n------\n")
    w("  Circuit constructed:  yes\n")
    w(f"  Circuit simulated:    {'yes' if report.simulated else 'no'}\n")
    w(f"  Run on hardware:      {'yes' if report.hardware_executed else 'no'}\n")

    w("\nAssumptions\n-----------\n")
    for item in report.assumptions:
        w(f"  * {item}\n")

    env = report.environment
    w("\nEnvironment\n-----------\n")
    for key in sorted(env):
        w(f"  {key + ':':22s}{env[key]}\n")

    if report.reproduce:
        w(f"\nReproduce\n---------\n  {report.reproduce}\n")

    return out.getvalue()


def to_json(reports: ResourceReport | Sequence[ResourceReport], indent: int = 2) -> str:
    if isinstance(reports, ResourceReport):
        return json.dumps(reports.to_dict(), indent=indent, default=str)
    return json.dumps([r.to_dict() for r in reports], indent=indent, default=str)


#: Columns used by the CSV and Markdown table renderers.
TABLE_COLUMNS = [
    ("spec", lambda r: r.spec_name),
    ("target", lambda r: r.target),
    ("rounds", lambda r: r.rounds),
    ("strategy", lambda r: r.strategy.get("label", "")),
    ("width", lambda r: r.width),
    ("data_qubits", lambda r: r.data_qubits),
    ("ancilla_qubits", lambda r: r.ancilla_qubits),
    ("max_live_qubits", lambda r: r.max_live_qubits),
    ("total_gates", lambda r: r.total_gates),
    ("ccx", lambda r: r.toffoli_count),
    ("cx", lambda r: r.cnot_count),
    ("depth", lambda r: r.depth["depth"]),
    ("toffoli_depth", lambda r: r.depth["toffoli_depth"]),
    ("t_count", lambda r: r.t_count),
    ("t_count_provenance", lambda r: r.t_count_provenance),
]


def to_csv(reports: Iterable[ResourceReport]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([name for name, _ in TABLE_COLUMNS])
    for report in reports:
        writer.writerow([fn(report) for _, fn in TABLE_COLUMNS])
    return buf.getvalue()


def to_markdown(reports: Iterable[ResourceReport], title: str = "") -> str:
    reports = list(reports)
    cols = [
        ("Circuit", lambda r: r.target),
        ("Rounds", lambda r: f"{r.rounds}"),
        ("Strategy", lambda r: r.strategy.get("label", "-")),
        ("Qubits", lambda r: f"{r.width:,}"),
        ("Ancilla", lambda r: f"{r.ancilla_qubits:,}"),
        ("Gates", lambda r: f"{r.total_gates:,}"),
        ("CCX", lambda r: f"{r.toffoli_count:,}"),
        ("Depth", lambda r: f"{r.depth['depth']:,}"),
        ("T-count", lambda r: f"{r.t_count:,}"),
        ("T src", lambda r: r.t_count_provenance[:4]),
    ]
    out = io.StringIO()
    if title:
        out.write(f"### {title}\n\n")
    out.write("| " + " | ".join(name for name, _ in cols) + " |\n")
    out.write("|" + "|".join("---:" if i else "---" for i in range(len(cols))) + "|\n")
    for report in reports:
        out.write("| " + " | ".join(fn(report) for _, fn in cols) + " |\n")
    return out.getvalue()


FORMATS = {
    "text": lambda r: (
        render_text(r) if isinstance(r, ResourceReport) else "\n".join(map(render_text, r))
    ),
    "json": to_json,
    "csv": lambda r: to_csv([r] if isinstance(r, ResourceReport) else r),
    "markdown": lambda r: to_markdown([r] if isinstance(r, ResourceReport) else r),
}


def render(reports, fmt: str = "text") -> str:
    try:
        return FORMATS[fmt](reports)
    except KeyError:
        raise KeyError(f"unknown format {fmt!r}; available: {sorted(FORMATS)}") from None
