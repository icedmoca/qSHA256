"""The complete reversible SHA-256 compression function.

Composing rounds is where the reversible-computing questions become concrete.
A single round leaves no garbage, but the compression function as a whole has a
structural problem: after 64 rounds the working registers hold the final
``a..h``, which must be *added into* the chaining state ``H``.  Those working
registers are then garbage -- entangled with the input, impossible to discard,
and fatal to a Grover oracle if left in place.

qSHA256 handles this the only way it can be handled: by running the whole
forward computation in reverse.  Because every gate emitted is a self-inverse
permutation gate, the inverse circuit is the recorded instruction span replayed
backwards.  After the chaining addition, that replay restores the working
registers to the copy of ``H`` they started from, and the final CNOT fan-out
clears them to ``|0>``.  The message-schedule registers are restored to the
original message block at the same time.

The cost of that is not hidden: **uncomputation roughly doubles the round cost**,
and the reports say so.  ``strategy.uncompute_working`` controls it, and both
variants are benchmarked, because the honest answer to "how much does one
SHA-256 evaluation cost on a quantum computer" depends entirely on whether you
need the answer to be usable inside a larger reversible computation.

Three distinct claims are kept separate throughout this module and everything
downstream of it:

* **constructed** -- the circuit object exists and its resources were measured;
* **simulated**  -- the circuit was executed and its output verified;
* **executed on hardware** -- has never happened here, for any circuit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...spec import SHA256, ShaSpec
from ..strategies import DEFAULT, Strategy
from ..primitives.add import ADDERS, add_into
from ..primitives.xor import xor_const, xor_word
from ..registers import CircuitBuilder, Word
from .round import apply_round
from .schedule import build_schedule

__all__ = ["CompressionCircuit", "build_compression"]


@dataclass
class CompressionCircuit:
    """A built compression circuit together with the registers that matter."""

    builder: CircuitBuilder
    spec: ShaSpec
    strategy: Strategy
    rounds: int
    #: Chaining-value registers.  Hold ``H_in`` on input; on output they hold
    #: ``H_out`` in the in-place variant, or are unchanged in the garbage-free
    #: variant (where the result lands in :attr:`digest` instead).
    state: list[Word]
    #: Message block registers; restored to the input block iff ``uncomputed``.
    message: list[Word]
    #: Where the compression output ends up -- ``state`` in place, or a separate
    #: register when uncomputation is enabled.
    digest: list[Word] = field(default_factory=list)
    #: Working registers ``a..h``; ``|0>`` on output iff ``uncomputed``.
    working: list[Word] = field(default_factory=list)
    #: True when work registers, schedule and message are all restored, leaving
    #: only the digest -- the precondition for use inside a Grover oracle.
    uncomputed: bool = False

    @property
    def circuit(self):
        return self.builder.circuit

    def summary(self) -> str:
        b = self.builder
        return (
            f"{self.spec.name} compression, {self.rounds} rounds, {self.strategy.label()}: "
            f"{self.circuit.num_qubits} qubits "
            f"({b.data_qubits} data + {b.ancilla_qubits} ancilla), "
            f"{sum(self.circuit.count_ops().values())} gates"
        )


def build_compression(
    spec: ShaSpec = SHA256,
    strategy: Strategy = DEFAULT,
    rounds: int | None = None,
    initial_state: tuple[int, ...] | None = None,
    message_constants: dict[int, int] | None = None,
    builder: CircuitBuilder | None = None,
    message: list[Word] | None = None,
    output: str | None = None,
    uncompute: bool | None = None,
) -> CompressionCircuit:
    """Build the reversible compression function.

    Parameters
    ----------
    spec:
        Which member of the SHA-256 family to build.
    strategy:
        Architecture choices; see :class:`~qsha256.quantum.strategies.Strategy`.
    rounds:
        Round count, for scaling studies.  ``None`` means ``spec.rounds``.
    initial_state:
        If given, the chaining registers are initialised from ``|0>`` to this
        classical value with X gates instead of being treated as free inputs.
        This is what a hash of a *known* IV does, and it is strictly cheaper
        than loading the IV from outside.
    message_constants:
        ``{word_index: value}`` for message words fixed at build time -- e.g. the
        padding words of a one-block message.  Fixed words are loaded with X
        gates rather than left as input qubits.

    Notes
    -----
    Uncomputation (``strategy.uncompute_working``) is implemented by replaying
    the recorded forward span in reverse, which requires every gate to be
    self-inverse.  The QFT adder is not, so it cannot be combined with
    uncomputation; that combination raises rather than silently producing a
    wrong circuit.
    """
    rounds = spec.rounds if rounds is None else rounds
    if not 1 <= rounds <= spec.rounds:
        raise ValueError(f"rounds must be in 1..{spec.rounds} for {spec.name}")

    # `output` and `uncompute` are independent knobs that the strategy bundles
    # together by default.  The oracle overrides them: it wants the result in a
    # separate register but *not* an internal uncomputation, because it wraps
    # the whole thing in its own inverse.  Doing both would run the rounds four
    # times where two suffice.
    if output is None:
        output = "digest" if strategy.uncompute_working else "in_place"
    if uncompute is None:
        uncompute = strategy.uncompute_working
    if output not in ("in_place", "digest"):
        raise ValueError(f"output must be 'in_place' or 'digest', got {output!r}")
    if uncompute and output != "digest":
        raise ValueError("uncompute=True requires output='digest' (see module docstring)")

    if uncompute and not ADDERS[strategy.adder].basis_simulable:
        raise ValueError(
            f"adder {strategy.adder!r} is not built from self-inverse gates, "
            "so uncompute_working=True cannot be realised by reverse replay"
        )

    width = spec.word_bits
    b = builder or CircuitBuilder(f"{spec.name}_compress_r{rounds}_{strategy.label()}")

    # -- registers ---------------------------------------------------------
    state = b.add_words(spec.state_words, width, "H")
    if message is None:
        message = b.add_words(spec.block_words, width, "M")
    elif len(message) != spec.block_words:
        raise ValueError(f"message must supply {spec.block_words} registers")
    working = b.add_words(spec.state_words, width, "wv")

    with b.section("load constants"):
        if initial_state is not None:
            if len(initial_state) != spec.state_words:
                raise ValueError("initial_state must have spec.state_words entries")
            for reg, value in zip(state, initial_state):
                xor_const(b, value, reg)
        for index, value in (message_constants or {}).items():
            xor_const(b, value, message[index])

    forward_start = len(b.circuit.data)

    with b.section("copy H into working registers"):
        # working starts in |0>, so a CNOT fan-out is an exact copy.
        for src, dst in zip(state, working):
            xor_word(b, src, dst)

    schedule = build_schedule(b, spec.with_rounds(rounds), strategy, message)

    with b.section("rounds"):
        st = tuple(working)
        for t in range(rounds):
            st = apply_round(b, st, schedule.word(t), spec.k[t], t, spec, strategy)

    forward_end = len(b.circuit.data)

    if output == "in_place":
        # Cheapest forward evaluation: accumulate in place and keep the garbage.
        # `st` is the round-permuted view of the same eight registers.
        with b.section("chaining addition (in place)"):
            for i in range(spec.state_words):
                add_into(b, st[i], state[i], strategy.adder)
        return CompressionCircuit(
            builder=b,
            spec=spec,
            strategy=strategy,
            rounds=rounds,
            state=state,
            message=message,
            digest=state,
            working=list(working),
            uncomputed=False,
        )

    # Garbage-free evaluation.  The result must go somewhere *other* than `H`:
    # the uncomputation replays the forward span in reverse, and that span reads
    # `H` (to copy it into the working registers), so `H` must still hold its
    # input value when the replay happens.  Writing the result into `H` in place
    # would break the copy's inverse -- which is exactly the bug this structure
    # exists to avoid.
    digest = b.add_words(spec.state_words, width, "D")
    with b.section("chaining addition (into digest register)"):
        for i in range(spec.state_words):
            xor_word(b, st[i], digest[i])  # digest starts |0>, so this is a copy
            add_into(b, state[i], digest[i], strategy.adder)

    if uncompute:
        with b.section("uncompute (inverse of copy + schedule + rounds)"):
            b.append_reversed(forward_start, forward_end)

    return CompressionCircuit(
        builder=b,
        spec=spec,
        strategy=strategy,
        rounds=rounds,
        state=state,
        message=message,
        digest=digest,
        working=list(working),
        uncomputed=uncompute,
    )
