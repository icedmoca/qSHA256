"""A reduced SHA-like compression small enough to run Grover on, end to end.

**This is not SHA-256 and must never be described as such.**  It is a toy with
the same *architecture*: an ARX round built from the same reversible primitives
this project uses for the real thing -- modular addition, ``Ch``, ``Maj``, sigma
functions made of rotations and shifts, an accumulator register that is renamed
rather than moved.

Its purpose is that full SHA-256 Grover cannot be executed.  A single SHA-256
preimage oracle is ~2600 qubits; statevector simulation of that would need more
memory than exists.  So the repository separates two things it never conflates:

* the **real** SHA-256 circuits, which are constructed at full scale, executed
  on computational basis states, and measured for resources;
* this **toy**, which is small enough (~20 qubits) that superposition, phase
  kickback, amplitude amplification and measurement statistics can all actually
  be simulated and observed.

The toy round, with ``n`` state words ``s[0..n-1]``::

    T1 = s[n-1] + Sigma1(s[n-2]) + Ch(s[n-2], s[n-3], s[n-4]) + K[t] + W[t]
    T2 = Sigma0(s[0]) + Maj(s[0], s[1], s[2])
    s  <- (T1 + T2, s[0], s[1], ..., s[n-2])

which is SHA-256's round with the second four-word chain removed.  Like the real
round it allocates no permanent qubits: ``s[n-1]`` is the accumulator and is
simply renamed to ``s[0]`` afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...spec import Term, first_primes, frac_bits_of_root
from ..primitives.add import add_const_into, add_into
from ..primitives.boolean import ch_word_into, maj_word_into
from ..primitives.xor import xor_const, xor_terms
from ..registers import CircuitBuilder, Word
from ..strategies import DEFAULT, Strategy

__all__ = ["TOY_TINY", "ToyHashCircuit", "ToySpec", "build_toy_hash", "toy_compress"]


@dataclass(frozen=True)
class ToySpec:
    """Parameters of the reduced model.  Deliberately a separate type from
    :class:`~qsha256.spec.ShaSpec` so a toy can never be mistaken for SHA-256."""

    name: str
    word_bits: int
    state_words: int
    message_words: int
    rounds: int
    big_sigma0: tuple[Term, ...]
    big_sigma1: tuple[Term, ...]

    @property
    def mask(self) -> int:
        return (1 << self.word_bits) - 1

    @property
    def k(self) -> tuple[int, ...]:
        primes = first_primes(self.rounds)
        return tuple(frac_bits_of_root(p, 3, self.word_bits) for p in primes)

    @property
    def h0(self) -> tuple[int, ...]:
        primes = first_primes(self.state_words)
        return tuple(frac_bits_of_root(p, 2, self.word_bits) for p in primes)

    @property
    def digest_bits(self) -> int:
        return self.state_words * self.word_bits


#: ~20 qubits including the oracle's comparison ancillas -- comfortably
#: simulable with a statevector, with a 16-candidate search space.
TOY_TINY = ToySpec(
    name="toy-tiny",
    word_bits=2,
    state_words=4,
    message_words=2,
    rounds=2,
    big_sigma0=(("rotr", 1),),
    big_sigma1=(("rotr", 1), ("shr", 1)),
)


# --------------------------------------------------------------------------
# Classical reference for the toy
# --------------------------------------------------------------------------


def _apply_terms(x: int, terms: tuple[Term, ...], bits: int) -> int:
    mask = (1 << bits) - 1
    acc = 0
    for kind, amount in terms:
        if kind == "rotr":
            acc ^= ((x >> amount) | (x << (bits - amount))) & mask
        else:
            acc ^= (x & mask) >> amount
    return acc & mask


def toy_compress(message: list[int], spec: ToySpec = TOY_TINY) -> tuple[int, ...]:
    """Classical reference for :func:`build_toy_hash`, used to check the circuit."""
    mask = spec.mask
    s = list(spec.h0)
    k = spec.k
    for t in range(spec.rounds):
        w = message[t % spec.message_words] & mask
        t1 = (
            s[-1]
            + _apply_terms(s[-2], spec.big_sigma1, spec.word_bits)
            + ((s[-2] & s[-3]) ^ (~s[-2] & s[-4]))
            + k[t]
            + w
        ) & mask
        t2 = (
            _apply_terms(s[0], spec.big_sigma0, spec.word_bits)
            + ((s[0] & s[1]) ^ (s[0] & s[2]) ^ (s[1] & s[2]))
        ) & mask
        s = [(t1 + t2) & mask] + s[:-1]
    return tuple(s)


# --------------------------------------------------------------------------
# Quantum toy hash
# --------------------------------------------------------------------------


@dataclass
class ToyHashCircuit:
    builder: CircuitBuilder
    spec: ToySpec
    message: list[Word]
    state: list[Word]

    @property
    def circuit(self):
        return self.builder.circuit


def build_toy_hash(
    spec: ToySpec = TOY_TINY,
    strategy: Strategy = DEFAULT,
    builder: CircuitBuilder | None = None,
    message: list[Word] | None = None,
    state: list[Word] | None = None,
) -> ToyHashCircuit:
    """Build the reduced hash in place on its state registers.

    The state is initialised from the constant IV with X gates, so the circuit's
    only quantum input is the message.  The result is in-place and garbage-free:
    the state registers hold the digest and every ancilla returns to ``|0>``.

    ``state`` may be supplied so that repeated emissions of the hash (as in a
    multi-iteration Grover circuit) reuse the same registers instead of
    allocating a fresh set each time.  The registers must be in ``|0>``.
    """
    b = builder or CircuitBuilder(f"{spec.name}_hash")
    if message is None:
        message = b.add_words(spec.message_words, spec.word_bits, "M")
    if state is None:
        state = b.add_words(spec.state_words, spec.word_bits, "S")

    with b.section("load IV"):
        for reg, value in zip(state, spec.h0):
            xor_const(b, value, reg)

    s = list(state)
    for t in range(spec.rounds):
        with b.section(f"toy round[{t}]"):
            acc = s[-1]
            w = message[t % spec.message_words]
            with b.ancillas.borrow(spec.word_bits, "tmp") as tmp:
                xor_terms(b, s[-2], spec.big_sigma1, tmp)
                add_into(b, tmp, acc, strategy.adder)
                xor_terms(b, s[-2], spec.big_sigma1, tmp)

                ch_word_into(b, s[-2], s[-3], s[-4], tmp)
                add_into(b, tmp, acc, strategy.adder)
                ch_word_into(b, s[-2], s[-3], s[-4], tmp)

            add_const_into(b, spec.k[t], acc, strategy.adder, strategy.const_add)
            add_into(b, w, acc, strategy.adder)

            with b.ancillas.borrow(spec.word_bits, "tmp") as tmp:
                xor_terms(b, s[0], spec.big_sigma0, tmp)
                add_into(b, tmp, acc, strategy.adder)
                xor_terms(b, s[0], spec.big_sigma0, tmp)

                maj_word_into(b, s[0], s[1], s[2], tmp)
                add_into(b, tmp, acc, strategy.adder)
                maj_word_into(b, s[0], s[1], s[2], tmp)

        s = [acc] + s[:-1]

    return ToyHashCircuit(builder=b, spec=spec, message=message, state=s)
