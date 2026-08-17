"""Reversible Keccak-f[1600], the permutation behind SHA-3.

SHA-3 is worth implementing here for contrast: it is built completely
differently from SHA-2, and the difference shows up directly in the resource
profile.

SHA-2 is an ARX design -- its cost is dominated by modular **addition**, whose
carry chains are long and serial.  Keccak has no arithmetic at all.  Four of its
five round steps are linear:

``theta``  XOR of column parities, plus one rotation
``rho``    rotate each lane by a fixed offset
``pi``     permute the lanes
``iota``   XOR a round constant into one lane

In this project's cost model, ``rho`` and ``pi`` are **completely free** -- they
are pure wire permutations, exactly like SHA-256's rotations -- and ``theta``
and ``iota`` cost only CNOTs and X gates.  Every non-linear gate in the whole of
SHA-3 comes from one step:

``chi``    ``A[x] ^= (NOT A[x+1]) AND A[x+2]``, applied across rows of five

That is one AND per state bit, 1600 per round.  Compared with SHA-256, Keccak
trades a small number of expensive carry chains for a large number of cheap
independent ANDs -- which is why its Toffoli *depth* is far lower even though
its Toffoli *count* is higher.

The uncomputation problem
-------------------------

``chi`` is invertible but not an involution, and its output cannot be written
in place: every output lane depends on lanes that the same step is modifying.
So the circuit ping-pongs between two 1600-qubit registers -- compute
``B = chi(A)``, then clear ``A``.  Clearing ``A`` is the expensive part, because
it needs ``chi^-1``, whose algebraic form is deeper than ``chi``'s::

    chi^-1: A[x] = B[x] XOR (NOT B[x+1] AND (B[x+2] XOR (NOT B[x+3] AND B[x+4])))

Two ANDs per bit instead of one.  This is the structural reason a reversible
Keccak costs more than its classical gate count suggests, and it is the same
lesson the carry-save experiment taught in SHA-256: uncomputation is where
reversible circuits actually spend their money.
"""

from .keccak import (
    KECCAK_ROTATION_OFFSETS,
    KECCAK_ROUND_CONSTANTS,
    KeccakCircuit,
    build_keccak_f,
    build_sha3,
    keccak_f,
    sha3_256,
)

__all__ = [
    "KECCAK_ROTATION_OFFSETS",
    "KECCAK_ROUND_CONSTANTS",
    "KeccakCircuit",
    "build_keccak_f",
    "build_sha3",
    "keccak_f",
    "sha3_256",
]
