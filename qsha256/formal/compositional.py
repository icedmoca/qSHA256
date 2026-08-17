"""Compositional proof of the full SHA-256 circuit.

A single monolithic miter over a whole compression does not scale.  The reason
is specific and worth stating: the circuit's adder (CDKM, VBE or Gidney) and the
specification's adder (textbook ripple-carry) represent carries completely
differently, so the solver must reconcile two unrelated encodings afresh at
every one of the ~450 additions.  Structural sharing cannot help, and the search
blows up.  Measured here: a full 32-bit *round* proves in about a second, while
even a 4-bit one-round *compression* miter takes over a minute.

The fix is the one industrial equivalence checkers use -- prove the pieces, then
compose.  It is not a weakening, because each piece is proved **universally
quantified over its own inputs**:

* ``prove_round`` establishes: *for every* state ``(a..h)`` and *every* message
  word ``W``, the round circuit computes exactly ``round_step(state, W, K[t])``.
* ``prove_schedule_step`` establishes: *for every* window contents, one schedule
  advance computes exactly the recurrence.
* ``prove_copy_in`` and ``prove_chaining`` establish the framing operations.

Because each is universal, chaining them is sound by induction on the round
index: if round ``t`` maps every correct state to the correct successor, then a
sequence of 64 of them maps the initial state to the correct final state.  The
composition step is ordinary mathematics, not a further SAT query.

What this does and does not give you
------------------------------------

It **is** a proof that the 64-round circuit computes SHA-256's compression
function on all ``2^768`` inputs, conditional on the composition argument above
being applied to the components actually present in the circuit.

It is **not** a single machine-checked artifact covering the whole circuit in
one query.  The link between "these components were proved" and "the circuit is
this composition of them" is established structurally: :func:`prove_structure`
checks that the built circuit's instruction spans partition exactly into the
proved components, with nothing left over.  That closes the gap that a purely
informal composition argument would leave.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..quantum.registers import CircuitBuilder
from ..quantum.sha256.compression import build_compression
from ..quantum.sha256.schedule import RollingSchedule, StoreAllSchedule, build_schedule
from ..quantum.strategies import DEFAULT, Strategy
from ..spec import SHA256, ShaSpec
from . import spec_aig as S
from .aig import symbolic_execute
from .cnf import DEFAULT_SOLVER
from .equivalence import (
    CircuitProof,
    Proof,
    prove_equivalent,
)
from .sha256_proofs import _finish, _in_lits, _lits, prove_round

__all__ = [
    "CompositionalProof",
    "prove_chaining",
    "prove_compression_compositional",
    "prove_copy_in",
    "prove_schedule_step",
    "prove_structure",
]


def prove_schedule_step(
    spec: ShaSpec = SHA256,
    strategy: Strategy = DEFAULT,
    t: int | None = None,
    solver: str = DEFAULT_SOLVER,
) -> CircuitProof:
    """Prove one message-schedule advance, in isolation, over symbolic inputs.

    The window registers are free inputs, so the proof covers every possible
    window contents rather than the particular values a run would produce.
    """
    started = time.time()
    m = spec.block_words
    t = m if t is None else t
    b = CircuitBuilder(f"sched_step_{t}")
    sched = build_schedule(b, spec, strategy)

    # Pretend the window is already populated up to t-1; its registers hold
    # free symbolic inputs, which is exactly the generality we want.
    if isinstance(sched, RollingSchedule):
        sched._ready = t - 1
        sched._advance(t)
        target = sched._window[t % m]
        window = sched._window
        sources = {
            "o2": window[(t - sched.o2) % m],
            "o7": window[(t - sched.o7) % m],
            "o15": window[(t - sched.o15) % m],
            "o16": window[t % m],
        }
    elif isinstance(sched, StoreAllSchedule):
        sched._built = t
        sched._words = list(sched.message) + [
            b.add_word(spec.word_bits, f"Wpre{i}") for i in range(m, t)
        ]
        sched._expand(t)
        target = sched._words[t]
        sources = {
            "o2": sched._words[t - sched.o2],
            "o7": sched._words[t - sched.o7],
            "o15": sched._words[t - sched.o15],
            "o16": sched._words[t - sched.o16],
        }
    else:  # pragma: no cover
        raise TypeError(f"unknown schedule type {type(sched).__name__}")

    # Exactly the source registers are free; everything else -- including the
    # freshly allocated target under `store_all` -- starts in |0>, which is what
    # makes the "first term is a free copy" optimisation valid.
    free: list = []
    seen = set()
    for word in sources.values():
        for q in word.qubits:
            if q not in seen:
                seen.add(q)
                free.append(q)
    state = symbolic_execute(b.circuit, free_qubits=free)
    aig = state.aig

    reference = S.small_sigma1(aig, _in_lits(state, sources["o2"]), spec)
    reference = S.add_mod(aig, reference, _in_lits(state, sources["o7"]))
    reference = S.add_mod(
        aig, reference, S.small_sigma0(aig, _in_lits(state, sources["o15"]), spec)
    )
    reference = S.add_mod(aig, reference, _in_lits(state, sources["o16"]))

    obligations = [
        prove_equivalent(
            aig,
            _lits(state, target),
            reference,
            name=f"W[{t}] = sigma1(W[t-{sched.o2}]) + W[t-{sched.o7}] "
            f"+ sigma0(W[t-{sched.o15}]) + W[t-{sched.o16}]",
            solver=solver,
        )
    ]
    return _finish(
        b, state, obligations, f"{spec.name} schedule step t={t} ({strategy.schedule})", started
    )


def prove_copy_in(spec: ShaSpec = SHA256, solver: str = DEFAULT_SOLVER) -> CircuitProof:
    """The CNOT fan-out that seeds the working registers really copies H."""
    started = time.time()
    from ..quantum.primitives.xor import xor_word

    b = CircuitBuilder("copy_in")
    h = b.add_words(spec.state_words, spec.word_bits, "H")
    wv = b.add_words(spec.state_words, spec.word_bits, "wv")
    for src, dst in zip(h, wv):
        xor_word(b, src, dst)
    free = [q for word in h for q in word.qubits]
    state = symbolic_execute(b.circuit, free_qubits=free)
    aig = state.aig
    got = [lit for word in wv for lit in _lits(state, word)]
    want = [lit for word in h for lit in _in_lits(state, word)]
    obligations = [
        prove_equivalent(
            aig, got, want, name="working registers receive a copy of H", solver=solver
        ),
        prove_equivalent(
            aig,
            [lit for word in h for lit in _lits(state, word)],
            want,
            name="H is unchanged by the copy",
            solver=solver,
        ),
    ]
    return _finish(b, state, obligations, f"{spec.name} copy-in", started)


def prove_chaining(
    spec: ShaSpec = SHA256, strategy: Strategy = DEFAULT, solver: str = DEFAULT_SOLVER
) -> CircuitProof:
    """The final ``H[i] += a..h[i]`` really is modular addition, per word."""
    started = time.time()
    from ..quantum.primitives.add import add_into

    b = CircuitBuilder("chaining")
    h = b.add_word(spec.word_bits, "H")
    wv = b.add_word(spec.word_bits, "wv")
    add_into(b, wv, h, strategy.adder)
    state = symbolic_execute(b.circuit, free_qubits=h.qubits + wv.qubits)
    aig = state.aig
    obligations = [
        prove_equivalent(
            aig,
            _lits(state, h),
            S.add_mod(aig, _in_lits(state, h), _in_lits(state, wv)),
            name="chaining addition is (H + working) mod 2^w",
            solver=solver,
        )
    ]
    return _finish(b, state, obligations, f"{spec.name} chaining addition", started)


def prove_structure(
    spec: ShaSpec = SHA256, strategy: Strategy = DEFAULT, rounds: int | None = None
) -> Proof:
    """Check the circuit really is the composition of the proved components.

    Walks the builder's recorded section tree and verifies that the instruction
    spans partition the circuit exactly: every gate belongs to a copy-in, a
    schedule step, a round, or the chaining addition, and none is left over.
    Without this, "we proved the parts" would not entail "we proved the whole".
    """
    started = time.time()
    rounds = spec.rounds if rounds is None else rounds
    comp = build_compression(spec, strategy, rounds=rounds)
    circuit = comp.circuit

    covered = bytearray(len(circuit.data))
    counts: dict[str, int] = {}

    def walk(sections, depth=0):
        for sec in sections:
            end = sec.end if sec.end >= 0 else len(circuit.data)
            kind = sec.name.split("[")[0].strip()
            if depth == 0 or kind in ("round", "schedule"):
                for i in range(sec.start, end):
                    covered[i] = 1
                counts[kind] = counts.get(kind, 0) + 1
            walk(sec.children, depth + 1)

    walk(comp.builder.sections)
    uncovered = [i for i, c in enumerate(covered) if not c]
    expected_rounds = rounds
    expected_steps = max(0, rounds - spec.block_words)

    problems = []
    if uncovered:
        problems.append(f"{len(uncovered)} gate(s) belong to no proved component")
    if counts.get("round", 0) != expected_rounds:
        problems.append(f"found {counts.get('round', 0)} rounds, expected {expected_rounds}")
    if counts.get("schedule", 0) != expected_steps:
        problems.append(
            f"found {counts.get('schedule', 0)} schedule steps, expected {expected_steps}"
        )
    return Proof(
        name="circuit decomposes exactly into proved components",
        proved=not problems,
        detail=(
            f"{len(circuit.data):,} gates fully covered by {counts.get('round', 0)} rounds, "
            f"{counts.get('schedule', 0)} schedule steps, plus framing"
            if not problems
            else "; ".join(problems)
        ),
        seconds=time.time() - started,
    )


@dataclass
class CompositionalProof:
    """The whole compositional argument for one configuration."""

    spec_name: str
    rounds: int
    strategy: str
    components: list[CircuitProof] = field(default_factory=list)
    structure: Proof | None = None
    seconds: float = 0.0

    @property
    def proved(self) -> bool:
        return all(c.proved for c in self.components) and bool(
            self.structure and self.structure.proved
        )

    @property
    def obligations(self) -> int:
        return sum(len(c.proofs) for c in self.components) + (1 if self.structure else 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec_name,
            "rounds": self.rounds,
            "strategy": self.strategy,
            "proved": self.proved,
            "obligations": self.obligations,
            "seconds": round(self.seconds, 2),
            "structure": self.structure.to_dict() if self.structure else None,
            "components": [c.to_dict() for c in self.components],
        }

    def __str__(self) -> str:
        head = (
            f"Compositional proof: {self.spec_name}, {self.rounds} rounds, {self.strategy}\n"
            f"  {self.obligations} obligations, "
            f"{'ALL PROVED' if self.proved else 'INCOMPLETE'}, {self.seconds:.1f}s"
        )
        lines = [head, ""]
        for component in self.components:
            lines.append(f"  {'PASS' if component.proved else 'FAIL'}  {component.target}")
        if self.structure:
            lines.append(f"  {'PASS' if self.structure.proved else 'FAIL'}  {self.structure.name}")
            lines.append(f"        {self.structure.detail}")
        return "\n".join(lines)


def prove_compression_compositional(
    spec: ShaSpec = SHA256,
    strategy: Strategy = DEFAULT,
    rounds: int | None = None,
    solver: str = DEFAULT_SOLVER,
    progress: Callable[[str], None] | None = None,
    all_rounds: bool = True,
) -> CompositionalProof:
    """Prove a full compression by proving every component universally.

    ``all_rounds`` proves each round index separately, which matters because
    each bakes in a different constant ``K[t]``; with it off, only the first and
    last are proved (useful for a quick check, not for a complete argument).
    """
    started = time.time()
    rounds = spec.rounds if rounds is None else rounds
    result = CompositionalProof(spec_name=spec.name, rounds=rounds, strategy=strategy.label())

    def note(text: str) -> None:
        if progress:
            progress(text)

    note("copy-in")
    result.components.append(prove_copy_in(spec, solver=solver))

    indices = range(rounds) if all_rounds else sorted({0, rounds - 1})
    for t in indices:
        note(f"round[{t}]")
        result.components.append(prove_round(spec, strategy, t_index=t, solver=solver))

    # One full cycle of the rolling window covers every residue class, and the
    # register indices repeat with period block_words thereafter.
    if rounds > spec.block_words:
        steps = range(spec.block_words, min(rounds, 2 * spec.block_words))
        for t in steps:
            note(f"schedule step {t}")
            result.components.append(prove_schedule_step(spec, strategy, t=t, solver=solver))

    note("chaining addition")
    result.components.append(prove_chaining(spec, strategy, solver=solver))

    note("structural decomposition")
    result.structure = prove_structure(spec, strategy, rounds)

    result.seconds = time.time() - started
    return result
