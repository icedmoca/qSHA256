"""The SHA-256 specification, built directly as Boolean formulas.

This is a second, independent implementation of the classical model -- written
against :class:`~qsha256.formal.aig.AIG` literals instead of Python integers,
and sharing no code with :mod:`qsha256.classical.sha256`.

That independence is the point.  An equivalence check is only meaningful if the
two sides were built by different code: comparing a circuit against a spec that
shares its helper functions can hide a bug that lives in the shared part.  Here
the quantum circuit is symbolically executed by
:func:`~qsha256.formal.aig.symbolic_execute`, the specification is constructed
by the functions below, and the SAT solver compares the results.

Note in particular that the addition here is a plain textbook ripple-carry over
Boolean formulas.  It bears no structural resemblance to CDKM, VBE or the Gidney
adder, so proving them equal to it is a real check rather than a tautology.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..spec import SHA256, ShaSpec, Term
from .aig import AIG, CONST_FALSE, Lit

__all__ = [
    "Word",
    "add_const",
    "add_mod",
    "big_sigma0",
    "big_sigma1",
    "ch",
    "compress",
    "const_word",
    "input_word",
    "maj",
    "message_schedule",
    "rotr",
    "round_step",
    "shr",
    "small_sigma0",
    "small_sigma1",
    "xor_words",
]

#: A word is a list of literals, least-significant bit first.
Word = list[Lit]


def input_word(aig: AIG, bits: int, name: str) -> Word:
    return [aig.new_input(f"{name}[{i}]") for i in range(bits)]


def const_word(value: int, bits: int) -> Word:
    from .aig import CONST_TRUE

    return [CONST_TRUE if (value >> i) & 1 else CONST_FALSE for i in range(bits)]


def xor_words(aig: AIG, a: Word, b: Word) -> Word:
    return [aig.xor(x, y) for x, y in zip(a, b)]


def rotr(word: Word, n: int) -> Word:
    """``ROTR^n`` on an LSB-first word: result bit i is source bit (i+n) mod w."""
    w = len(word)
    n %= w
    return word[n:] + word[:n]


def shr(word: Word, n: int) -> Word:
    """``SHR^n``: the vacated high positions become constant false."""
    return word[n:] + [CONST_FALSE] * n


def add_mod(aig: AIG, a: Word, b: Word) -> Word:
    """Textbook ripple-carry addition modulo ``2^w``.

    Deliberately the most obvious possible implementation -- the carry out of
    the top bit is simply discarded, which is the reduction.
    """
    carry = CONST_FALSE
    out: Word = []
    for x, y in zip(a, b):
        out.append(aig.xor(aig.xor(x, y), carry))
        carry = aig.majority(x, y, carry)
    return out


def add_const(aig: AIG, a: Word, value: int) -> Word:
    return add_mod(aig, a, const_word(value, len(a)))


def ch(aig: AIG, x: Word, y: Word, z: Word) -> Word:
    """``(x AND y) XOR ((NOT x) AND z)``, written out literally."""
    from .aig import negate

    return [aig.xor(aig.and_(xi, yi), aig.and_(negate(xi), zi)) for xi, yi, zi in zip(x, y, z)]


def maj(aig: AIG, x: Word, y: Word, z: Word) -> Word:
    """``(x AND y) XOR (x AND z) XOR (y AND z)``, written out literally."""
    return [
        aig.xor(aig.xor(aig.and_(xi, yi), aig.and_(xi, zi)), aig.and_(yi, zi))
        for xi, yi, zi in zip(x, y, z)
    ]


def _apply_terms(aig: AIG, x: Word, terms: Sequence[Term]) -> Word:
    acc: Word = [CONST_FALSE] * len(x)
    for kind, amount in terms:
        view = rotr(x, amount) if kind == "rotr" else shr(x, amount)
        acc = xor_words(aig, acc, view)
    return acc


def big_sigma0(aig: AIG, x: Word, spec: ShaSpec = SHA256) -> Word:
    return _apply_terms(aig, x, spec.big_sigma0)


def big_sigma1(aig: AIG, x: Word, spec: ShaSpec = SHA256) -> Word:
    return _apply_terms(aig, x, spec.big_sigma1)


def small_sigma0(aig: AIG, x: Word, spec: ShaSpec = SHA256) -> Word:
    return _apply_terms(aig, x, spec.small_sigma0)


def small_sigma1(aig: AIG, x: Word, spec: ShaSpec = SHA256) -> Word:
    return _apply_terms(aig, x, spec.small_sigma1)


def message_schedule(aig: AIG, block: list[Word], spec: ShaSpec = SHA256) -> list[Word]:
    """``W[t] = sigma1(W[t-2]) + W[t-7] + sigma0(W[t-15]) + W[t-16]``."""
    from ..classical.sha256 import schedule_offsets

    o16, o15, o7, o2 = schedule_offsets(spec)
    w = list(block)
    for t in range(spec.block_words, spec.rounds):
        total = small_sigma1(aig, w[t - o2], spec)
        total = add_mod(aig, total, w[t - o7])
        total = add_mod(aig, total, small_sigma0(aig, w[t - o15], spec))
        total = add_mod(aig, total, w[t - o16])
        w.append(total)
    return w


def round_step(
    aig: AIG, state: Sequence[Word], w_t: Word, k_t: int, spec: ShaSpec = SHA256
) -> list[Word]:
    """One compression round, transcribed straight from FIPS 180-4."""
    a, b, c, d, e, f, g, h = state

    t1 = add_mod(aig, h, big_sigma1(aig, e, spec))
    t1 = add_mod(aig, t1, ch(aig, e, f, g))
    t1 = add_const(aig, t1, k_t)
    t1 = add_mod(aig, t1, w_t)

    t2 = add_mod(aig, big_sigma0(aig, a, spec), maj(aig, a, b, c))

    return [add_mod(aig, t1, t2), a, b, c, add_mod(aig, d, t1), e, f, g]


def compress(
    aig: AIG,
    state: Sequence[Word],
    block: list[Word],
    spec: ShaSpec = SHA256,
    rounds: int | None = None,
) -> list[Word]:
    """Full block compression: schedule, rounds, chaining addition."""
    rounds = spec.rounds if rounds is None else rounds
    reduced = spec.with_rounds(rounds)
    w = message_schedule(aig, block, reduced)
    k = spec.k
    working = list(state)
    for t in range(rounds):
        working = round_step(aig, working, w[t], k[t], spec)
    return [add_mod(aig, s, x) for s, x in zip(state, working)]
