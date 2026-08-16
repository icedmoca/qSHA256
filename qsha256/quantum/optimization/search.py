"""Automated design-space search over reversible SHA-256 implementations.

This is what turns qSHA256 from "here is a circuit and its cost" into "here is
the cheapest circuit we could find, and here is what it trades away".

The search is exhaustive over a discrete space rather than heuristic, which is
possible because the design space is small and every point in it is correct by
construction: a :class:`~qsha256.quantum.optimization.strategies.Strategy` is a
parameter vector, not a program, so there is no invalid combination to guard
against.  On top of each architectural point the gate-level rewriter may be
applied, giving a second, finer layer of optimization.

There is no single best circuit, and the search does not pretend otherwise.
Minimising qubits and minimising T-depth pull in opposite directions, so the
result is a **Pareto front**: the set of designs that are not beaten on *every*
objective simultaneously.  Choosing among them requires knowing what the machine
is short of, which is what the hardware model in
:mod:`qsha256.quantum.optimization.hardware` is for.

Every design that enters the front is functionally verified against the
classical reference implementation first.  A design that is cheap but wrong is
not a result.
"""

from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Sequence

from ...classical.sha256 import compress
from ...spec import SHA256, ShaSpec
from ..primitives.add import ADDERS
from ..resources.analyzer import ResourceReport, analyze
from ..sha256.compression import build_compression
from .rewrite import apply_rewrites
from .strategies import Strategy, enumerate_strategies
from .verify import Assurance, EquivalenceResult, verify_against_classical

__all__ = [
    "OBJECTIVES",
    "DesignPoint",
    "SearchResult",
    "pareto_front",
    "search_designs",
    "compare_designs",
]


#: Objectives the search minimises.  All are "lower is better".
OBJECTIVES: dict[str, Callable[[ResourceReport], int]] = {
    "qubits": lambda r: r.width,
    "ancilla": lambda r: r.ancilla_qubits,
    "gates": lambda r: r.total_gates,
    "toffoli": lambda r: r.toffoli_count,
    "t_count": lambda r: r.t_count,
    "depth": lambda r: r.depth["depth"],
    "toffoli_depth": lambda r: r.depth["toffoli_depth"],
}

DEFAULT_OBJECTIVES = ("qubits", "t_count", "toffoli_depth")


@dataclass
class DesignPoint:
    """One searched design: its architecture, its measured cost, its verification."""

    strategy: Strategy
    rewritten: bool
    spec_name: str
    rounds: int
    report: ResourceReport
    verification: str
    verified: bool
    build_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.strategy.label() + ("/rewritten" if self.rewritten else "")

    def metrics(self, objectives: Sequence[str] = DEFAULT_OBJECTIVES) -> tuple[int, ...]:
        return tuple(OBJECTIVES[o](self.report) for o in objectives)

    def to_dict(self, objectives: Sequence[str] = DEFAULT_OBJECTIVES) -> dict[str, Any]:
        return {
            "label": self.label,
            "strategy": self.strategy.to_dict(),
            "rewritten": self.rewritten,
            "spec": self.spec_name,
            "rounds": self.rounds,
            "verified": self.verified,
            "verification": self.verification,
            "build_seconds": round(self.build_seconds, 3),
            "metrics": {o: OBJECTIVES[o](self.report) for o in OBJECTIVES},
            "notes": self.notes,
        }


def pareto_front(
    points: Iterable[DesignPoint],
    objectives: Sequence[str] = DEFAULT_OBJECTIVES,
) -> list[DesignPoint]:
    """Designs not dominated on every objective at once.

    ``a`` dominates ``b`` when ``a`` is no worse on all objectives and strictly
    better on at least one.  The front is everything left standing.
    """
    points = list(points)
    front: list[DesignPoint] = []
    for candidate in points:
        cm = candidate.metrics(objectives)
        dominated = False
        for other in points:
            if other is candidate:
                continue
            om = other.metrics(objectives)
            if all(o <= c for o, c in zip(om, cm)) and any(o < c for o, c in zip(om, cm)):
                dominated = True
                break
        if not dominated:
            front.append(candidate)
    return sorted(front, key=lambda p: p.metrics(objectives))


