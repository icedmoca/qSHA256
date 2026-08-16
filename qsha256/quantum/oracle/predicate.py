"""Digest comparison and phase marking.

A Grover oracle must apply ``|x> -> -|x>`` exactly when ``x`` satisfies the
predicate, and leave every work register untouched.  For preimage search the
predicate is "the computed digest equals the target digest".

The construction is deliberately built from a marker qubit and an explicit AND
tree rather than from a multi-controlled-Z primitive, for two reasons:

* the ancilla cost becomes visible and countable rather than hidden inside a
  library gate whose decomposition the resource model would have to guess;
* the whole thing stays inside the permutation-plus-diagonal gate set, so the
  fast basis-state simulator can verify the phase behaviour exactly, at full
  32-bit scale, without a statevector.

Comparing an ``n``-bit digest costs ``n-2`` tree ancillas plus one marker, and
``2(2(n-2)+1)`` Toffolis once compute and uncompute are counted -- for SHA-256's
256-bit digest, 1018 Toffolis and 255 ancillas.  That is small next to the hash
itself, which is the point worth knowing: **the comparison is not what makes a
preimage oracle expensive.**
"""

from __future__ import annotations

from ..primitives.boolean import and_tree_ancilla_count, and_tree_mcx
from ..registers import CircuitBuilder, Word

__all__ = [
    "equality_phase_flip",
    "equality_ancilla_count",
    "digest_bits",
]


def digest_bits(registers: list[Word]) -> list:
    """Flatten a list of word registers into a single LSB-first qubit list."""
    out = []
    for word in registers:
        out.extend(word.qubits)
    return out


def equality_ancilla_count(n_bits: int) -> int:
    """Ancillas needed by :func:`equality_phase_flip`: tree ancillas plus a marker."""
    return and_tree_ancilla_count(n_bits) + 1


def equality_phase_flip(
    b: CircuitBuilder,
    registers: list[Word],
    target: int,
    label: str = "cmp",
) -> None:
    """Flip the phase of basis states whose ``registers`` hold ``target``.

    ``target`` is interpreted as a single big integer over the concatenated
    registers, least-significant register first, matching
    :func:`digest_bits`.

    The sequence is::

        X on every bit where target is 0    -> the state is all-ones iff it matches
        AND-tree over all bits -> marker    -> marker is 1 iff it matches
        Z on marker                         -> phase flip iff it matches
        AND-tree again                      -> marker back to |0> (self-inverse)
        X again                             -> registers restored

    Every ancilla is returned to ``|0>`` and every input register is restored,
    so the net action is exactly a diagonal phase operator.
    """
    bits = digest_bits(registers)
    n = len(bits)
    if not 0 <= target < (1 << n):
        raise ValueError(f"target does not fit in {n} bits")

    with b.section(f"{label}: phase flip on digest == target"):
        zero_positions = [i for i in range(n) if not (target >> i) & 1]

        need = and_tree_ancilla_count(n)
        with b.ancillas.borrow(need + 1, f"{label}_anc") as anc:
            marker = anc[0]
            tree = list(anc.qubits[1:])

            for i in zero_positions:
                b.x(bits[i])

            and_tree_mcx(b, bits, marker, tree)
            b.z(marker)
            and_tree_mcx(b, bits, marker, tree)

            for i in zero_positions:
                b.x(bits[i])
