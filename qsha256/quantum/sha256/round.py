"""One reversible SHA-256 compression round.

The classical round is::

    T1 = h + Sigma1(e) + Ch(e,f,g) + K[t] + W[t]
    T2 = Sigma0(a) + Maj(a,b,c)
    (a,b,c,d,e,f,g,h) <- (T1+T2, a, b, c, d+T1, e, f, g)

Making this reversible is easier than it looks, because of one observation:
**the round is already almost in-place.**  Six of the eight state words are
merely renamed, and the two that change (``a`` and ``e``) are computed by
*adding* things to registers that are about to be discarded anyway.

Concretely, the register currently called ``h`` is dead after this round -- its
old value is consumed by ``T1`` and never referenced again.  So ``h`` can be
used as the accumulator for ``T1``, and then for ``T1 + T2``, and then simply
*renamed* to ``a``.  Likewise ``d`` becomes the new ``e`` by adding ``T1`` into
it.  The register shuffle costs **zero gates**: it is a permutation of Python
references, exactly like the rotations in the sigma functions.

The result is a round that allocates **no permanent qubits at all**.  Its only
work space is a borrowed temporary for each sub-expression, returned to ``|0>``
and recycled before the round ends.  The seven modular additions per round are
what remain, and they are where essentially the whole cost lives.

Register lifecycle for one ``serial`` round
-------------------------------------------

======================  =======================================================
``h`` (accumulator)     ``h`` -> ``+Sigma1(e)`` -> ``+Ch`` -> ``+K`` -> ``+W``
                        = ``T1`` -> ``+Sigma0(a)`` -> ``+Maj`` = ``T1+T2``
                        -> renamed ``a``
``d``                   ``d`` -> ``+T1`` -> renamed ``e``
``a,b,c,e,f,g``         untouched, renamed ``b,c,d,f,g,h``
temporary (32 qubits)   holds each sub-expression in turn; uncomputed after
                        each addition; returned to the pool at round end
garbage produced        none: every temporary is uncomputed within the round
======================  =======================================================
"""

from __future__ import annotations

from ...spec import SHA256, ShaSpec
from ..primitives.add import add_const_into, add_into
from ..primitives.csa import sum_addends
from ..primitives.xor import xor_const
from ..registers import CircuitBuilder, Word
from ..strategies import DEFAULT, Strategy
from .functions import (
    big_sigma0_into,
    big_sigma1_into,
    ch_into_word,
    maj_into_word,
)

__all__ = ["ROUND_ADDITIONS", "RoundState", "apply_round", "build_round_circuit"]

#: Modular additions performed per round: Sigma1, Ch, K, W, d+=T1, Sigma0, Maj.
ROUND_ADDITIONS = 7

#: The eight working registers ``(a, b, c, d, e, f, g, h)``.
RoundState = tuple[Word, ...]


def apply_round(
    b: CircuitBuilder,
    state: RoundState,
    w_t: Word,
    k_t: int,
    t: int = 0,
    spec: ShaSpec = SHA256,
    strategy: Strategy = DEFAULT,
) -> RoundState:
    """Apply round ``t`` in place, returning the renamed register tuple.

    ``state`` is not mutated; the returned tuple is the same eight registers in
    their new roles.
    """
    if len(state) != 8:
        raise ValueError("SHA-256 rounds operate on exactly 8 state words")
    a, b_, c, d, e, f, g, h = state

    layouts = {"serial": _round_serial, "wide": _round_wide, "csa": _round_csa}
    with b.section(f"round[{t}]"):
        layouts[strategy.round_layout](b, a, b_, c, d, e, f, g, h, w_t, k_t, spec, strategy)

    # Free relabelling -- zero gates.
    return (h, a, b_, c, d, e, f, g)


def _round_serial(b, a, b_, c, d, e, f, g, h, w_t, k_t, spec, strategy) -> None:
    """Minimum-width layout: one recycled temporary, addends accumulated in turn."""
    width = spec.word_bits
    adder = strategy.adder

    with b.section("T1"):
        with b.ancillas.borrow(width, "tmp") as tmp:
            big_sigma1_into(b, e, tmp, spec)
            add_into(b, tmp, h, adder)
            big_sigma1_into(b, e, tmp, spec)  # uncompute (XOR fold is self-inverse)

            ch_into_word(b, e, f, g, tmp)
            add_into(b, tmp, h, adder)
            ch_into_word(b, e, f, g, tmp)  # uncompute (Ch embedding is an involution)

        add_const_into(b, k_t, h, adder, strategy.const_add)
        add_into(b, w_t, h, adder)
    # h now holds T1.

    with b.section("d+=T1"):
        add_into(b, h, d, adder)

    with b.section("T2"):
        with b.ancillas.borrow(width, "tmp") as tmp:
            big_sigma0_into(b, a, tmp, spec)
            add_into(b, tmp, h, adder)
            big_sigma0_into(b, a, tmp, spec)

            maj_into_word(b, a, b_, c, tmp)
            add_into(b, tmp, h, adder)
            maj_into_word(b, a, b_, c, tmp)
    # h now holds T1 + T2.