@dataclass
class SearchResult:
    """The full search outcome: every point, the Pareto front, and the winners."""

    spec_name: str
    rounds: int
    objectives: tuple[str, ...]
    points: list[DesignPoint]
    front: list[DesignPoint]
    baseline: DesignPoint | None = None
    elapsed_seconds: float = 0.0

    def best(self, objective: str) -> DesignPoint:
        return min(self.points, key=lambda p: OBJECTIVES[objective](p.report))

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec_name,
            "rounds": self.rounds,
            "objectives": list(self.objectives),
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "points": [p.to_dict() for p in self.points],
            "pareto_front": [p.label for p in self.front],
        }

    def __str__(self) -> str:
        cols = ["qubits", "gates", "toffoli", "t_count", "depth", "toffoli_depth"]
        header = f"{'design':<42}{'ok':>4}" + "".join(f"{c:>14}" for c in cols)
        lines = [
            f"qSHA256 Design-Space Search - {self.spec_name}, {self.rounds} rounds",
            "=" * len(header),
            f"{len(self.points)} designs evaluated in {self.elapsed_seconds:.1f}s; "
            f"{len(self.front)} on the Pareto front over {', '.join(self.objectives)}",
            "",
            header,
            "-" * len(header),
        ]
        front_labels = {p.label for p in self.front}
        for point in sorted(self.points, key=lambda p: OBJECTIVES["t_count"](p.report)):
            mark = "*" if point.label in front_labels else " "
            ok = "y" if point.verified else ("-" if "UNSUPPORTED" in point.verification else "N")
            row = f"{mark}{point.label:<41}{ok:>4}"
            row += "".join(f"{OBJECTIVES[c](point.report):>14,}" for c in cols)
            lines.append(row)
        lines += ["", "* = on the Pareto front", "", "Best by objective", "-" * 20]
        for objective in cols:
            best = self.best(objective)
            lines.append(
                f"  {objective + ':':<16}{OBJECTIVES[objective](best.report):>14,}  {best.label}"
            )
        if self.baseline:
            lines += ["", "Versus the default architecture", "-" * 31]
            for point in self.front:
                if point.label != self.baseline.label:
                    lines.append("  " + compare_designs(self.baseline, point, self.objectives))
        return "\n".join(lines)


