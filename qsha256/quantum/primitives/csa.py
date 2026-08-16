"""Carry-save addition: summing many addends with one carry propagation.

A ripple-carry adder is expensive because the carry must travel the whole word:
its Toffoli depth is ``O(n)``.  SHA-256 adds *five* things together to form
``T1``, and doing that with four ripple adders means four full carry
propagations in sequence -- which is exactly why the naive round has a long
critical path that no amount of extra ancilla fixes.

A **carry-save adder** (3-to-2 compressor) sidesteps this.  Given three addends
it produces two, whose sum equals the sum of the original three::

    S = x XOR y XOR z                (bitwise, no carry propagation)
    C = MAJ(x, y, z) << 1            (bitwise, no carry propagation)
    S + C == x + y + z   (mod 2^n)

Every bit is independent, so a CSA layer has **constant depth** regardless of
word size, and costs one Toffoli per bit rather than two.  A tree of CSA layers
reduces ``k`` addends to two, and a single ripple-carry adder finishes the job.

Summing ``k`` addends therefore costs ``k-2`` constant-depth CSA layers plus one
ripple, instead of ``k-1`` ripples.  For SHA-256's five-addend ``T1`` that is
three CSA layers (31 Toffoli each) plus one CDKM adder (64 Toffoli) = 157
Toffoli, against four CDKM adders = 256 Toffoli -- **cheaper in Toffoli count
and shorter in depth at the same time**, paid for in ancilla.

The ``<< 1`` on the carry word is, as always in this project, free wiring: bit
``i`` of ``MAJ`` is written directly into bit ``i+1`` of the carry register, and
the carry out of the top bit is simply never written, which *is* the reduction
modulo ``2^n``.

This construction is standard in classical arithmetic (Wallace/Dadda trees) and
its reversible use is well established in quantum arithmetic circuits; it is
applied here to SHA-256's multi-operand round additions.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from ..registers import CircuitBuilder, Word
from .add import add_into
from .boolean import maj_into
from .xor import xor_word

__all__ = ["csa_layer", "csa_toffoli_cost", "sum_addends"]


def csa_layer(b: CircuitBuilder, x: Word, y: Word, z: Word, s: Word, c: Word) -> None:
    """3-to-2 compression: ``s ^= x^y^z`` and ``c ^= MAJ(x,y,z) << 1``.

    ``s`` and ``c`` must start in ``|0>``.  ``x``, ``y`` and ``z`` are restored
    to their input values (``maj_into`` borrows them and puts them back).
    """
    n = len(x)
    if not (len(y) == len(z) == len(s) == len(c) == n):
        raise ValueError("carry-save operands must all have the same width")
    xor_word(b, x, s)
    xor_word(b, y, s)
    xor_word(b, z, s)
    # Carry into bit i+1; the carry out of the top bit is dropped == mod 2^n.
    for i in range(n - 1):
        maj_into(b, x[i], y[i], z[i], c[i + 1])


def csa_toffoli_cost(n_addends: int, width: int) -> int:
    """Analytic Toffoli cost of :func:`sum_addends` (forward pass only)."""
    if n_addends < 2:
        return 0
    layers = n_addends - 2
    return layers * (width - 1) + 2 * width  # CSA layers + one CDKM ripple


@contextmanager
def sum_addends(
    b: CircuitBuilder,
    addends: Sequence[Word],
    adder: str = "cdkm",
    label: str = "csum",
) -> Iterator[Word]:
    """Yield a borrowed register holding ``sum(addends) mod 2^w``; uncompute on exit.

    Reduces the addend list with CSA layers until two remain, then finishes with
    one ripple-carry addition into a fresh register.  On exit the entire forward
    computation is replayed in reverse, returning every borrowed register to
    ``|0>`` -- so the caller sees a clean, garbage-free multi-operand adder.

    The reversal is exact rather than approximate: the builder only emits
    self-inverse permutation gates, so replaying the recorded instruction span
    backwards inverts it.  Nothing may be emitted on these qubits between the
    span and its reversal except the caller's use of the yielded register, which
    the ``with`` block enforces.
    """
    if len(addends) < 1:
        raise ValueError("need at least one addend")
    width = len(addends[0])
    start = len(b.circuit.data)
    acquired: list[Word] = []

    pending = list(addends)
    while len(pending) > 2:
        x, y, z = pending.pop(0), pending.pop(0), pending.pop(0)
        s = b.ancillas.acquire(width, f"{label}_s")
        c = b.ancillas.acquire(width, f"{label}_c")
        acquired += [s, c]
        csa_layer(b, x, y, z, s, c)
        pending += [s, c]

    total = b.ancillas.acquire(width, f"{label}_t")
    acquired.append(total)
    xor_word(b, pending[0], total)
    if len(pending) == 2:
        add_into(b, pending[1], total, adder)

    end = len(b.circuit.data)
    try:
        yield total
    finally:
        b.append_reversed(start, end)
        for word in reversed(acquired):
            b.ancillas.release(word)
