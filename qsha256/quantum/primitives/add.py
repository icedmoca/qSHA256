"""Reversible modular addition -- the cost centre of quantum SHA-256.

SHA-256 performs seven 32-bit modular additions per compression round plus three
per message-schedule word, so the adder choice propagates directly into every
headline number the project reports.  Three published designs are implemented so
the tradeoff can be *measured* rather than asserted:

===========  =========  ==================  ==============  ========================
Adder        Ancillas   Toffoli             T-count         Reference
===========  =========  ==================  ==============  ========================
``cdkm``     1          ``2n``              ``14n``         Cuccaro et al. 2004 [1]
``vbe``      ``n``      ``4(n-1)``          ``28(n-1)``     Vedral et al. 1996 [2]
``qft``      0          0                   synthesis       Draper 2000 [3]
``gidney``   ``n-1``    0 (temporary ANDs)  ``4(n-1)``      Gidney 2018 [4]
===========  =========  ==================  ==============  ========================

The ``gidney`` adder is the T-count winner by a wide margin: 124 T gates for a
32-bit addition against CDKM's 448. It replaces every Toffoli with a
compute/uncompute **temporary AND** pair, where the compute costs 4 T and the
uncompute -- being a measurement plus a Clifford correction -- costs none. The
price is ``n-1`` ancillas and a hardware requirement for mid-circuit measurement
with feedforward. See :mod:`qsha256.quantum.primitives.temporary_and`.

The QFT adder trades all Toffolis for ``O(n^2)`` controlled-phase rotations.
That is not free: on a fault-tolerant machine each arbitrary-angle rotation must
be synthesised from Clifford+T at some target precision, which typically costs
*more* T gates than the Toffoli it replaced.  It is included as an honest point
of comparison and its T-cost is reported through an explicit, clearly-labelled
rotation-synthesis model rather than being quietly counted as zero.

All three compute ``b <- (a + b) mod 2^n`` in place on ``b``, leaving ``a``
unchanged and every ancilla returned to ``|0>``.  The test suite verifies this
exhaustively for ``n = 2..5`` over all input pairs, in both directions.

References
----------
[1] S. Cuccaro, T. Draper, S. Kutin, D. Petrie Moulton,
    "A new quantum ripple-carry addition circuit", arXiv:quant-ph/0410184 (2004).
[2] V. Vedral, A. Barenco, A. Ekert,
    "Quantum Networks for Elementary Arithmetic Operations",
    Phys. Rev. A 54, 147 (1996), arXiv:quant-ph/9511018.
[3] T. G. Draper, "Addition on a Quantum Computer", arXiv:quant-ph/0008033 (2000).
[4] C. Gidney, "Halving the cost of quantum addition", Quantum 2, 74 (2018),
    arXiv:1709.06648.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from ..registers import CircuitBuilder, Word
from .xor import xor_const

__all__ = [
    "ADDERS",
    "CONST_ADD_STRATEGIES",
    "Adder",
    "add_const_into",
    "add_into",
    "get_adder",
]


# --------------------------------------------------------------------------
# CDKM ripple-carry (Cuccaro, Draper, Kutin, Moulton 2004)
# --------------------------------------------------------------------------


def _maj(b: CircuitBuilder, c, y, x) -> None:
    """MAJ: ``(c, y, x) -> (c^x, y^x, MAJ(x, y, c))``.

    The third wire ends up holding the outgoing carry, which is why the carry
    ripples through the addend register rather than through ancilla.
    """
    b.cx(x, y)
    b.cx(x, c)
    b.ccx(c, y, x)


def _uma(b: CircuitBuilder, c, y, x) -> None:
    """UMA: the inverse of MAJ composed with sum extraction.

    ``(c^x, y^x, carry) -> (c, x^y^c, x)``: restores the addend bit and the
    incoming carry while leaving the sum bit in place of ``y``.
    """
    b.ccx(c, y, x)
    b.cx(x, c)
    b.cx(c, y)


def _cdkm_add(b: CircuitBuilder, a: Word, target: Word, anc: Word) -> None:
    """``target += a  (mod 2^n)`` -- 2n Toffoli, 4n CNOT, 1 ancilla."""
    n = len(a)
    carry_in = [anc[0]] + list(a.bits[: n - 1])
    for i in range(n):
        _maj(b, carry_in[i], target[i], a[i])
    # The outgoing carry now sits in a[n-1] and is simply not used: discarding
    # it *is* the reduction mod 2^n.  The UMA sweep restores a[n-1] regardless.
    for i in reversed(range(n)):
        _uma(b, carry_in[i], target[i], a[i])


def _cdkm_ancillas(n: int) -> int:
    return 1


# --------------------------------------------------------------------------
# VBE ripple-carry (Vedral, Barenco, Ekert 1996)
# --------------------------------------------------------------------------


def _carry(b: CircuitBuilder, c_in, a, t, c_out) -> None:
    """``c_out ^= MAJ(a, t, c_in)``, leaving ``t`` as ``a ^ t``."""
    b.ccx(a, t, c_out)
    b.cx(a, t)
    b.ccx(c_in, t, c_out)


def _carry_inv(b: CircuitBuilder, c_in, a, t, c_out) -> None:
    b.ccx(c_in, t, c_out)
    b.cx(a, t)
    b.ccx(a, t, c_out)


def _vbe_add(b: CircuitBuilder, a: Word, target: Word, anc: Word) -> None:
    """``target += a  (mod 2^n)`` -- 4(n-1) Toffoli, 4n-2 CNOT, n ancillas.

    Carries live in a dedicated ancilla register instead of rippling through the
    addend.  Costs twice CDKM's Toffolis and ``n`` times its ancillas; included
    precisely so the benchmark can show that.
    """
    n = len(a)
    c = list(anc.bits[:n])
    for i in range(n - 1):
        _carry(b, c[i], a[i], target[i], c[i + 1])
    # Top bit: sum only, no outgoing carry needed for mod 2^n.
    b.cx(a[n - 1], target[n - 1])
    b.cx(c[n - 1], target[n - 1])
    for i in reversed(range(n - 1)):
        _carry_inv(b, c[i], a[i], target[i], c[i + 1])
        b.cx(a[i], target[i])
        b.cx(c[i], target[i])


def _vbe_ancillas(n: int) -> int:
    return n


# --------------------------------------------------------------------------
# Draper QFT adder
# --------------------------------------------------------------------------


def _qft(b: CircuitBuilder, reg: Word, inverse: bool = False) -> None:
    """Semi-classical-free QFT on an LSB-first register, without output swaps."""
    n = len(reg)
    sign = -1.0 if inverse else 1.0
    order = range(n - 1, -1, -1) if not inverse else range(n)
    for j in order:
        if inverse:
            for m in range(j - 1, -1, -1):
                b.circuit.cp(sign * math.pi / 2 ** (j - m), reg[m], reg[j])
            b.circuit.h(reg[j])
        else:
            b.circuit.h(reg[j])
            for m in range(j):
                b.circuit.cp(sign * math.pi / 2 ** (j - m), reg[m], reg[j])


def _qft_add(b: CircuitBuilder, a: Word, target: Word, anc: Word) -> None:
    """``target += a  (mod 2^n)`` in the Fourier basis -- 0 ancillas, 0 Toffoli.

    Not simulable by the basis-state simulator (it leaves the computational
    basis mid-circuit) and not natively Clifford+T; see the module docstring.
    """
    n = len(a)
    _qft(b, target)
    for j in range(n):
        for m in range(j + 1):
            b.circuit.cp(math.pi / 2 ** (j - m), a[m], target[j])
    _qft(b, target, inverse=True)


def _qft_ancillas(n: int) -> int:
    return 0


# --------------------------------------------------------------------------
# Gidney temporary-AND ripple-carry
# --------------------------------------------------------------------------


def _gidney_add(b: CircuitBuilder, a: Word, target: Word, anc: Word) -> None:
    """``target += a  (mod 2^n)`` using temporary ANDs -- 4(n-1) T, n-1 ancillas.

    The carries live in ancillas computed by :func:`~...temporary_and.and_g` and
    destroyed by ``and_g_dg``. Writing ``c_i`` for the carry into position ``i``
    (with ``c_0 = 0``), the forward sweep establishes::

        a_i  <- a_i XOR c_i
        b_i  <- b_i XOR c_i
        t_i  <- c_{i+1} = c_i XOR ((a_i XOR c_i) AND (b_i XOR c_i))

    and the backward sweep uncomputes each ``t_i`` -- for free -- while turning
    the mutated ``a_i``/``b_i`` back into the addend and the sum::

        b_i  <- a_i' XOR b_i' XOR c_i = a_i XOR b_i XOR c_i   (the sum bit)
        a_i  <- a_i' XOR c_i = a_i                            (restored)

    The carry out of the top bit is never computed, which *is* the reduction
    modulo ``2^n``.
    """
    n = len(a)
    if n == 1:
        b.cx(a[0], target[0])
        return

    t = list(anc.bits[: n - 1])

    # Forward: build the carry chain.
    for i in range(n - 1):
        if i:
            b.cx(t[i - 1], a[i])
            b.cx(t[i - 1], target[i])
        b.and_g(a[i], target[i], t[i])
        if i:
            b.cx(t[i - 1], t[i])

    # Top sum bit, while the final carry still exists.
    b.cx(a[n - 1], target[n - 1])
    b.cx(t[n - 2], target[n - 1])

    # Backward: free the carries and extract the sums.
    for i in reversed(range(n - 1)):
        if i:
            b.cx(t[i - 1], t[i])
        b.and_g_dg(a[i], target[i], t[i])
        b.cx(a[i], target[i])
        if i:
            b.cx(t[i - 1], target[i])
            b.cx(t[i - 1], a[i])


def _gidney_ancillas(n: int) -> int:
    return max(0, n - 1)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Adder:
    """A reversible in-place modular adder."""

    name: str
    apply: Callable[[CircuitBuilder, Word, Word, Word], None]
    ancillas: Callable[[int], int]
    reference: str
    #: False for adders that leave the computational basis (the QFT adder), which
    #: therefore cannot be checked with the fast basis-state simulator.
    basis_simulable: bool
    #: False for adders whose native gate set is not Clifford+T without an
    #: explicit rotation-synthesis assumption.
    native_clifford_t: bool
    #: True when the adder needs mid-circuit measurement and classical
    #: feedforward, so no purely unitary transpilation can express it.
    needs_measurement: bool = False

    def ancilla_count(self, width: int) -> int:
        return self.ancillas(width)


ADDERS: dict[str, Adder] = {
    "cdkm": Adder(
        name="cdkm",
        apply=_cdkm_add,
        ancillas=_cdkm_ancillas,
        reference="Cuccaro, Draper, Kutin & Moulton, arXiv:quant-ph/0410184",
        basis_simulable=True,
        native_clifford_t=True,
    ),
    "vbe": Adder(
        name="vbe",
        apply=_vbe_add,
        ancillas=_vbe_ancillas,
        reference="Vedral, Barenco & Ekert, Phys. Rev. A 54, 147 (1996)",
        basis_simulable=True,
        native_clifford_t=True,
    ),
    "qft": Adder(
        name="qft",
        apply=_qft_add,
        ancillas=_qft_ancillas,
        reference="Draper, arXiv:quant-ph/0008033",
        basis_simulable=False,
        native_clifford_t=False,
    ),
    "gidney": Adder(
        name="gidney",
        apply=_gidney_add,
        ancillas=_gidney_ancillas,
        reference="Gidney, Quantum 2, 74 (2018), arXiv:1709.06648",
        basis_simulable=True,
        native_clifford_t=True,
        needs_measurement=True,
    ),
}


def get_adder(name: str) -> Adder:
    try:
        return ADDERS[name]
    except KeyError:
        raise KeyError(f"unknown adder {name!r}; available: {sorted(ADDERS)}") from None


def add_into(b: CircuitBuilder, a: Word, target: Word, adder: Adder | str = "cdkm") -> None:
    """``target += a  (mod 2^w)``, borrowing and returning the adder's ancillas."""
    adder = get_adder(adder) if isinstance(adder, str) else adder
    if len(a) != len(target):
        raise ValueError(f"width mismatch: {len(a)} vs {len(target)}")
    need = adder.ancilla_count(len(a))
    with b.ancillas.borrow(need, f"{adder.name}_carry") as anc:
        adder.apply(b, a, target, anc)


