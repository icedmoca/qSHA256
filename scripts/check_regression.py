#!/usr/bin/env python3
"""Fail the build if resource costs regress.

    python scripts/check_regression.py            # check against the baseline
    python scripts/check_regression.py --update   # accept current as the baseline

Performance testing, but for circuits. Optimization claims are easy to make and
easy to lose: a refactor that quietly adds 5% to the T-count is invisible in a
normal test suite, because the circuit still computes the right answer. This
pins the numbers so that a regression is a build failure rather than a
discovery six months later.

The baseline lives in ``benchmarks/results/baseline.json`` and is committed, so
the history records not just that the circuits worked but what they cost.
Improvements are reported and, with ``--update``, adopted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE = ROOT / "benchmarks" / "results" / "baseline.json"

#: Metric -> fractional worsening tolerated before the build fails. Qubits and
#: T-count are pinned tightly because they are the headline claims; depth is
#: looser because it moves with unrelated scheduling changes.
TOLERANCE = {
    "qubits": 0.0,
    "toffoli": 0.01,
    "and_g": 0.01,
    "t_count": 0.01,
    "non_clifford_depth": 0.05,
}


def measure() -> dict[str, dict[str, int]]:
    """The configurations whose cost is pinned."""
    from qsha256 import SHA256
    from qsha256.quantum.resources import analyze
    from qsha256.quantum.sha256.compression import build_compression
    from qsha256.quantum.strategies import Strategy

    configurations = {
        "sha256-forward-cdkm": Strategy(),
        "sha256-forward-gidney": Strategy(adder="gidney"),
        "sha256-garbage-free-cdkm": Strategy(uncompute_working=True),
        "sha256-garbage-free-gidney": Strategy(adder="gidney", uncompute_working=True),
    }
    out: dict[str, dict[str, int]] = {}
    for name, strategy in configurations.items():
        comp = build_compression(SHA256, strategy, rounds=64)
        report = analyze(comp, spec=SHA256, strategy=strategy, rounds=64, transpile_t=False)
        ct = report.clifford_t
        out[name] = {
            "qubits": report.width,
            "toffoli": report.toffoli_count,
            "and_g": int(ct.get("and_compute_gates", 0)),
            "t_count": report.t_count,
            "non_clifford_depth": report.depth["non_clifford_depth"],
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="adopt current values")
    args = parser.parse_args()

    current = measure()

    if args.update or not BASELINE.exists():
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(current, indent=2) + "\n")
        print(f"baseline written to {BASELINE.relative_to(ROOT)}")
        return 0

    baseline = json.loads(BASELINE.read_text())
    regressions: list[str] = []
    improvements: list[str] = []

    for name, metrics in current.items():
        if name not in baseline:
            print(f"NEW  {name} (not in baseline; run --update to adopt)")
            continue
        for metric, value in metrics.items():
            was = baseline[name].get(metric)
            if was is None:
                continue
            if was == 0:
                if value > 0:
                    regressions.append(f"{name}.{metric}: 0 -> {value:,}")
                continue
            change = (value - was) / was
            if change > TOLERANCE.get(metric, 0.02):
                regressions.append(
                    f"{name}.{metric}: {was:,} -> {value:,} ({change:+.1%}, "
                    f"tolerance {TOLERANCE.get(metric, 0.02):+.0%})"
                )
            elif change < -0.005:
                improvements.append(f"{name}.{metric}: {was:,} -> {value:,} ({change:+.1%})")

    for line in improvements:
        print(f"IMPROVED  {line}")
    for line in regressions:
        print(f"REGRESSED {line}")

    if regressions:
        print(f"\n{len(regressions)} regression(s). If intended, re-run with --update.")
        return 1
    print(f"\nno regressions across {len(current)} configurations")
    if improvements:
        print("improvements found; run --update to adopt them as the new baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
