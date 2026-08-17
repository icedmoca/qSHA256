"""Lattice-surgery layout: spacetime volume from an actual floor plan.

The fault-tolerant estimator in :mod:`qsha256.quantum.resources.physical` uses
``2 d^2`` physical qubits per logical qubit and treats runtime as a simple
function of depth.  That is the conventional shortcut, and it quietly assumes a
layout exists in which every operation the circuit wants is available at unit
cost.  On a real surface-code processor it is not: logical operations are
lattice-surgery merges between *adjacent* patches, so what a circuit costs
depends on how the patches are arranged and how much free space is left for
routing.

This module makes that explicit, following Litinski's *A Game of Surface Codes*
(arXiv:1808.02892), which gives concrete floor plans and their tradeoffs:

============  ==========================  ===================================
Layout        Tiles for ``n`` logical     Cost
============  ==========================  ===================================
``compact``   ``1.5 n + 3``               one T gate per 9 code cycles
``intermediate`` ``2 n + 4``              one T gate per 5 code cycles
``fast``      ``2 n + sqrt(8 n) + 1``     one T gate per code cycle
============  ==========================  ===================================

The pattern is the point: buying area buys time, almost linearly.  A circuit
that looks cheap because it has few logical qubits may be expensive because a
compact layout throttles its T gates, and the "best" design changes depending on
which resource is scarce.

Spacetime volume -- tiles multiplied by code cycles -- is the figure that
actually compares circuits, because it prices both. It is reported here in tile
cycles, and converted to physical qubit-seconds once a code distance and cycle
time are fixed.

Everything here is a model with named parameters, like the rest of the
fault-tolerance layer, and every output carries its assumptions.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "LAYOUTS",
    "LayoutEstimate",
    "LayoutModel",
    "compare_layouts",
    "get_layout",
    "lattice_surgery_layout",
]


@dataclass(frozen=True)
class LayoutModel:
    """A surface-code floor plan and its throughput."""

    name: str
    #: Tiles required for ``n`` logical qubits.
    tiles: Any
    #: Code cycles per consumed T state.
    cycles_per_t: float
    description: str
    reference: str = "Litinski, 'A Game of Surface Codes', arXiv:1808.02892"

    def tile_count(self, logical_qubits: int) -> int:
        return math.ceil(self.tiles(logical_qubits))


LAYOUTS: dict[str, LayoutModel] = {
    "compact": LayoutModel(
        name="compact",
        tiles=lambda n: 1.5 * n + 3,
        cycles_per_t=9.0,
        description=(
            "Smallest footprint. Data patches are packed two to a tile column "
            "with a single routing channel, so consecutive T gates contend for "
            "the same corridor and the circuit is throttled to one every nine "
            "code cycles."
        ),
    ),
    "intermediate": LayoutModel(
        name="intermediate",
        tiles=lambda n: 2.0 * n + 4,
        cycles_per_t=5.0,
        description=(
            "One tile per logical qubit plus a wider routing region. Roughly "
            "twice the area of compact for nearly twice the throughput."
        ),
    ),
    "fast": LayoutModel(
        name="fast",
        tiles=lambda n: 2.0 * n + math.sqrt(8.0 * n) + 1,
        cycles_per_t=1.0,
        description=(
            "Enough routing space that a T gate can be consumed every code "
            "cycle, making the circuit reaction-limited rather than "
            "layout-limited. The extra sqrt(8n) tiles are the routing fabric "
            "that buys it."
        ),
    ),
}


def get_layout(name: str) -> LayoutModel:
    try:
        return LAYOUTS[name]
    except KeyError:
        raise KeyError(f"unknown layout {name!r}; available: {sorted(LAYOUTS)}") from None


@dataclass
class LayoutEstimate:
    """Spacetime cost of running a circuit under one floor plan."""

    layout: str
    logical_qubits: int
    t_count: int
    t_depth: int

    tiles: int
    code_distance: int
    physical_qubits_data: int
    physical_qubits_factories: int
    physical_qubits_total: int

    code_cycles: int
    cycles_layout_limited: int
    cycles_reaction_limited: int
    runtime_seconds: float

    #: Tiles multiplied by code cycles: the layout-aware cost measure.
    tile_cycles: float
    spacetime_qubit_seconds: float

    assumptions: list[str] = field(default_factory=list)
    provenance: str = "ASSUMPTION-DEPENDENT"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        return "\n".join(
            [
                f"Lattice-surgery layout: {self.layout}  [ASSUMPTION-DEPENDENT]",
                "=" * 58,
                "",
                f"  logical qubits:        {self.logical_qubits:,}",
                f"  T-count:               {self.t_count:,}",
                f"  T-depth:               {self.t_depth:,}",
                "",
                f"  tiles:                 {self.tiles:,}",
                f"  code distance:         {self.code_distance}",
                f"  physical (data):       {self.physical_qubits_data:,}",
                f"  physical (factories):  {self.physical_qubits_factories:,}",
                f"  physical (total):      {self.physical_qubits_total:,}",
                "",
                f"  cycles, layout-limited:   {self.cycles_layout_limited:,}",
                f"  cycles, reaction-limited: {self.cycles_reaction_limited:,}",
                f"  code cycles (binding):    {self.code_cycles:,}",
                f"  runtime:                  {_human(self.runtime_seconds)}",
                "",
                f"  spacetime volume:      {self.tile_cycles:.4g} tile-cycles",
                f"                         {self.spacetime_qubit_seconds:.4g} qubit-seconds",
                "",
                "Assumptions",
                "-----------",
            ]
            + [f"  * {a}" for a in self.assumptions]
        )


def _human(seconds: float) -> str:
    for unit, size in (("years", 3.156e7), ("days", 86400), ("hours", 3600), ("s", 1)):
        if seconds >= size:
            return f"{seconds / size:,.3g} {unit}"
    return f"{seconds:.3g} s"


def lattice_surgery_layout(
    logical_report,
    layout: str | LayoutModel = "intermediate",
    model: Any = "superconducting",
    target_failure_probability: float = 0.01,
) -> LayoutEstimate:
    """Cost a circuit under a concrete surface-code floor plan.

    The code distance is taken from the same self-consistent solve the physical
    estimator uses, so the two layers agree; what this adds is the tile count
    and the layout-imposed throughput limit, which the simpler model ignores.
    """
    from .physical import choose_code_distance, get_hardware_model

    layout = get_layout(layout) if isinstance(layout, str) else layout
    hardware = get_hardware_model(model) if isinstance(model, str) else model

    logical_qubits = logical_report.width
    t_count = logical_report.t_count
    t_depth = max(
        logical_report.depth.get("non_clifford_depth", 0),
        logical_report.depth.get("toffoli_depth", 0),
        1,
    )
    tiles = layout.tile_count(logical_qubits)

    # A T gate every `cycles_per_t` cycles is what the floor plan permits; the
    # circuit's own T-depth is what it demands. The larger binds.
    cycles_layout = math.ceil(t_count * layout.cycles_per_t)

    distance = None
    cycles = max(1, cycles_layout)
    for _ in range(20):
        candidate = choose_code_distance(tiles, cycles, hardware, target_failure_probability)
        if candidate is None:
            break
        reaction = t_depth * candidate
        new_cycles = max(cycles_layout, reaction, 1)
        if candidate == distance and new_cycles == cycles:
            break
        distance, cycles = candidate, new_cycles

    if distance is None:
        distance = 0
        cycles = cycles_layout

    data_qubits = tiles * distance * distance
    factory_qubits = hardware.factories * hardware.factory_qubits
    reaction = t_depth * distance
    binding = max(cycles_layout, reaction, 1)
    runtime = binding * hardware.cycle_time_seconds

    return LayoutEstimate(
        layout=layout.name,
        logical_qubits=logical_qubits,
        t_count=t_count,
        t_depth=t_depth,
        tiles=tiles,
        code_distance=distance,
        physical_qubits_data=data_qubits,
        physical_qubits_factories=factory_qubits,
        physical_qubits_total=data_qubits + factory_qubits,
        code_cycles=binding,
        cycles_layout_limited=cycles_layout,
        cycles_reaction_limited=reaction,
        runtime_seconds=runtime,
        tile_cycles=float(tiles) * binding,
        spacetime_qubit_seconds=(data_qubits + factory_qubits) * runtime,
        assumptions=[
            f"Floor plan: {layout.name}. {layout.description}",
            f"Tiles for {logical_qubits:,} logical qubits: {tiles:,} ({layout.reference}).",
            f"One tile is d x d = {distance}x{distance} physical qubits.",
            f"Throughput: one T state per {layout.cycles_per_t:g} code cycles, so the "
            f"layout alone imposes {cycles_layout:,} cycles for {t_count:,} T gates.",
            "Runtime is the larger of the layout limit and the circuit's own "
            "reaction limit (T-depth times the code distance).",
            "Routing congestion beyond the floor plan's nominal throughput is not "
            "modelled; a real compiler may do worse.",
            "No hardware was involved. This is a model with named parameters.",
        ],
    )


def compare_layouts(
    logical_report, model: Any = "superconducting", target_failure_probability: float = 0.01
) -> list[LayoutEstimate]:
    """Cost a circuit under every floor plan, for the area/time tradeoff."""
    return [
        lattice_surgery_layout(logical_report, name, model, target_failure_probability)
        for name in LAYOUTS
    ]