def _round_wide(b, a, b_, c, d, e, f, g, h, w_t, k_t, spec, strategy) -> None:
    """Lower-depth layout: the four sub-expressions live on disjoint qubits.

    ``Sigma1(e)``, ``Ch(e,f,g)``, ``Sigma0(a)`` and ``Maj(a,b,c)`` all depend
    only on registers the round does not modify, so all four can be computed
    before any accumulation begins.  Writing them to four separate temporaries
    lets the compiler schedule their gate layers concurrently, at the cost of
    ``4 * word_bits`` simultaneously-live ancillas instead of ``word_bits``.
    """
    width = spec.word_bits
    adder = strategy.adder

    t_s1 = b.ancillas.acquire(width, "s1")
    t_ch = b.ancillas.acquire(width, "ch")
    t_s0 = b.ancillas.acquire(width, "s0")
    t_mj = b.ancillas.acquire(width, "mj")

    with b.section("subexpressions"):
        big_sigma1_into(b, e, t_s1, spec)
        ch_into_word(b, e, f, g, t_ch)
        big_sigma0_into(b, a, t_s0, spec)
        maj_into_word(b, a, b_, c, t_mj)

    with b.section("T1"):
        add_into(b, t_s1, h, adder)
        add_into(b, t_ch, h, adder)
        add_const_into(b, k_t, h, adder, strategy.const_add)
        add_into(b, w_t, h, adder)

    with b.section("d+=T1"):
        add_into(b, h, d, adder)

    with b.section("T2"):
        add_into(b, t_s0, h, adder)
        add_into(b, t_mj, h, adder)

    with b.section("uncompute subexpressions"):
        big_sigma1_into(b, e, t_s1, spec)
        ch_into_word(b, e, f, g, t_ch)
        big_sigma0_into(b, a, t_s0, spec)
        maj_into_word(b, a, b_, c, t_mj)

    for word in (t_mj, t_s0, t_ch, t_s1):
        b.ancillas.release(word)


def _round_csa(b, a, b_, c, d, e, f, g, h, w_t, k_t, spec, strategy) -> None:
    """Carry-save layout: multi-operand addition with one carry propagation.

    Rests on a rearrangement of the round equations that removes ``h`` from the
    multi-operand sum entirely.  Writing ``R1 = Sigma1(e) + Ch + K + W`` and
    ``T2 = Sigma0(a) + Maj``::

        T1      = h + R1
        new e   = d + T1  = (d + h) + R1
        new a   = T1 + T2 = h + (R1 + T2)

    So ``h`` and ``d`` are only ever *accumulated into*, never consumed as CSA
    inputs -- which is what keeps the round free of permanent garbage.  ``R1``
    and ``R1 + T2`` are each formed by a carry-save tree in a borrowed register
    and uncomputed immediately after use.
    """
    width = spec.word_bits
    adder = strategy.adder

    t_s1 = b.ancillas.acquire(width, "s1")
    t_ch = b.ancillas.acquire(width, "ch")
    t_s0 = b.ancillas.acquire(width, "s0")
    t_mj = b.ancillas.acquire(width, "mj")
    t_k = b.ancillas.acquire(width, "k")

    with b.section("subexpressions"):
        big_sigma1_into(b, e, t_s1, spec)
        ch_into_word(b, e, f, g, t_ch)
        big_sigma0_into(b, a, t_s0, spec)
        maj_into_word(b, a, b_, c, t_mj)
        # The round constant is loaded with X gates -- one per set bit -- rather
        # than added, because a carry-save tree wants a register, not an adder.
        xor_const(b, k_t, t_k)

    with b.section("R1 = Sigma1 + Ch + K + W"):
        with sum_addends(b, [t_s1, t_ch, t_k, w_t], adder, "r1") as r1:
            with b.section("e' = d + h + R1"):
                add_into(b, h, d, adder)
                add_into(b, r1, d, adder)
            with b.section("a' = h + (R1 + Sigma0 + Maj)"):
                with sum_addends(b, [r1, t_s0, t_mj], adder, "r2") as r2:
                    add_into(b, r2, h, adder)

    with b.section("uncompute subexpressions"):
        xor_const(b, k_t, t_k)
        maj_into_word(b, a, b_, c, t_mj)
        big_sigma0_into(b, a, t_s0, spec)
        ch_into_word(b, e, f, g, t_ch)
        big_sigma1_into(b, e, t_s1, spec)

    for word in (t_k, t_mj, t_s0, t_ch, t_s1):
        b.ancillas.release(word)


def build_round_circuit(
    spec: ShaSpec = SHA256,
    strategy: Strategy = DEFAULT,
    t: int = 0,
) -> tuple[CircuitBuilder, RoundState, Word, RoundState]:
    """Build a standalone single-round circuit, for inspection and testing.

    Returns ``(builder, input_state, w_register, output_state)`` where the two
    state tuples are the same registers under their before/after names.
    """
    b = CircuitBuilder(f"{spec.name}_round{t}_{strategy.label()}")
    state = tuple(b.add_word(spec.word_bits, name) for name in "abcdefgh")
    w = b.add_word(spec.word_bits, "W")
    out = apply_round(b, state, w, spec.k[t], t, spec, strategy)
    return b, state, w, out
