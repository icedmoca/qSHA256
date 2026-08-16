"""Circular rotation as a zero-gate wiring transformation.

SHA-256 rotates constantly -- six of them per compression round -- and in a
naive resource estimate that looks expensive.  It is not.  ``ROTR^n`` is a fixed
permutation of bit positions, and in a circuit model where we control the
mapping from logical bits to physical qubits, a fixed permutation of *labels*
requires no gates at all.

This is a real effect, not an accounting trick, but it comes with a condition
that this module makes explicit:

* A rotated word may be **read** (used as a control, XORed into a target) for
  free, because reading through a relabelling is just addressing different
  qubits.
* Rotating a register **in place** -- so that subsequent code sees the register
  itself as rotated -- is also free, provided the rotation is tracked in the
  compiler's wire map rather than realised with SWAPs.  qSHA256 only ever needs
  the first case.
* On hardware with restricted connectivity the relabelling is *not* free: it is
  absorbed into routing/SWAP overhead by the physical compiler.  That cost is a
  layout concern, deliberately excluded from the logical resource model and
  flagged as an assumption in every report.

If you want to see the alternative accounting, :func:`rotate_in_place_cost`
gives the SWAP count a naive implementation would pay.
"""

from __future__ import annotations

from ..registers import Word

__all__ = ["rotate_gate_cost", "rotate_in_place_cost", "rotl", "rotr"]


def rotr(word: Word, n: int) -> Word:
    """``ROTR^n(word)`` -- a view, costing zero gates."""
    return word.rotr(n)


def rotl(word: Word, n: int) -> Word:
    """``ROTL^n(word)`` -- a view, costing zero gates."""
    return word.rotr(len(word) - (n % len(word)))


def rotate_gate_cost() -> int:
    """The gate cost of a rotation in the logical model: zero."""
    return 0


def rotate_in_place_cost(width: int, amount: int) -> int:
    """SWAP count if a rotation were physically realised instead of rewired.

    A cyclic shift by ``n`` on ``w`` wires decomposes into ``w - gcd(w, n)``
    transpositions.  Reported for contrast only; qSHA256 never emits these.
    """
    from math import gcd

    amount %= width
    if amount == 0:
        return 0
    return width - gcd(width, amount)
