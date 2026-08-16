"""The SHA-256 preimage oracle -- the real cryptanalytic object.

The statement "Grover finds a SHA-256 preimage in ~2^128 queries" is about
*queries*.  It says nothing about what a query costs, and a query is not one
SHA-256 evaluation.  It is::

    |candidate>|0>
        -> reversible SHA-256              (forward)
    |candidate>|digest>
        -> compare digest with target, flip phase on a match
    |candidate>|digest>   (phase-marked)
        -> reversible SHA-256 inverse      (uncompute)
    |candidate>|0>

The uncomputation is not optional housekeeping.  Without it the digest register
stays entangled with the candidate, which destroys the interference Grover
depends on -- the algorithm simply stops working.  So the honest unit of cost is
**two SHA-256 evaluations plus a comparison**, not one.

The forward evaluation must also clean up its message schedule and working
registers, but that comes for free here: the oracle's trailing inverse undoes
the whole forward span at once, so the compression is built *without* its own
internal uncomputation.  Doing both would run the rounds four times where two
suffice, and this module explicitly avoids that.

So an oracle call costs almost exactly **twice** a forward-only SHA-256 circuit,
plus a digest comparison.  That factor is measured by the benchmark, not
assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...spec import SHA256, ShaSpec
from ..strategies import ORACLE, Strategy
from ..registers import CircuitBuilder, Word
from ..sha256.compression import build_compression
from .predicate import equality_phase_flip

__all__ = ["PreimageOracle", "build_preimage_oracle"]


@dataclass
class PreimageOracle:
    """A built preimage oracle plus the registers a caller needs."""

    builder: CircuitBuilder
    spec: ShaSpec
    strategy: Strategy
    rounds: int
    target_digest: int
    #: Candidate message registers -- the Grover search space.
    message: list[Word]
    #: Message word indices held at fixed classical values (e.g. padding).
    fixed_words: dict[int, int] = field(default_factory=dict)
    #: Instruction span of the forward hash, for cost attribution.
    forward_span: tuple[int, int] = (0, 0)

    @property
    def circuit(self):
        return self.builder.circuit

    @property
    def search_qubits(self) -> int:
        """Free candidate qubits -- the size of the space Grover searches."""
        return sum(len(w) for i, w in enumerate(self.message) if i not in self.fixed_words)


def build_preimage_oracle(
    spec: ShaSpec = SHA256,
    strategy: Strategy = ORACLE,
    rounds: int | None = None,
    target_digest: int = 0,
    initial_state: tuple[int, ...] | None = None,
    fixed_words: dict[int, int] | None = None,
    builder: CircuitBuilder | None = None,
    message: list[Word] | None = None,
) -> PreimageOracle:
    """Build ``SHA-256 -> compare -> phase flip -> SHA-256^-1``.

    Parameters
    ----------
    target_digest:
        The digest to search for, as one integer over the concatenated state
        words (word 0 least significant).
    initial_state:
        Classical IV, loaded with X gates.  Almost always what you want: the
        SHA-256 IV is a public constant, so making it a quantum input would be
        pure waste.
    fixed_words:
        Message words pinned to classical values -- padding, or a partially
        known preimage.  Pinned words are loaded with X gates and are excluded
        from the search space.

    The chosen adder must be built from self-inverse gates, since the oracle's
    inverse half is produced by replaying the forward span backwards.  The QFT
    adder is not, and is rejected rather than silently producing a circuit that
    does not uncompute.
    """
    from ..primitives.add import ADDERS

    if not ADDERS[strategy.adder].basis_simulable:
        raise ValueError(
            f"adder {strategy.adder!r} is not built from self-inverse gates, so the "
            "oracle's inverse cannot be produced by reverse replay"
        )
    rounds = spec.rounds if rounds is None else rounds
    b = builder or CircuitBuilder(f"{spec.name}_preimage_oracle_r{rounds}")

    start = len(b.circuit.data)
    with b.section("forward SHA-256"):
        comp = build_compression(
            spec,
            strategy,
            rounds=rounds,
            initial_state=initial_state,
            message_constants=fixed_words,
            builder=b,
            message=message,
            # The oracle supplies its own inverse, so the compression must not
            # also uncompute internally -- that would double the round count.
            output="digest",
            uncompute=False,
        )
    end = len(b.circuit.data)

    equality_phase_flip(b, comp.digest, target_digest, label="target")

    with b.section("inverse SHA-256 (uncompute digest)"):
        b.append_reversed(start, end)

    return PreimageOracle(
        builder=b,
        spec=spec,
        strategy=strategy,
        rounds=rounds,
        target_digest=target_digest,
        message=comp.message,
        fixed_words=dict(fixed_words or {}),
        forward_span=(start, end),
    )
