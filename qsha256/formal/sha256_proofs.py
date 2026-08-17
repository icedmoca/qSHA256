"""Proof obligations for the SHA-256 circuits themselves.

Each function here builds a circuit, symbolically executes it, builds the
specification independently in the same AIG, and discharges the miter.  An UNSAT
answer proves the property for **every** input -- all ``2^768`` of them for a
full compression, which no amount of testing can reach.

The proofs are layered exactly like the test suite, and for the same reason: a
failure at the bottom explains a failure at the top.  Primitives first, then the
round function, then the schedule, then whole compressions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..quantum.primitives.add import add_const_into, add_into
from ..quantum.primitives.boolean import ch_word_into, maj_word_into
from ..quantum.primitives.xor import xor_terms
from ..quantum.registers import CircuitBuilder
from ..quantum.sha256.compression import build_compression
from ..quantum.sha256.round import build_round_circuit
from ..quantum.sha256.schedule import build_schedule
from ..quantum.strategies import DEFAULT, Strategy
from ..spec import SHA256, ShaSpec
from . import spec_aig as S
from .aig import CONST_FALSE, symbolic_execute
from .cnf import DEFAULT_SOLVER
from .equivalence import (
    CircuitProof,
    prove_ancillas_clean,
    prove_and_preconditions,
    prove_equivalent,
)

__all__ = [
    "ProofReport",
    "prove_adder",
    "prove_boolean",
    "prove_compression",
    "prove_const_adder",
    "prove_round",
    "prove_schedule",
    "prove_sigma",
    "run_proofs",
]


def _lits(state, word):
    return [CONST_FALSE if q is None else state.values[q] for q in word]


def _in_lits(state, word):
    return [CONST_FALSE if q is None else state.inputs[q] for q in word]


def _finish(builder, state, obligations, target, started) -> CircuitProof:
    obligations.append(
        prove_ancillas_clean(state.aig, [state.values[q] for q in builder.ancillas.all])
    )
    obligations.append(prove_and_preconditions(state.aig, state.and_preconditions))
    return CircuitProof(
        target=target,
        proofs=obligations,
        aig_nodes=state.aig.num_ands,
        seconds=time.time() - started,
    )


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------


def prove_adder(name: str = "cdkm", width: int = 32, solver: str = DEFAULT_SOLVER) -> CircuitProof:
    """``target += a  (mod 2^w)``, with ``a`` unchanged and ancillas cleared."""
    started = time.time()
    b = CircuitBuilder(f"{name}{width}")
    a, t = b.add_word(width, "a"), b.add_word(width, "b")
    add_into(b, a, t, name)
    state = symbolic_execute(b.circuit, free_qubits=a.qubits + t.qubits)
    aig = state.aig
    a_in, t_in = _in_lits(state, a), _in_lits(state, t)
    obligations = [
        prove_equivalent(
            aig,
            _lits(state, t),
            S.add_mod(aig, a_in, t_in),
            name=f"{name} adder computes (a + b) mod 2^{width}",
            solver=solver,
        ),
        prove_equivalent(
            aig,
            _lits(state, a),
            a_in,
            name="addend register is preserved",
            solver=solver,
        ),
    ]
    return _finish(b, state, obligations, f"adder {name} w={width}", started)


def prove_const_adder(
    strategy: str = "load", width: int = 8, value: int = 0xA5, solver: str = DEFAULT_SOLVER
) -> CircuitProof:
    started = time.time()
    b = CircuitBuilder(f"const_{strategy}")
    t = b.add_word(width, "b")
    add_const_into(b, value, t, "cdkm", strategy)
    state = symbolic_execute(b.circuit, free_qubits=t.qubits)
    aig = state.aig
    obligations = [
        prove_equivalent(
            aig,
            _lits(state, t),
            S.add_const(aig, _in_lits(state, t), value),
            name=f"constant addition ({strategy}) of 0x{value:x} mod 2^{width}",
            solver=solver,
        )
    ]
    return _finish(b, state, obligations, f"const-adder {strategy} w={width}", started)


def prove_boolean(which: str = "ch", width: int = 32, solver: str = DEFAULT_SOLVER) -> CircuitProof:
    """Ch or Maj: correct output, and all three inputs restored."""
    started = time.time()
    b = CircuitBuilder(which)
    x, y, z, t = (b.add_word(width, c) for c in "xyzt")
    (ch_word_into if which == "ch" else maj_word_into)(b, x, y, z, t)
    state = symbolic_execute(b.circuit, free_qubits=x.qubits + y.qubits + z.qubits + t.qubits)
    aig = state.aig
    xi, yi, zi, ti = (_in_lits(state, w) for w in (x, y, z, t))
    reference = (S.ch if which == "ch" else S.maj)(aig, xi, yi, zi)
    obligations = [
        prove_equivalent(
            aig,
            _lits(state, t),
            S.xor_words(aig, ti, reference),
            name=f"{which} target ^= {which}(x, y, z)",
            solver=solver,
        ),
        prove_equivalent(
            aig,
            _lits(state, x) + _lits(state, y) + _lits(state, z),
            xi + yi + zi,
            name=f"{which} inputs restored",
            solver=solver,
        ),
    ]
    return _finish(b, state, obligations, f"{which} w={width}", started)


def prove_sigma(
    which: str = "big_sigma0", spec: ShaSpec = SHA256, solver: str = DEFAULT_SOLVER
) -> CircuitProof:
    started = time.time()
    terms = getattr(spec, which)
    b = CircuitBuilder(which)
    x, t = b.add_word(spec.word_bits, "x"), b.add_word(spec.word_bits, "t")
    xor_terms(b, x, terms, t)
    state = symbolic_execute(b.circuit, free_qubits=x.qubits + t.qubits)
    aig = state.aig
    xi, ti = _in_lits(state, x), _in_lits(state, t)
    reference = getattr(S, which)(aig, xi, spec)
    obligations = [
        prove_equivalent(
            aig,
            _lits(state, t),
            S.xor_words(aig, ti, reference),
            name=f"{which} matches the specification",
            solver=solver,
        ),
        prove_equivalent(aig, _lits(state, x), xi, name=f"{which} source preserved", solver=solver),
    ]
    return _finish(b, state, obligations, f"{which} {spec.name}", started)


# --------------------------------------------------------------------------
# SHA-256 structure
# --------------------------------------------------------------------------


def prove_round(
    spec: ShaSpec = SHA256,
    strategy: Strategy = DEFAULT,
    t_index: int = 0,
    solver: str = DEFAULT_SOLVER,
) -> CircuitProof:
    """One compression round, for every possible input state and message word."""
    started = time.time()
    b, st_in, w, st_out = build_round_circuit(spec, strategy, t=t_index)
    free = [q for word in st_in for q in word.qubits] + w.qubits
    state = symbolic_execute(b.circuit, free_qubits=free)
    aig = state.aig
    inputs = [_in_lits(state, word) for word in st_in]
    reference = S.round_step(aig, inputs, _in_lits(state, w), spec.k[t_index], spec)
    got = [lit for word in st_out for lit in _lits(state, word)]
    want = [lit for word in reference for lit in word]
    obligations = [
        prove_equivalent(aig, got, want, name="round output state", solver=solver),
        prove_equivalent(
            aig, _lits(state, w), _in_lits(state, w), name="W[t] preserved", solver=solver
        ),
    ]
    return _finish(
        b, state, obligations, f"{spec.name} round[{t_index}] {strategy.label()}", started
    )


def prove_schedule(
    spec: ShaSpec = SHA256,
    strategy: Strategy = DEFAULT,
    rounds: int | None = None,
    solver: str = DEFAULT_SOLVER,
) -> CircuitProof:
    """The message schedule reproduces W[t] for every message block."""
    started = time.time()
    rounds = spec.rounds if rounds is None else rounds
    reduced = spec.with_rounds(rounds)
    b = CircuitBuilder(f"schedule_{strategy.schedule}")
    sched = build_schedule(b, reduced, strategy)
    for t in range(rounds):
        sched.word(t)
    alive = (
        range(max(0, rounds - spec.block_words), rounds)
        if strategy.schedule == "rolling"
        else range(rounds)
    )
    registers = {t: sched.word(t) for t in alive}
    free = [q for word in sched.message for q in word.qubits]
    state = symbolic_execute(b.circuit, free_qubits=free)
    aig = state.aig
    block = [_in_lits(state, word) for word in sched.message]
    reference = S.message_schedule(aig, block, reduced)
    got, want = [], []
    for t, reg in registers.items():
        got += _lits(state, reg)
        want += reference[t]
    obligations = [
        prove_equivalent(
            aig,
            got,
            want,
            name=f"schedule words W[{min(alive)}..{max(alive)}] match the recurrence",
            solver=solver,
        )
    ]
    return _finish(
        b, state, obligations, f"{spec.name} schedule {strategy.schedule} r={rounds}", started
    )


def prove_compression(
    spec: ShaSpec = SHA256,
    strategy: Strategy = DEFAULT,
    rounds: int | None = None,
    solver: str = DEFAULT_SOLVER,
) -> CircuitProof:
    """A whole block compression, for every chaining value and message block."""
    started = time.time()
    rounds = spec.rounds if rounds is None else rounds
    comp = build_compression(spec, strategy, rounds=rounds)
    free = [q for word in comp.state + comp.message for q in word.qubits]
    state = symbolic_execute(comp.circuit, free_qubits=free)
    aig = state.aig
    h_in = [_in_lits(state, word) for word in comp.state]
    block = [_in_lits(state, word) for word in comp.message]
    reference = S.compress(aig, h_in, block, spec, rounds)

    got = [lit for word in comp.digest for lit in _lits(state, word)]
    want = [lit for word in reference for lit in word]
    obligations = [
        prove_equivalent(aig, got, want, name="digest matches the specification", solver=solver)
    ]
    if comp.uncomputed:
        obligations.append(
            prove_equivalent(
                aig,
                [lit for word in comp.message for lit in _lits(state, word)],
                [lit for word in block for lit in word],
                name="message block restored (garbage-free)",
                solver=solver,
            )
        )
        obligations.append(
            prove_equivalent(
                aig,
                [lit for word in comp.working for lit in _lits(state, word)],
                [CONST_FALSE] * sum(len(w) for w in comp.working),
                name="working registers returned to |0>",
                solver=solver,
            )
        )
    return _finish(
        comp.builder,
        state,
        obligations,
        f"{spec.name} compression r={rounds} {strategy.label()}",
        started,
    )


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


@dataclass
class ProofReport:
    """A batch of circuit proofs."""

    results: list[CircuitProof] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def proved(self) -> bool:
        return all(r.proved for r in self.results)

    @property
    def obligations(self) -> int:
        return sum(len(r.proofs) for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proved": self.proved,
            "obligations": self.obligations,
            "seconds": round(self.seconds, 2),
            "results": [r.to_dict() for r in self.results],
        }


def run_proofs(
    scope: str = "standard",
    solver: str = DEFAULT_SOLVER,
    progress=None,
    spec: ShaSpec = SHA256,
) -> ProofReport:
    """Run the proof suite.

    ``scope`` selects how far up the stack to go:

    ``quick``     primitives only
    ``standard``  primitives, sigma functions, one round of each layout
    ``full``      adds the message schedule and multi-round compressions
    """
    started = time.time()
    report = ProofReport()

    def add(fn, *args, **kwargs):
        result = fn(*args, solver=solver, **kwargs)
        report.results.append(result)
        if progress:
            progress(result)
        return result

    for adder in ("cdkm", "vbe", "gidney"):
        add(prove_adder, adder, spec.word_bits)
    for strategy in ("load", "vbe_const"):
        add(prove_const_adder, strategy, 8, 0xA5)
    for which in ("ch", "maj"):
        add(prove_boolean, which, spec.word_bits)

    if scope in ("standard", "full"):
        for which in ("big_sigma0", "big_sigma1", "small_sigma0", "small_sigma1"):
            add(prove_sigma, which, spec=spec)
        for layout in ("serial", "wide", "csa"):
            add(prove_round, spec=spec, strategy=Strategy(round_layout=layout))
        add(prove_round, spec=spec, strategy=Strategy(adder="gidney"))

    if scope == "full":
        for schedule in ("rolling", "store_all"):
            add(prove_schedule, spec=spec, strategy=Strategy(schedule=schedule))
        for rounds in (1, 2, 4):
            add(prove_compression, spec=spec, strategy=Strategy(), rounds=rounds)
        add(prove_compression, spec=spec, strategy=Strategy(adder="gidney"), rounds=2)
        add(
            prove_compression,
            spec=spec,
            strategy=Strategy(uncompute_working=True),
            rounds=1,
        )

    report.seconds = time.time() - started
    return report
