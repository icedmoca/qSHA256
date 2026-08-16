"""Hardware-aware design ranking.

The Pareto front in :mod:`qsha256.quantum.optimization.search` answers "which
designs are not strictly worse than some other design".  It cannot answer "which
design should I actually use", because that depends on what the machine is short
of.  A design that spends qubits to buy depth is a bargain on a machine with
plenty of qubits and a tight runtime budget, and a disaster on a small one.

So this module scores designs against a concrete
:class:`~qsha256.quantum.resources.physical.HardwareModel` using **spacetime
volume** -- physical qubits multiplied by runtime -- which is the standard way
to compare fault-tolerant circuits, because it captures the fact that occupying
a million qubits for an hour and a thousand qubits for a thousand hours are
comparably expensive.

The important consequence, and one the benchmark demonstrates rather than
asserts: **the winning design changes with the machine.**  Ranking circuits by
logical gate count alone can pick the wrong one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..resources.physical import (
    HardwareModel,
    PhysicalEstimate,
    estimate_physical,
    get_hardware_model,
)

__all__ = ["HardwareRanking", "ScoredDesign", "rank_for_hardware"]


@dataclass
class ScoredDesign:
    """A design plus its physical estimate on one machine."""

    label: str
    point: object
    estimate: PhysicalEstimate

    @property
    def spacetime_volume(self) -> float:
        """Physical qubits times runtime, in qubit-seconds.  Lower is better."""
        if not self.estimate.achievable:
            return float("inf")
        return self.estimate.physical_qubits_total * self.estimate.runtime_seconds

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "achievable": self.estimate.achievable,
            "code_distance": self.estimate.code_distance,
            "physical_qubits": self.estimate.physical_qubits_total,
            "runtime_seconds": self.estimate.runtime_seconds,
            "spacetime_qubit_seconds": (
                None if self.spacetime_volume == float("inf") else self.spacetime_volume
            ),
        }


@dataclass
class HardwareRanking:
    model_name: str
    scored: list[ScoredDesign] = field(default_factory=list)

    @property
    def best(self) -> ScoredDesign | None:
        achievable = [s for s in self.scored if s.estimate.achievable]
        return min(achievable, key=lambda s: s.spacetime_volume) if achievable else None

    def to_dict(self) -> dict:
        return {
            "hardware_model": self.model_name,
            "best": self.best.label if self.best else None,
            "designs": [s.to_dict() for s in self.scored],
        }

    def __str__(self) -> str:
        header = f"{'design':<44}{'d':>4}{'phys. qubits':>16}{'runtime':>16}{'qubit-seconds':>18}"
        lines = [
            f"Hardware-Aware Ranking - {self.model_name}  [ASSUMPTION-DEPENDENT]",
            "=" * len(header),
            "",
            header,
            "-" * len(header),
        ]
        for scored in sorted(self.scored, key=lambda s: s.spacetime_volume):
            est = scored.estimate
            if not est.achievable:
                lines.append(f"{scored.label:<44}{'-':>4}{'not achievable':>16}")
                continue
            lines.append(
                f"{scored.label:<44}{est.code_distance:>4}"
                f"{est.physical_qubits_total:>16,}"
                f"{_short_time(est.runtime_seconds):>16}"
                f"{scored.spacetime_volume:>18.3g}"
            )
        best = self.best
        if best:
            lines += [
                "",
                f"Best for this machine: {best.label}",
                "",
                "Ranked by spacetime volume (physical qubits x runtime). A different "
                "machine can and does reorder this list -- see the benchmark, which "
                "runs the same designs against several models.",
            ]
        return "\n".join(lines)


def _short_time(seconds: float) -> str:
    for unit, size in (("y", 3.156e7), ("d", 86400), ("h", 3600), ("s", 1)):
        if seconds >= size:
            return f"{seconds / size:,.3g}{unit}"
    return f"{seconds:.3g}s"


def rank_for_hardware(
    points: Sequence,
    model: HardwareModel | str = "superconducting",
    target_failure_probability: float = 0.01,
) -> HardwareRanking:
    """Score every design against one machine and rank by spacetime volume.

    ``points`` is a sequence of
    :class:`~qsha256.quantum.optimization.search.DesignPoint`.
    """
    model = get_hardware_model(model) if isinstance(model, str) else model
    scored = [
        ScoredDesign(
            label=getattr(point, "label", str(point)),
            point=point,
            estimate=estimate_physical(point.report, model, target_failure_probability),
        )
        for point in points
    ]
    return HardwareRanking(model_name=model.name, scored=scored)
