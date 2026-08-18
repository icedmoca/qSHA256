#!/usr/bin/env python3
"""Regenerate and check every claim in docs/claims.md, from scratch.

    python scripts/reproduce.py              # check all claims
    python scripts/reproduce.py --quick      # skip the slower ones
    python scripts/reproduce.py --json out.json

Each claim in the register has an identifier (C1..C10). This script re-derives
the number behind each one and compares it against the value the register
states, so the documentation cannot drift away from the code. A claim that
cannot be re-derived is a failure, not a warning.

Claims are also tagged with their epistemic status, and the script prints it
alongside each result, because "measured", "proved" and "proved within a stated
model" are different things and the distinction is the point.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UNCONDITIONAL = "PROVED (unconditional)"
CONDITIONAL = "PROVED (within a stated model)"
MEASURED = "MEASURED"
EXECUTED = "VERIFIED BY EXECUTION"
ANALYTICAL = "ANALYTICAL (stated decomposition)"


@dataclass
class ClaimResult:
    claim: str
    title: str
    status: str
    passed: bool
    expected: Any = None
    actual: Any = None
    detail: str = ""
    conditions: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        mark = "OK  " if self.passed else "FAIL"
        head = f"  [{mark}] {self.claim}: {self.title}"
        body = f"         {self.status}"
        if self.expected is not None:
            body += f" | expected {self.expected}, got {self.actual}"
        lines = [head, body]
        if self.detail:
            lines.append(f"         {self.detail}")
        for condition in self.conditions:
            lines.append(f"         CONDITION: {condition}")
        return "\n".join(lines)


def _check(claim, title, status, expected, actual, detail="", conditions=()):
    return ClaimResult(
        claim=claim,
        title=title,
        status=status,
        passed=expected == actual,
        expected=expected,
        actual=actual,
        detail=detail,
        conditions=list(conditions),
    )


# --------------------------------------------------------------------------


def c1_circuit_computes_sha256() -> ClaimResult:
    from qsha256 import SHA256
    from qsha256.classical.sha256 import pad_message, parse_blocks
    from qsha256.classical.sha256 import sha256 as sha_generic
    from qsha256.quantum.sha256.compression import build_compression
    from qsha256.quantum.strategies import Strategy
    from qsha256.validation.basis_sim import BasisSimulator
    from qsha256.validation.vectors import NIST_CAVP_SHA256

    comp = build_compression(SHA256, Strategy(), initial_state=SHA256.h0)
    sim = BasisSimulator(comp.circuit)
    checked = 0
    for hexmsg, expected in NIST_CAVP_SHA256:
        message = bytes.fromhex(hexmsg)
        blocks = parse_blocks(pad_message(message), SHA256)
        if len(blocks) != 1:
            continue
        out, _ = sim.run(sim.load(dict(zip(comp.message, blocks[0]))))
        digest = b"".join(sim.read(out, r).to_bytes(4, "big") for r in comp.state)
        if digest.hex() != expected or sha_generic(message).hex() != expected:
            return _check("C1", "circuit computes SHA-256", EXECUTED, expected, digest.hex())
        checked += 1
    return _check(
        "C1",
        "circuit computes SHA-256",
        EXECUTED,
        checked,
        checked,
        detail=f"{checked} NIST CAVP one-block vectors executed through the "
        f"1,057-qubit circuit and matched",
        conditions=["exact on the inputs tried; SAT proofs cover all inputs (C1 proof half)"],
    )


def c2_mc_of_ch_and_maj() -> ClaimResult:
    from qsha256.classical.sha256 import ch, maj
    from qsha256.formal.bounds import multiplicative_complexity, truth_table

    values = {}
    for name, fn in (("Ch", ch), ("Maj", maj)):
        table = truth_table(lambda x, y, z, _f=fn: _f(x, y, z) & 1, 3)
        result = multiplicative_complexity(table, 3)
        values[name] = result.value if result.exact else None
    control = multiplicative_complexity(truth_table(lambda x, y, z: x & y & z, 3), 3)
    return _check(
        "C2",
        "MC(Ch) = MC(Maj) = 1",
        UNCONDITIONAL,
        {"Ch": 1, "Maj": 1, "control(x&y&z)": 2},
        {**values, "control(x&y&z)": control.value},
        detail="exhaustive over affine decompositions; witness plus exhausted search",
    )


def c3_gidney_adder_floor() -> ClaimResult:
    from qsha256.formal.bounds import component_bounds

    bounds = {b.component.split(" ")[0]: b for b in component_bounds(32, timeout=20)}
    actual = {name: bounds[name].achieved_ands for name in ("gidney", "cdkm", "vbe")}
    return _check(
        "C3",
        "Gidney adder attains the n-1 floor",
        UNCONDITIONAL,
        {"gidney": 31, "cdkm": 62, "vbe": 124},
        actual,
        detail=(
            "floor MC(add mod 2^n) = n-1 is published (Boyar-Peralta), not proved "
            "here. CDKM is 2(n-1) = 62, not 2n: the top MAJ/UMA pair cancels "
            "when the carry out is discarded. The rewriter was already removing "
            "those, so this is not a resource saving -- the construction now "
            "reaches the published cost without needing a later pass."
        ),
        conditions=["adder floor cited, not re-proved"],
    )


def c4_and_count() -> ClaimResult:
    from qsha256 import SHA256
    from qsha256.interop import cross_validate
    from qsha256.quantum.sha256.compression import build_compression
    from qsha256.quantum.strategies import Strategy

    comp = build_compression(SHA256, Strategy(adder="gidney"), rounds=64)
    ops = dict(comp.circuit.count_ops())
    agreement = cross_validate(comp.circuit, "gidney r=64")
    actual = {
        "and_g": ops.get("and_g", 0),
        "ccx": ops.get("ccx", 0),
        "independent_counters_agree": agreement.agree,
    }
    return _check(
        "C4",
        "22,696 AND computations, zero Toffolis",
        MEASURED,
        {"and_g": 22696, "ccx": 0, "independent_counters_agree": True},
        actual,
        detail=f"and_g_dg uncomputations: {ops.get('and_g_dg', 0):,} (free in T, "
        f"but real measurements)",
    )


def c5_composed_floor() -> ClaimResult:
    from qsha256.formal.bounds import circuit_bound_report
    from qsha256.quantum.strategies import Strategy

    report = circuit_bound_report(strategy=Strategy(adder="gidney"), rounds=64, timeout=20)
    return _check(
        "C5",
        "attains the composed floor for its architecture class",
        CONDITIONAL,
        {"achieved": 22696, "floor": 22696},
        {"achieved": report.achieved_ands, "floor": report.lower_bound},
        detail="NOT a lower bound on SHA-256's multiplicative complexity",
        conditions=[
            "components computed separately, no non-linear work shared across them",
            "multi-operand sums formed as chained pairwise additions",
            "MC of the 5-operand sum mod 2^n is not known to be 4(n-1)",
        ],
    )


def c6_c7_pebbling(quick: bool) -> list[ClaimResult]:
    from qsha256 import SHA256
    from qsha256.formal.pebbling import schedule_dag, solve_pebbling

    dag = schedule_dag(SHA256)
    budgets = [48] if quick else [48, 64, 96, 128, 192, 256]

    witness = solve_pebbling(dag, 16, steps=48, timeout=120)
    c6 = _check(
        "C6",
        "16 registers suffice",
        CONDITIONAL,
        "STRATEGY",
        witness.status,
        detail=f"explicit move sequence found ({witness.computations} computations)",
        conditions=["within 48 moves", "in-place moves permitted; see pebbling rules"],
    )

    statuses = {}
    for steps in budgets:
        statuses[steps] = solve_pebbling(dag, 15, steps=steps, timeout=120).status
    all_impossible = set(statuses.values()) == {"IMPOSSIBLE"}
    c7 = _check(
        "C7",
        "15 registers do not suffice",
        CONDITIONAL,
        True,
        all_impossible,
        detail=f"UNSAT at every budget tested: {statuses}",
        conditions=[
            f"bounded: impossible within {max(budgets)} moves "
            f"({max(budgets) / 48:.1f}x the minimum), not unbounded",
            "relative to the stated move set; --classical-game gives a different answer",
            "word-granularity dependency graph",
        ],
    )
    return [c6, c7]


def c8_t_count() -> ClaimResult:
    from qsha256 import SHA256
    from qsha256.quantum.resources import analyze
    from qsha256.quantum.sha256.compression import build_compression
    from qsha256.quantum.strategies import Strategy

    comp = build_compression(SHA256, Strategy(adder="gidney"), rounds=64)
    report = analyze(comp, spec=SHA256, rounds=64, transpile_t=False)
    return _check(
        "C8",
        "T-count 90,784 at 1,119 logical qubits",
        ANALYTICAL,
        {"t_count": 90784, "qubits": 1119},
        {"t_count": report.t_count, "qubits": report.width},
        detail="Gidney decomposition: 4 T per AND computation, 0 per uncomputation",
        conditions=[
            "logical qubits, not physical",
            "requires mid-circuit measurement and classical feedforward",
        ],
    )


def c9_versus_published() -> ClaimResult:
    """Like-for-like against Amy et al., with their circuit REBUILT, not quoted.

    The previous version of this claim compared a qSHA256 number against a
    figure transcribed from their table, and hard-coded our own side of the
    comparison as a literal. Both halves are now derived: their architecture is
    rebuilt from their published figures and run through the same expansion and
    the same phase-polynomial optimizer as ours.

    The margin got smaller. That is the point of doing it properly.
    """
    from qsha256 import SHA256
    from qsha256.interop.baselines.amy2016 import build_amy_round, build_amy_stretch
    from qsha256.quantum.optimization.phase_fold import phase_fold, to_clifford_t
    from qsha256.quantum.sha256.compression import build_compression
    from qsha256.quantum.sha256.round import build_round_circuit
    from qsha256.quantum.strategies import Strategy

    def folded(circuit) -> int:
        return phase_fold(to_clifford_t(circuit), already_clifford_t=True).t_after

    amy_round, _, _, _ = build_amy_round(SHA256)
    amy_stretch, _ = build_amy_stretch(SHA256)
    theirs = 64 * folded(amy_round.circuit) + 48 * folded(amy_stretch.circuit)

    ours_round = build_round_circuit(SHA256, Strategy(adder="cdkm"), t=0)
    ours_round = ours_round[0].circuit if isinstance(ours_round, tuple) else ours_round.circuit
    ours_unitary = 64 * folded(ours_round) + 48 * folded(amy_stretch.circuit)

    ops = dict(build_compression(SHA256, Strategy(adder="gidney"), rounds=64).circuit.count_ops())
    ours_best = ops.get("and_g", 0) * 4

    return _check(
        "C9",
        "below the Amy et al. architecture, rebuilt and measured here",
        MEASURED,
        {"rebuilt": 195968, "unitary_delta_pct": -8.4, "best_delta_pct": -53.7},
        {
            "rebuilt": theirs,
            "unitary_delta_pct": round(100 * (ours_unitary / theirs - 1), 1),
            "best_delta_pct": round(100 * (ours_best / theirs - 1), 1),
        },
        detail=(
            f"their architecture rebuilt = {theirs:,} T; qSHA256 unitary = "
            f"{ours_unitary:,} T; qSHA256 with feedforward = {ours_best:,} T. "
            f"Their PUBLISHED figure is 228,992, against which the margins look "
            f"larger -- but see the conditions."
        ),
        conditions=[
            "both sides expanded at 7 T per Toffoli and folded by the SAME "
            "optimizer; no number is transcribed from their table",
            "the rebuild reaches 626 Toffoli/round where their H column implies "
            "754, so their published total is 17% above their own architecture; "
            "comparing against 228,992 would credit qSHA256 with that gap",
            "the -53.7% figure assumes measurement and feedforward, which their "
            "circuit does not use; the like-for-like number is -8.4%",
        ],
    )


def c11_reproduce_published_row() -> ClaimResult:
    """An entire published optimized row, reproduced end to end."""
    from qsha256.interop.baselines.amy2016 import reproduce_optimized_stretch

    got = reproduce_optimized_stretch()
    return _check(
        "C11",
        "their Stretch (Opt.) row reproduces exactly",
        EXECUTED,
        {"t": 744, "h": 372},
        {"t": got["t_after_folding"], "h": got["our_h"]},
        detail=(
            "rebuilt from Algorithm 2 -> 186 Toffoli -> 1,302 T -> phase folding "
            "-> 744 T and 372 H, matching Table 1 'Stretch (Opt.)' on both"
        ),
        conditions=[
            "this also shows their unoptimized Stretch T of 1,329 is wrong: "
            "744 is consistent with 1,302, and the error inflates their "
            "reported SHA-256 total by 48 x 27 = 1,296 T",
        ],
    )


def c12_pareto_position() -> ClaimResult:
    """Where qSHA256 sits against the best published designs. Behind, on two axes."""
    from qsha256 import SHA256
    from qsha256.quantum.resources import analyze
    from qsha256.quantum.resources.leaderboard import pareto_position
    from qsha256.quantum.sha256.compression import build_compression
    from qsha256.quantum.strategies import Strategy

    dominated = {}
    for adder, layout in (("gidney", "wide"), ("gidney", "serial"), ("cdkm", "serial")):
        comp = build_compression(SHA256, Strategy(adder=adder, round_layout=layout), rounds=64)
        report = analyze(comp, spec=SHA256, rounds=64, transpile_t=False)
        position = pareto_position(
            f"{adder}/{layout}", report.width, report.depth["non_clifford_depth"]
        )
        dominated[f"{adder}/{layout}"] = position.dominated
    return _check(
        "C12",
        "qSHA256 is Pareto-dominated on width and depth",
        MEASURED,
        {"gidney/wide": True, "gidney/serial": True, "cdkm/serial": True},
        dominated,
        detail=(
            "Every configuration is beaten on BOTH width and non-Clifford depth "
            "by Lee et al. 2022 SHA-Z2 (799 qubits, Toffoli-depth 12,024). "
            "qSHA256 leads on T-count; that line of work does not report one."
        ),
        conditions=[
            "our non-Clifford depth is compared against their Toffoli-depth; "
            "these are close but not identical quantities",
            "Kim et al.'s figures are read from Lee et al.'s Table 2, not from "
            "the 2018 paper directly",
        ],
    )


def c10_oracle_ratio() -> ClaimResult:
    from qsha256 import SHA256
    from qsha256.quantum.oracle.preimage import build_preimage_oracle
    from qsha256.quantum.sha256.compression import build_compression
    from qsha256.quantum.strategies import Strategy

    forward = build_compression(SHA256, Strategy(), rounds=64).circuit.count_ops()["ccx"]
    oracle = build_preimage_oracle(
        SHA256,
        Strategy(uncompute_working=True),
        rounds=64,
        target_digest=0,
        initial_state=tuple(SHA256.h0),
    ).circuit.count_ops()["ccx"]
    return _check(
        "C10",
        "a Grover query costs 2.02x a forward hash",
        MEASURED,
        2.02,
        round(oracle / forward, 2),
        detail=f"oracle {oracle:,} Toffolis vs forward {forward:,}",
    )


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="skip the slower checks")
    parser.add_argument("--json", metavar="PATH", help="write results as JSON")
    args = parser.parse_args()

    print("Reproducing every claim in docs/claims.md")
    print("=" * 78)
    print()

    checks: list[Callable[[], Any]] = [
        c1_circuit_computes_sha256,
        c2_mc_of_ch_and_maj,
        c3_gidney_adder_floor,
        c4_and_count,
        c5_composed_floor,
        lambda: c6_c7_pebbling(args.quick),
        c8_t_count,
        c9_versus_published,
        c10_oracle_ratio,
        c11_reproduce_published_row,
        c12_pareto_position,
    ]

    results: list[ClaimResult] = []
    for check in checks:
        started = time.time()
        outcome = check()
        produced = outcome if isinstance(outcome, list) else [outcome]
        for item in produced:
            item.seconds = round(time.time() - started, 2)
            results.append(item)
            print(item, flush=True)
        print()

    failures = [r for r in results if not r.passed]
    print("=" * 78)
    print(f"{len(results) - len(failures)}/{len(results)} claims reproduced")
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    for status, count in sorted(by_status.items()):
        print(f"  {count:>2}  {status}")
    conditional = sum(1 for r in results if r.conditions)
    print(f"\n{conditional} of {len(results)} claims carry explicit conditions.")
    print("A claim without its conditions is not a claim; see docs/claims.md.")

    if args.json:
        Path(args.json).write_text(json.dumps([r.to_dict() for r in results], indent=2) + "\n")
        print(f"\nwrote {args.json}")

    if failures:
        print(f"\n{len(failures)} claim(s) could not be reproduced:")
        for failure in failures:
            print(f"  {failure.claim}: expected {failure.expected}, got {failure.actual}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
