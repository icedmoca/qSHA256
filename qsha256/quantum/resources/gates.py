"""Gate counting and per-component cost attribution.

Answering "which part of SHA-256 dominates the quantum cost?" requires
attributing gates to the construct that emitted them.  The builder records named
:class:`~qsha256.quantum.registers.Section` spans over the instruction list, so
attribution is exact -- no estimation, no sampling.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from qiskit import QuantumCircuit

from ..registers import Section

__all__ = ["GateCounts", "SectionCost", "aggregate", "attribute", "count_ops", "count_range"]


@dataclass
class GateCounts:
    """A gate histogram with the derived quantities reports actually use."""

    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def toffoli(self) -> int:
        return self.counts.get("ccx", 0) + self.counts.get("ccz", 0)

    @property
    def cnot(self) -> int:
        return self.counts.get("cx", 0)

    @property
    def single_qubit(self) -> int:
        return sum(self.counts.get(g, 0) for g in ("x", "y", "z", "h", "s", "sdg", "t", "tdg"))

    @property
    def two_qubit(self) -> int:
        return sum(self.counts.get(g, 0) for g in ("cx", "cz", "swap", "cp"))

    def get(self, name: str) -> int:
        return self.counts.get(name, 0)

    def to_dict(self) -> dict[str, int]:
        return dict(sorted(self.counts.items()))


def count_ops(circuit: QuantumCircuit) -> GateCounts:
    return GateCounts(dict(circuit.count_ops()))


def count_range(circuit: QuantumCircuit, start: int, end: int) -> GateCounts:
    """Gate histogram over instructions ``[start:end)``."""
    counter: Counter[str] = Counter()
    for inst in circuit.data[start:end]:
        counter[inst.operation.name] += 1
    return GateCounts(dict(counter))


@dataclass
class SectionCost:
    """Cost of a named section, split into its own gates and its children's."""

    name: str
    total: GateCounts
    own: GateCounts
    children: list[SectionCost] = field(default_factory=list)

    def to_dict(self, include_children: bool = True) -> dict:
        d = {
            "name": self.name,
            "gates": self.total.total,
            "ccx": self.total.toffoli,
            "cx": self.total.cnot,
            "counts": self.total.to_dict(),
        }
        if include_children and self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


def attribute(circuit: QuantumCircuit, sections: list[Section]) -> list[SectionCost]:
    """Recursively cost each recorded section."""
    result = []
    for sec in sections:
        end = sec.end if sec.end >= 0 else len(circuit.data)
        total = count_range(circuit, sec.start, end)
        children = attribute(circuit, sec.children)
        own = Counter(total.counts)
        for child in children:
            own.subtract(child.total.counts)
        result.append(
            SectionCost(
                name=sec.name,
                total=total,
                own=GateCounts({k: v for k, v in own.items() if v}),
                children=children,
            )
        )
    return result


def aggregate(costs: list[SectionCost]) -> dict[str, GateCounts]:
    """Flatten the section tree into disjoint, named components.

    Two things matter for this to be an honest breakdown:

    * **Indices are stripped**, so 64 individual ``round[t]`` sections merge into
      one ``round`` entry -- which is what a cost breakdown should show.
    * **Only each section's own gates are counted**, excluding those belonging to
      nested sections.  Without that, a parent and its children would both claim
      the same gates and the percentages would be meaningless.  With it, the
      components are disjoint and sum to the total.
    """
    merged: dict[str, Counter] = {}

    def walk(nodes: list[SectionCost]) -> None:
        for node in nodes:
            key = node.name.split("[")[0].strip()
            if node.own.total:
                merged.setdefault(key, Counter()).update(node.own.counts)
            walk(node.children)

    walk(costs)
    return {k: GateCounts(dict(v)) for k, v in merged.items()}