# --------------------------------------------------------------------------
# Constant addition
# --------------------------------------------------------------------------


def _const_load(b: CircuitBuilder, value: int, target: Word, adder: Adder) -> None:
    """Materialise the constant in a borrowed register, add, then unload.

    Simple and adder-agnostic, at the price of ``w`` extra ancillas held for the
    duration of the addition.
    """
    w = len(target)
    with b.ancillas.borrow(w, "const") as k:
        xor_const(b, value, k)
        with b.ancillas.borrow(adder.ancilla_count(w), f"{adder.name}_carry") as anc:
            adder.apply(b, k, target, anc)
        xor_const(b, value, k)


def _const_vbe(b: CircuitBuilder, value: int, target: Word, adder: Adder) -> None:
    """VBE ripple-carry with the addend folded in at compile time.

    Because the addend bits are *classical*, every gate controlled on an addend
    bit either vanishes (bit 0) or loses that control (bit 1).  The two Toffolis
    per carry stage collapse to one, so this costs ``2(n-1)`` Toffolis with ``n``
    ancillas and no register to hold the constant.
    """
    n = len(target)
    with b.ancillas.borrow(n, "kcarry") as anc:
        c = list(anc.bits[:n])
        bits = [(value >> i) & 1 for i in range(n)]

        for i in range(n - 1):
            if bits[i]:
                b.cx(target[i], c[i + 1])
                b.x(target[i])
            b.ccx(c[i], target[i], c[i + 1])
        if bits[n - 1]:
            b.x(target[n - 1])
        b.cx(c[n - 1], target[n - 1])
        for i in reversed(range(n - 1)):
            # inverse CARRY, specialised on the classical addend bit
            b.ccx(c[i], target[i], c[i + 1])
            if bits[i]:
                b.x(target[i])
                b.cx(target[i], c[i + 1])
                b.x(target[i])  # the SUM stage's `cx(a[i], target[i])`
            b.cx(c[i], target[i])


CONST_ADD_STRATEGIES: dict[str, Callable[[CircuitBuilder, int, Word, Adder], None]] = {
    "load": _const_load,
    "vbe_const": _const_vbe,
}


def add_const_into(
    b: CircuitBuilder,
    value: int,
    target: Word,
    adder: Adder | str = "cdkm",
    strategy: str = "load",
) -> None:
    """``target += value  (mod 2^w)`` for a compile-time classical constant."""
    adder = get_adder(adder) if isinstance(adder, str) else adder
    try:
        fn = CONST_ADD_STRATEGIES[strategy]
    except KeyError:
        raise KeyError(
            f"unknown constant-add strategy {strategy!r}; available: {sorted(CONST_ADD_STRATEGIES)}"
        ) from None
    fn(b, value & ((1 << len(target)) - 1), target, adder)
