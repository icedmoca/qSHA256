"""Reversible XOR and constant injection.

XOR is the one classical Boolean operation that is *already* reversible when
written as an in-place update: ``b ^= a`` is its own inverse.  That makes CNOT
the cheapest useful gate in the whole construction and explains why SHA-256's
sigma functions -- pure XOR folds of rotations -- carry no Toffoli cost at all.
"""

from __future__ import annotations

from typing import Sequence

from ...spec import Term
from ..registers import CircuitBuilder, Word

__all__ = ["xor_word", "xor_const", "copy_word", "xor_terms", "swap_words"]


def xor_word(b: CircuitBuilder, src: Word, dst: Word) -> None:
    """``dst ^= src``, one CNOT per bit where ``src`` is not constant zero.

    Self-inverse.  Constant-zero source positions (from a shift view) emit
    nothing, which is both correct and the reason ``SHR`` is cheaper than
    ``ROTR`` in CNOT count despite both being free as wiring.
    """
    if len(src) != len(dst):
        raise ValueError(f"width mismatch: {len(src)} vs {len(dst)}")
    for s, d in zip(src, dst):
        if s is None:
            continue
        if d is None:
            raise ValueError("cannot XOR into a constant-zero bit position")
        b.cx(s, d)


def copy_word(b: CircuitBuilder, src: Word, dst: Word) -> None:
    """Copy ``src`` into a register known to be ``|0>``.

    This is *not* quantum cloning: it is the CNOT fan-out that entangles ``dst``
    with ``src`` in the computational basis.  It is only a "copy" when ``src``
    holds a basis state; on a superposition it produces the correlated state
    ``sum_x |x>|x>``, which is exactly what a reversible embedding needs.
    """
    xor_word(b, src, dst)


def xor_const(b: CircuitBuilder, value: int, dst: Word) -> None:
    """``dst ^= value`` for a classical constant, one X gate per set bit."""
    for i, d in enumerate(dst):
        if (value >> i) & 1:
            if d is None:
                raise ValueError("constant has a set bit over a constant-zero position")
            b.x(d)


def xor_terms(b: CircuitBuilder, src: Word, terms: Sequence[Term], dst: Word) -> None:
    """XOR a sigma function's rotate/shift terms of ``src`` into ``dst``.

    ``xor_terms(x, (("rotr", 7), ("rotr", 18), ("shr", 3)), out)`` computes
    ``out ^= sigma0(x)``.  Every term is a free rewiring followed by a CNOT
    layer, so the cost is exactly the number of non-zero source bits summed
    over terms -- no ancilla, no Toffoli, and the whole thing is self-inverse.
    """
    for kind, amount in terms:
        view = src.rotr(amount) if kind == "rotr" else src.shr(amount)
        xor_word(b, view, dst)


def swap_words(b: CircuitBuilder, x: Word, y: Word) -> None:
    """Exchange two registers with SWAP gates.

    Almost always unnecessary: a register exchange is normally done by swapping
    the Python references, which costs nothing.  Provided only for the rare case
    where a *physical* exchange is genuinely required.
    """
    if len(x) != len(y):
        raise ValueError("width mismatch")
    for a, c in zip(x, y):
        if a is None or c is None:
            raise ValueError("cannot swap constant-zero positions")
        b.swap(a, c)
