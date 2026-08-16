"""Scaling benchmarks: how resources grow with rounds, and across designs."""

from __future__ import annotations

from typing import Callable, Sequence

from ..spec import SHA256, ShaSpec
from ..quantum.resources.analyzer import ResourceReport, analyze
from ..quantum.sha256.compression import build_compression
from ..quantum.strategies import DEFAULT, Strategy

__all__ = ["run_scaling_benchmark", "run_strategy_benchmark"]


def run_scaling_benchmark(
    spec: ShaSpec = SHA256,
    rounds: Sequence[int] = (1, 2, 4, 8, 16, 32, 64),
    strategy: Strategy = DEFAULT,
    transpile_t: bool = True,
    progress: Callable[[str], None] | None = None,
) -> list[ResourceReport]:
    """Measure one architecture across a range of round counts.

    Every row is built and measured independently -- nothing is extrapolated
    from a per-round figure, which is why the table can show that the cost is
    not perfectly linear in rounds (the message schedule only starts expanding
    after round 16).
    """
    reports = []
    for count in rounds:
        if count > spec.rounds:
            raise ValueError(f"{spec.name} has at most {spec.rounds} rounds")
        if progress:
            progress(f"building {spec.name} @ {count} rounds ({strategy.label()})")
        comp = build_compression(spec, strategy, rounds=count)
        reports.append(
            analyze(
                comp,
                spec=spec,
                strategy=strategy,
                rounds=count,
                target=f"{spec.name} compression",
                transpile_t=transpile_t,
                reproduce=(
                    f"qsha256 analyze --spec {spec.name} --rounds {count} "
                    f"--adder {strategy.adder} --schedule {strategy.schedule} "
                    f"--round-layout {strategy.round_layout}"
                    + (" --uncompute" if strategy.uncompute_working else "")
                ),
            )
        )
    return reports


def run_strategy_benchmark(
    spec: ShaSpec = SHA256,
    strategies: Sequence[Strategy] = (),
    rounds: int | None = None,
    transpile_t: bool = True,
    progress: Callable[[str], None] | None = None,
) -> list[ResourceReport]:
    """Measure several architectures at one round count."""
    rounds = spec.rounds if rounds is None else rounds
    reports = []
    for strategy in strategies:
        if progress:
            progress(f"building {strategy.label()}")
        comp = build_compression(spec, strategy, rounds=rounds)
        reports.append(
            analyze(
                comp,
                spec=spec,
                strategy=strategy,
                rounds=rounds,
                target=f"{spec.name} compression",
                transpile_t=transpile_t,
            )
        )
    return reports
