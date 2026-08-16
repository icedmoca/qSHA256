"""Command-line interface for qSHA256.

qsha256 validate      check circuits against the classical reference
qsha256 circuit       build a circuit and show or export it
qsha256 analyze       measure logical resources
qsha256 benchmark     scaling tables across round counts
qsha256 search        Pareto search over the design space
qsha256 oracle        preimage oracle cost and Grover extrapolation
qsha256 physical      fault-tolerant estimate under a hardware model
qsha256 leaderboard   comparison against published circuits
qsha256 grover-demo   run the toy Grover search for real
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .spec import SPECS, get_spec

# Ordered round counts used by the default benchmark sweep.
DEFAULT_ROUNDS = (1, 2, 4, 8, 16, 32, 64)


def _add_common(parser: argparse.ArgumentParser, rounds: bool = True) -> None:
    parser.add_argument("--spec", default="sha256", choices=sorted(SPECS), help="which SHA variant")
    if rounds:
        parser.add_argument("--rounds", type=int, default=None, help="number of compression rounds")
    parser.add_argument("--adder", default="cdkm", choices=("cdkm", "vbe", "qft"))
    parser.add_argument("--const-add", default="load", choices=("load", "vbe_const"))
    parser.add_argument("--schedule", default="rolling", choices=("rolling", "store_all"))
    parser.add_argument("--round-layout", default="serial", choices=("serial", "wide", "csa"))
    parser.add_argument(
        "--uncompute",
        action="store_true",
        help="produce a garbage-free circuit (required for oracle use; ~2x cost)",
    )
    parser.add_argument(
        "--preset",
        default=None,
        help="named architecture preset, overriding the individual flags",
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="apply gate-level rewriting (cancellation + constant folding)",
    )


def _strategy(args):
    from .quantum.strategies import Strategy, get_preset

    if getattr(args, "preset", None):
        strategy = get_preset(args.preset)
    else:
        strategy = Strategy(
            adder=args.adder,
            const_add=args.const_add,
            schedule=args.schedule,
            round_layout=args.round_layout,
            uncompute_working=args.uncompute,
        )
    return strategy


def _build(args):
    from .quantum.sha256.compression import build_compression

    spec = get_spec(args.spec)
    rounds = args.rounds if getattr(args, "rounds", None) else spec.rounds
    return spec, rounds, build_compression(spec, _strategy(args), rounds=rounds)


def _write(text: str, path: str | None) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text)
        print(f"wrote {path}", file=sys.stderr)
    else:
        print(text)


def _reproduce(args, extra: str = "") -> str:
    return f"qsha256 {args.command} --spec {args.spec} " + extra


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_analyze(args) -> int:
    from .quantum.resources import analyze, render

    spec, rounds, comp = _build(args)
    circuit_source = comp
    note = None
    if args.rewrite:
        from .quantum.optimization.rewrite import apply_rewrites

        result = apply_rewrites(comp.builder)
        circuit_source = result.circuit
        note = result.summary()

    report = analyze(
        circuit_source,
        spec=spec,
        strategy=comp.strategy,
        rounds=rounds,
        target=f"{spec.name} compression"
        + (" (garbage-free)" if comp.uncomputed else " (forward only)"),
        toffoli_model=args.toffoli_model,
        transpile_t=None if not args.no_transpile else False,
        reproduce=_reproduce(args, f"--rounds {rounds} --adder {args.adder}"),
    )
    if note:
        report.assumptions.append(f"Gate-level rewriting applied: {note}")
    _write(render(report, args.format), args.output)
    return 0


def cmd_circuit(args) -> int:
    spec, rounds, comp = _build(args)
    circuit = comp.circuit
    print(
        f"{spec.name} compression, {rounds} rounds, {comp.strategy.label()}\n"
        f"  qubits: {circuit.num_qubits:,} "
        f"({comp.builder.data_qubits:,} data + {comp.builder.ancilla_qubits:,} ancilla)\n"
        f"  gates:  {sum(circuit.count_ops().values()):,}  {dict(circuit.count_ops())}\n"
        f"  depth:  {circuit.depth():,}",
        file=sys.stderr,
    )

    if args.draw:
        if circuit.num_qubits > 40 or len(circuit.data) > 400:
            print(
                f"\nRefusing to draw a {circuit.num_qubits}-qubit, "
                f"{len(circuit.data):,}-gate circuit as text -- it would be unreadable.\n"
                f"Try a small one:  qsha256 circuit --spec toy4 --rounds 1 --draw\n"
                f"or export it:     qsha256 circuit --qasm out.qasm",
                file=sys.stderr,
            )
            return 1
        print(circuit.draw(output="text", fold=120))

    if args.qasm:
        from qiskit import qasm3

        _write(qasm3.dumps(circuit), args.qasm)
    return 0


def cmd_validate(args) -> int:
    from .validation.suite import run_validation

    ok = run_validation(quick=args.quick, verbose=True)
    return 0 if ok else 1


def cmd_benchmark(args) -> int:
    from .quantum.resources import render
    from .validation.benchmark import run_scaling_benchmark

    rounds = args.rounds_list or list(DEFAULT_ROUNDS)
    spec = get_spec(args.spec)
    reports = run_scaling_benchmark(
        spec,
        rounds,
        _strategy(args),
        transpile_t=not args.no_transpile,
        progress=lambda msg: print(msg, file=sys.stderr),
    )
    _write(render(reports, args.format), args.output)
    return 0


def cmd_search(args) -> int:
    import json

    from .quantum.optimization.search import search_designs

    spec = get_spec(args.spec)
    rounds = args.rounds or spec.rounds
    result = search_designs(
        spec,
        rounds=rounds,
        rewrite=not args.no_rewrite,
        verify=not args.no_verify,
        verify_trials=args.verify_trials,
        progress=(lambda label: print(f"  building {label}", file=sys.stderr))
        if args.verbose
        else None,
    )
    _write(
        json.dumps(result.to_dict(), indent=2) if args.format == "json" else str(result),
        args.output,
    )
    return 0


def cmd_oracle(args) -> int:
    from .quantum.oracle.grover import grover_cost_estimate
    from .quantum.oracle.preimage import build_preimage_oracle
    from .quantum.resources import analyze

    spec = get_spec(args.spec)
    rounds = args.rounds or spec.rounds
    strategy = _strategy(args)
    oracle = build_preimage_oracle(
        spec, strategy, rounds=rounds, target_digest=0, initial_state=tuple(spec.h0)
    )
    report = analyze(
        oracle.builder,
        spec=spec,
        strategy=strategy,
        rounds=rounds,
        target=f"{spec.name} preimage oracle",
        transpile_t=not args.no_transpile,
        reproduce=_reproduce(args, f"--rounds {rounds}"),
    )
    print(report)
    print()
    print(grover_cost_estimate(report, search_bits=args.search_bits))
    return 0


def cmd_physical(args) -> int:
    from .quantum.resources import analyze, estimate_physical

    spec, rounds, comp = _build(args)
    report = analyze(
        comp, spec=spec, strategy=comp.strategy, rounds=rounds, target=f"{spec.name} compression"
    )
    for model in args.model:
        print(estimate_physical(report, model, args.target_failure))
        print()
    return 0


def cmd_leaderboard(args) -> int:
    from .quantum.resources import analyze
    from .quantum.resources.leaderboard import PUBLISHED, render_leaderboard

    spec, rounds, comp = _build(args)
    report = analyze(
        comp,
        spec=spec,
        strategy=comp.strategy,
        rounds=rounds,
        target=f"{spec.name} compression",
        transpile_t=True,
    )
    for key in args.against or list(PUBLISHED):
        print(render_leaderboard(report, key))
        print()
    return 0


def cmd_grover_demo(args) -> int:
    from .validation.grover_demo import run_grover_demo

    return 0 if run_grover_demo(iterations=args.iterations, compare_bits=args.compare_bits) else 1


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qsha256",
        description=(
            "Reversible quantum SHA-256: construct, verify, optimize and measure. "
            "This is a research tool. It does not hash anything faster than "
            "hashlib and is not a cryptographic library."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"qsha256 {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="measure logical resources of a circuit")
    _add_common(p)
    p.add_argument("--format", default="text", choices=("text", "json", "csv", "markdown"))
    p.add_argument("--output", default=None, help="write to a file instead of stdout")
    p.add_argument("--toffoli-model", default="standard", choices=("standard", "selinger", "jones"))
    p.add_argument("--no-transpile", action="store_true", help="skip Clifford+T transpilation")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("circuit", help="build a circuit; optionally draw or export it")
    _add_common(p)
    p.add_argument("--draw", action="store_true", help="print as text (small circuits only)")
    p.add_argument("--qasm", default=None, metavar="PATH", help="export OpenQASM 3")
    p.set_defaults(func=cmd_circuit)

    p = sub.add_parser("validate", help="check circuits against the classical reference")
    p.add_argument("--quick", action="store_true", help="skip the slower full-scale checks")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("benchmark", help="resource scaling across round counts")
    _add_common(p, rounds=False)
    p.add_argument(
        "--rounds",
        dest="rounds_list",
        type=lambda s: [int(x) for x in s.replace(",", " ").split()],
        default=None,
        help="round counts, e.g. --rounds 1,2,4,8,16,32,64",
    )
    p.add_argument("--format", default="markdown", choices=("text", "json", "csv", "markdown"))
    p.add_argument("--output", default=None)
    p.add_argument("--no-transpile", action="store_true")
    p.set_defaults(func=cmd_benchmark)

    p = sub.add_parser("search", help="Pareto search over the design space")
    p.add_argument("--spec", default="sha256", choices=sorted(SPECS))
    p.add_argument("--rounds", type=int, default=None)
    p.add_argument("--no-rewrite", action="store_true")
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--verify-trials", type=int, default=2)
    p.add_argument("--format", default="text", choices=("text", "json"))
    p.add_argument("--output", default=None)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("oracle", help="preimage oracle cost and Grover extrapolation")
    _add_common(p)
    p.add_argument("--search-bits", type=int, default=256, help="search space size, in bits")
    p.add_argument("--no-transpile", action="store_true")
    p.set_defaults(func=cmd_oracle, uncompute=True)

    p = sub.add_parser("physical", help="fault-tolerant estimate under a hardware model")
    _add_common(p)
    p.add_argument(
        "--model",
        nargs="+",
        default=["superconducting"],
        choices=("superconducting", "optimistic", "conservative"),
    )
    p.add_argument("--target-failure", type=float, default=0.01)
    p.set_defaults(func=cmd_physical)

    p = sub.add_parser("leaderboard", help="compare against published circuits")
    _add_common(p)
    p.add_argument("--against", nargs="+", default=None)
    p.set_defaults(func=cmd_leaderboard)

    p = sub.add_parser("grover-demo", help="run the toy Grover search for real")
    p.add_argument("--iterations", type=int, default=None)
    p.add_argument("--compare-bits", type=int, default=4)
    p.set_defaults(func=cmd_grover_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
