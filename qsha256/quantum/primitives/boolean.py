"""Reversible Boolean primitives: AND, Ch, Maj, and multi-controlled AND trees.

AND is where the cost lives.  Unlike XOR it is not reversible on its own -- the
map ``(x, y) -> x AND y`` throws away information -- so it must be embedded as
``(x, y, t) -> (x, y, t XOR (x AND y))``, which is precisely the Toffoli gate.
Toffoli is the only non-Clifford resource in the entire SHA-256 construction, so
every Toffoli saved here is a direct T-count saving downstream.

Both Ch and Maj are implemented in **ancilla-free, one-Toffoli-per-bit** form by
rewriting them so that a single AND does all the non-linear work:

    Ch(x, y, z)  = z XOR (x AND (y XOR z))
    Maj(x, y, z) = x XOR ((x XOR y) AND (x XOR z))

Both identities are verified exhaustively over all 8 input assignments in the
test suite, and the circuits are verified bit-by-bit against the classical
model.  A naive transcription of the textbook formulas would need 2 and 3
Toffolis per bit respectively; these forms need 1.
"""

from __future__ import annotations

from collections.abc import Sequence

from qiskit.circuit import Qubit

from ..registers import CircuitBuilder, Word

__all__ = [
    "and_into",
    "and_tree_ancilla_count",
    "and_tree_mcx",
    "ch_into",
    "ch_word_into",
    "ch_word_into_temporary",
    "maj_into",
    "maj_word_into",
    "maj_word_into_temporary",
    "uncompute_ch_temporary",
    "uncompute_maj_temporary",
]


def and_into(b: CircuitBuilder, x: Qubit, y: Qubit, target: Qubit) -> None:
    """``target ^= x AND y`` -- a single Toffoli.  Self-inverse."""
    b.ccx(x, y, target)


# --------------------------------------------------------------------------
# Ch
# --------------------------------------------------------------------------


def ch_into(b: CircuitBuilder, x: Qubit, y: Qubit, z: Qubit, target: Qubit) -> None:
    """``target ^= Ch(x, y, z)`` using 1 Toffoli, 3 CNOTs and no ancilla.

    Uses ``Ch(x,y,z) = z XOR (x AND (y XOR z))``.  The term ``y XOR z`` is formed
    *in place on y*, consumed by the Toffoli, and immediately restored -- a
    textbook compute/use/uncompute sandwich that borrows a data register instead
    of an ancilla.

    ::

        cx  z -> y        y  becomes y^z
        ccx x, y -> t     t ^= x & (y^z)
        cx  z -> y        y  restored
        cx  z -> t        t ^= z

    Not self-inverse as a sequence, but it is an involution as a whole map
    (target is XORed with a function of untouched inputs), so replaying it
    uncomputes it.
    """
    b.cx(z, y)
    b.ccx(x, y, target)
    b.cx(z, y)
    b.cx(z, target)


def ch_word_into(
    b: CircuitBuilder, x: Word, y: Word, z: Word, target: Word, temporary_and: bool = False
) -> None:
    """Bitwise ``target ^= Ch(x, y, z)`` over a whole word.

    ``temporary_and`` routes the AND through a clean borrowed ancilla so that
    Gidney's construction applies: 4 T to compute and none to uncompute, against
    7 and 7 for a plain Toffoli pair. It costs one extra ancilla per bit, which
    is why it is not the default -- see :func:`ch_word_into_temporary`.
    """
    _check_widths(x, y, z, target)
    if temporary_and:
        ch_word_into_temporary(b, x, y, z, target)
        return
    for xi, yi, zi, ti in zip(x, y, z, target):
        ch_into(b, xi, yi, zi, ti)


def ch_word_into_temporary(b: CircuitBuilder, x: Word, y: Word, z: Word, target: Word) -> None:
    """``target ^= Ch(x, y, z)`` with the AND in a clean ancilla.

    The plain :func:`ch_into` writes its AND straight into ``target``, which is
    an accumulator and therefore not clean -- so Gidney's temporary AND does not
    apply and the compute/uncompute pair costs 14 T per bit. Routing the AND
    through a dedicated ``|0>`` ancilla makes it applicable, at 4 T per bit for
    the pair, in exchange for one ancilla per bit held across the caller's use
    of ``target``.

    The ancilla is *not* released here: it must stay alive until the caller
    uncomputes, which :func:`uncompute_ch_temporary` does.
    """
    _check_widths(x, y, z, target)
    width = len(x)
    anc = b.ancillas.acquire(width, "ch_and")
    for i in range(width):
        b.cx(z[i], y[i])  # y becomes y XOR z
        b.and_g(x[i], y[i], anc[i])  # anc = x AND (y XOR z)
        b.cx(z[i], y[i])  # restore y
        b.cx(anc[i], target[i])  # target ^= the AND
        b.cx(z[i], target[i])  # target ^= z, completing Ch
    return anc


def uncompute_ch_temporary(
    b: CircuitBuilder, x: Word, y: Word, z: Word, target: Word, anc: Word
) -> None:
    """Undo :func:`ch_word_into_temporary`, clearing both target and ancilla."""
    for i in range(len(x)):
        b.cx(z[i], target[i])
        b.cx(anc[i], target[i])
        b.cx(z[i], y[i])
        b.and_g_dg(x[i], y[i], anc[i])
        b.cx(z[i], y[i])
    b.ancillas.release(anc)


