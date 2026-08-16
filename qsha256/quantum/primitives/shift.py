"""Logical right shift in a reversible circuit.

``SHR^n`` is the one place in SHA-256 where the classical algorithm genuinely
destroys information: the low ``n`` bits are discarded and cannot be recovered
from the result.  A reversible circuit cannot destroy information, so the shift
has to be handled rather than merely translated.

The resolution is that **qSHA256 never shifts a register; it only reads a
shifted view of one.**  ``SHR^n`` appears in SHA-256 exclusively inside the
small sigma functions:

    sigma0(x) = ROTR^7(x) XOR ROTR^18(x) XOR SHR^3(x)

and the result of ``sigma0`` is always XORed into a *separate* target register.
The source register ``x`` is untouched throughout, so the "discarded" low bits
are still sitting in ``x``.  Nothing is erased; the shifted view simply declines
to read them, and the vacated high positions are bound to the constant zero.

Consequences for the resource model:

* A shift costs **zero gates**, like a rotation.
* A shift is *cheaper* than a rotation when XORed into a target: ``SHR^n`` on a
  ``w``-bit word contributes ``w - n`` CNOTs rather than ``w``, because the
  ``n`` constant-zero positions emit nothing.
* Shifting a register **in place** would be irreversible and is not offered.
  :func:`in_place_shift_is_reversible` exists to say so explicitly.
"""

from __future__ import annotations

from ..registers import Word

__all__ = [
    "in_place_shift_is_reversible",
    "shift_cnot_cost",
    "shift_gate_cost",
    "shr",
]


def shr(word: Word, n: int) -> Word:
    """``SHR^n(word)`` -- a view whose top ``n`` positions are constant zero."""
    return word.shr(n)


def shift_gate_cost() -> int:
    """The gate cost of forming a shifted view: zero."""
    return 0


def shift_cnot_cost(width: int, amount: int) -> int:
    """CNOTs needed to XOR ``SHR^amount(x)`` into a target: ``width - amount``."""
    if not 0 <= amount < width:
        raise ValueError(f"shift {amount} out of range for {width}-bit word")
    return width - amount


def in_place_shift_is_reversible() -> bool:
    """``False`` -- and the reason is the point.

    An in-place ``x <- SHR^n(x)`` maps ``2^n`` distinct inputs to each output.
    No unitary can do that.  Realising one would require either keeping the
    displaced bits in ancilla (making it a rotation into scratch space) or
    measuring and discarding them (making it non-unitary and, in an oracle,
    destroying the interference Grover depends on).
    """
    return False