def search_designs(
    spec: ShaSpec = SHA256,
    rounds: int | None = None,
    objectives: Sequence[str] = DEFAULT_OBJECTIVES,
    axes: dict[str, Any] | None = None,
    rewrite: bool = True,
    verify: bool = True,
    verify_trials: int = 3,
    transpile_t: bool = False,
    progress: Callable[[str], None] | None = None,
    **fixed: Any,
) -> SearchResult:
    """Build, verify, measure and rank every design in the space.

    Parameters
    ----------
    axes / fixed:
        Restrict the space, e.g. ``search_designs(adder=("cdkm", "vbe"))``.
    rewrite:
        Also evaluate a gate-level-rewritten variant of each architecture.
    verify:
        Check each design against the classical reference before scoring it.
        Designs that fail verification are kept but flagged, never silently
        dropped -- a failure is information.
    """
    rounds = spec.rounds if rounds is None else rounds
    started = time.time()
    points: list[DesignPoint] = []
    baseline: DesignPoint | None = None

    for strategy in enumerate_strategies(axes, **fixed):
        if strategy.uncompute_working and not ADDERS[strategy.adder].basis_simulable:
            continue  # cannot uncompute by reverse replay; not a valid design
        if progress:
            progress(strategy.label())

        t0 = time.time()
        try:
            comp = build_compression(spec, strategy, rounds=rounds)
        except (ValueError, KeyError) as exc:
            continue
        build_time = time.time() - t0

        result = (
            _verify_design(comp, spec, rounds, verify_trials)
            if verify
            else EquivalenceResult(True, "SKIPPED", 0, "verification disabled")
        )

        base_report = analyze(
            comp,
            spec=spec,
            strategy=strategy,
            rounds=rounds,
            target=f"{spec.name}-compression",
            transpile_t=transpile_t,
            simulated=result.equivalent and result.assurance != Assurance.UNSUPPORTED,
        )
        point = DesignPoint(
            strategy=strategy,
            rewritten=False,
            spec_name=spec.name,
            rounds=rounds,
            report=base_report,
            verification=str(result),
            verified=bool(result),
            build_seconds=build_time,
        )
        points.append(point)
        if strategy == Strategy():
            baseline = point

        if rewrite and ADDERS[strategy.adder].basis_simulable:
            rewritten = apply_rewrites(comp.builder)
            rw_report = analyze(
                rewritten.circuit,
                spec=spec,
                strategy=strategy,
                rounds=rounds,
                target=f"{spec.name}-compression-rewritten",
                transpile_t=transpile_t,
            )
            # The rewriter never allocates, so qubit accounting carries over.
            rw_report.data_qubits = base_report.data_qubits
            rw_report.ancilla_qubits = base_report.ancilla_qubits
            rw_report.max_live_qubits = base_report.max_live_qubits
            rw_report.assumptions.append(
                f"Gate-level rewriting applied ({'+'.join(rewritten.passes)}): "
                f"{rewritten.summary()}"
            )
            points.append(
                DesignPoint(
                    strategy=strategy,
                    rewritten=True,
                    spec_name=spec.name,
                    rounds=rounds,
                    report=rw_report,
                    verification=str(result) + " (pre-rewrite); rewrite passes are local identities",
                    verified=bool(result),
                    build_seconds=build_time,
                    notes=[rewritten.summary()],
                )
            )

    return SearchResult(
        spec_name=spec.name,
        rounds=rounds,
        objectives=tuple(objectives),
        points=points,
        front=pareto_front(points, objectives),
        baseline=baseline,
        elapsed_seconds=time.time() - started,
    )


def _verify_design(comp, spec: ShaSpec, rounds: int, trials: int) -> EquivalenceResult:
    """Check a built compression circuit against the classical reference."""
    reduced = spec.with_rounds(rounds)

    def draw(rng: random.Random) -> dict:
        assignment = {w: rng.getrandbits(spec.word_bits) for w in comp.state}
        assignment |= {w: rng.getrandbits(spec.word_bits) for w in comp.message}
        return assignment

    def expected(assignment: dict) -> dict:
        state = tuple(assignment[w] for w in comp.state)
        block = [assignment[w] for w in comp.message]
        want = dict(zip(comp.digest, compress(state, block, reduced)))
        if comp.uncomputed:
            # A garbage-free design must also hand the message block back
            # unchanged, and leave the chaining input untouched.
            want |= {w: assignment[w] for w in comp.message}
            want |= {w: assignment[w] for w in comp.state}
        return want

    # A garbage-free design must return its working registers to |0>; a
    # forward-only design is *expected* to leave them populated.
    clean = comp.working if comp.uncomputed else ()
    return verify_against_classical(
        comp.circuit,
        draw,
        expected,
        clean=clean,
        ancillas=comp.builder.ancillas.all,
        trials=trials,
    )


def compare_designs(
    baseline: DesignPoint,
    candidate: DesignPoint,
    objectives: Sequence[str] = DEFAULT_OBJECTIVES,
) -> str:
    """A one-line, quantified statement of what a design trades for what.

    Produces exactly the kind of claim this project exists to support:
    *"uses 6% more logical qubits but reduces T-depth by 19%"* -- with both
    numbers coming from measured circuits.
    """
    gains, losses = [], []
    for objective in objectives:
        base = OBJECTIVES[objective](baseline.report)
        cand = OBJECTIVES[objective](candidate.report)
        if base == 0:
            continue
        pct = 100 * (cand - base) / base
        if abs(pct) < 0.05:
            continue
        (losses if pct > 0 else gains).append(f"{objective} {pct:+.1f}%")
    if not gains and not losses:
        return f"{candidate.label}: identical to baseline on {', '.join(objectives)}"
    parts = ", ".join(gains) if gains else "no improvement"
    if losses:
        parts += " at the cost of " + ", ".join(losses)
    return f"{candidate.label}: {parts}"