# --------------------------------------------------------------------------
# Maj
# --------------------------------------------------------------------------


def maj_into(b: CircuitBuilder, x: Qubit, y: Qubit, z: Qubit, target: Qubit) -> None:
    """``target ^= Maj(x, y, z)`` using 1 Toffoli, 5 CNOTs and no ancilla.

    Uses ``Maj(x,y,z) = x XOR ((x XOR y) AND (x XOR z))``.  Both XOR terms are
    formed in place on ``y`` and ``z``, consumed, and restored.

    ::

        cx  x -> y        y becomes x^y
        cx  x -> z        z becomes x^z
        ccx y, z -> t     t ^= (x^y) & (x^z)
        cx  x -> z        z restored
        cx  x -> y        y restored
        cx  x -> t        t ^= x
    """
    b.cx(x, y)
    b.cx(x, z)
    b.ccx(y, z, target)
    b.cx(x, z)
    b.cx(x, y)
    b.cx(x, target)


def maj_word_into(
    b: CircuitBuilder, x: Word, y: Word, z: Word, target: Word, temporary_and: bool = False
) -> None:
    """Bitwise ``target ^= Maj(x, y, z)`` over a whole word."""
    _check_widths(x, y, z, target)
    if temporary_and:
        maj_word_into_temporary(b, x, y, z, target)
        return
    for xi, yi, zi, ti in zip(x, y, z, target):
        maj_into(b, xi, yi, zi, ti)


def maj_word_into_temporary(b: CircuitBuilder, x: Word, y: Word, z: Word, target: Word) -> Word:
    """``target ^= Maj(x, y, z)`` with the AND in a clean ancilla.  See
    :func:`ch_word_into_temporary` for why this is worth an ancilla per bit."""
    _check_widths(x, y, z, target)
    width = len(x)
    anc = b.ancillas.acquire(width, "maj_and")
    for i in range(width):
        b.cx(x[i], y[i])  # y becomes x XOR y
        b.cx(x[i], z[i])  # z becomes x XOR z
        b.and_g(y[i], z[i], anc[i])  # anc = (x XOR y) AND (x XOR z)
        b.cx(x[i], z[i])
        b.cx(x[i], y[i])
        b.cx(anc[i], target[i])
        b.cx(x[i], target[i])  # target ^= x, completing Maj
    return anc


def uncompute_maj_temporary(
    b: CircuitBuilder, x: Word, y: Word, z: Word, target: Word, anc: Word
) -> None:
    """Undo :func:`maj_word_into_temporary`."""
    for i in range(len(x)):
        b.cx(x[i], target[i])
        b.cx(anc[i], target[i])
        b.cx(x[i], y[i])
        b.cx(x[i], z[i])
        b.and_g_dg(y[i], z[i], anc[i])
        b.cx(x[i], z[i])
        b.cx(x[i], y[i])
    b.ancillas.release(anc)


def _check_widths(*words: Word) -> None:
    widths = {len(w) for w in words}
    if len(widths) != 1:
        raise ValueError(f"word width mismatch: {[len(w) for w in words]}")
    for w in words:
        if any(bit is None for bit in w):
            raise ValueError("Boolean primitives require fully materialised words")


# --------------------------------------------------------------------------
# Multi-controlled AND
# --------------------------------------------------------------------------


def and_tree_ancilla_count(n_controls: int) -> int:
    """Clean ancillas needed by :func:`and_tree_mcx` for ``n`` controls."""
    return max(0, n_controls - 2)


def and_tree_mcx(
    b: CircuitBuilder,
    controls: Sequence[Qubit],
    target: Qubit,
    ancillas: Sequence[Qubit],
) -> None:
    """``target ^= AND(controls)`` via a balanced compute/uncompute AND tree.

    Uses ``n - 2`` clean ancillas and ``2(n - 2) + 1`` Toffolis for ``n >= 3``.
    The tree is balanced rather than a linear chain, giving Toffoli-depth
    ``O(log n)`` instead of ``O(n)`` -- which matters for the 256-bit digest
    comparison in the preimage oracle, where a linear chain would dominate the
    oracle's depth.

    All ancillas are returned to ``|0>``: the tree is computed, the root
    Toffoli writes to the target, then the tree is uncomputed in reverse.
    """
    controls = list(controls)
    n = len(controls)
    if n == 0:
        b.x(target)
        return
    if n == 1:
        b.cx(controls[0], target)
        return
    if n == 2:
        b.ccx(controls[0], controls[1], target)
        return

    need = and_tree_ancilla_count(n)
    if len(ancillas) < need:
        raise ValueError(f"and_tree_mcx with {n} controls needs {need} ancillas")
    pool = list(ancillas[:need])

    # Compute the tree bottom-up, pairing nodes; an odd node is carried forward.
    steps: list[tuple[Qubit, Qubit, Qubit]] = []
    level = controls
    while len(level) > 2:
        nxt: list[Qubit] = []
        for i in range(0, len(level) - 1, 2):
            anc = pool.pop()
            steps.append((level[i], level[i + 1], anc))
            b.ccx(level[i], level[i + 1], anc)
            nxt.append(anc)
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt

    b.ccx(level[0], level[1], target)

    for c0, c1, anc in reversed(steps):
        b.ccx(c0, c1, anc)
