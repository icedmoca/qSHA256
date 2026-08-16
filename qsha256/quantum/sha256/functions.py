"""Reversible SHA-256 round functions: Sigma0, Sigma1, sigma0, sigma1, Ch, Maj.

The four sigma functions are the cheapest part of SHA-256 on a quantum computer
and the most expensive part to *misunderstand*.  Each is a XOR of rotations and
shifts, and both rotation and shift are free rewirings, so a sigma function
costs nothing but CNOTs -- **no Toffoli, no ancilla, no non-Clifford resource at
all**.  A resource estimate that charges Toffolis for rotations is overcounting
by a wide margin.

Ch and Maj are the opposite: they are the only sources of non-linearity in the
round function besides carry propagation in the adders, and each costs exactly
one Toffoli per bit (see :mod:`qsha256.quantum.primitives.boolean`).
"""

from __future__ import annotations

from ...spec import SHA256, ShaSpec
from ..primitives.boolean import ch_word_into, maj_word_into
from ..primitives.xor import xor_terms
from ..registers import CircuitBuilder, Word

__all__ = [
    "big_sigma0_into",
    "big_sigma1_into",
    "ch_into_word",
    "maj_into_word",
    "sigma_cnot_cost",
    "small_sigma0_into",
    "small_sigma1_into",
]


def big_sigma0_into(b: CircuitBuilder, x: Word, target: Word, spec: ShaSpec = SHA256) -> None:
    """``target ^= Sigma0(x)``.  SHA-256: 96 CNOTs, 0 Toffoli, 0 ancilla."""
    xor_terms(b, x, spec.big_sigma0, target)


def big_sigma1_into(b: CircuitBuilder, x: Word, target: Word, spec: ShaSpec = SHA256) -> None:
    """``target ^= Sigma1(x)``.  SHA-256: 96 CNOTs, 0 Toffoli, 0 ancilla."""
    xor_terms(b, x, spec.big_sigma1, target)


def small_sigma0_into(b: CircuitBuilder, x: Word, target: Word, spec: ShaSpec = SHA256) -> None:
    """``target ^= sigma0(x)``.  SHA-256: 32+32+29 = 93 CNOTs (SHR^3 loses 3)."""
    xor_terms(b, x, spec.small_sigma0, target)


def small_sigma1_into(b: CircuitBuilder, x: Word, target: Word, spec: ShaSpec = SHA256) -> None:
    """``target ^= sigma1(x)``.  SHA-256: 32+32+22 = 86 CNOTs (SHR^10 loses 10)."""
    xor_terms(b, x, spec.small_sigma1, target)


def ch_into_word(b: CircuitBuilder, x: Word, y: Word, z: Word, target: Word) -> None:
    """``target ^= Ch(x, y, z)``.  SHA-256: 32 Toffoli, 96 CNOT, 0 ancilla."""
    ch_word_into(b, x, y, z, target)


def maj_into_word(b: CircuitBuilder, x: Word, y: Word, z: Word, target: Word) -> None:
    """``target ^= Maj(x, y, z)``.  SHA-256: 32 Toffoli, 160 CNOT, 0 ancilla."""
    maj_word_into(b, x, y, z, target)


def sigma_cnot_cost(terms, word_bits: int) -> int:
    """Analytic CNOT cost of a sigma function -- ``w`` per rotation, ``w-n`` per shift."""
    total = 0
    for kind, amount in terms:
        total += word_bits if kind == "rotr" else word_bits - amount
    return total
