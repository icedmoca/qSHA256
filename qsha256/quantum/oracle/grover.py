"""Grover search: the diffusion operator, a runnable toy, and cost extrapolation.

Two very different things live here, and the distinction is the whole point.

**A toy that runs.**  :func:`build_toy_grover` assembles superposition, a real
reversible hash oracle, the diffusion operator and measurement into a circuit of
about twenty qubits, which a statevector simulator executes for real.  You can
watch the amplitude of the marked candidate grow with each iteration.

**An extrapolation that does not run, and says so.**
:func:`grover_cost_estimate` takes the *measured* cost of a full SHA-256 oracle
and multiplies it by the analytic Grover query count.  Nothing is simulated,
nothing is executed; it is arithmetic on measured inputs, labelled as such.

On the ``2^128`` claim
----------------------

For a ``d``-bit digest and a search space of ``2^n`` candidates with a unique
match, Grover needs about ``(pi/4) * 2^(n/2)`` oracle calls.  For SHA-256
preimage search that is quoted as ``2^128``, which invites the conclusion that
SHA-256 offers "128-bit quantum security".  Three things make that misleading,
and this module reports all three:

1. **An oracle call is not a hash evaluation.**  It is a garbage-free forward
   hash, a digest comparison, and a full inverse hash -- measured here at
   roughly twice a forward-only circuit's Toffoli count.
2. **Grover barely parallelises.**  Splitting the search across ``m`` machines
   gives only a ``sqrt(m)`` speedup, so wall-clock time cannot be bought down
   the way classical brute force can.
3. **Depth limits bite.**  A ``2^128``-iteration circuit is inherently serial;
   its depth is astronomically beyond any plausible coherence or runtime budget,
   which is why NIST's post-quantum criteria evaluate attacks under an explicit
   ``MAXDEPTH`` cap rather than by query count alone.

References
----------
- L. K. Grover, "A fast quantum mechanical algorithm for database search",
  STOC '96, arXiv:quant-ph/9605043.
- C. Zalka, "Grover's quantum searching algorithm is optimal",
  Phys. Rev. A 60, 2746 (1999), arXiv:quant-ph/9711070.
- M. Amy, O. Di Matteo, V. Gheorghiu, M. Mosca, A. Parent, J. Schanck,
  "Estimating the cost of generic quantum pre-image attacks on SHA-2 and SHA-3",
  SAC 2016, arXiv:1603.09383.
- NIST, "Submission Requirements and Evaluation Criteria for the Post-Quantum
  Cryptography Standardization Process" (2016), section on MAXDEPTH.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from ..primitives.boolean import and_tree_ancilla_count, and_tree_mcx
from ..registers import CircuitBuilder, Word
from .predicate import digest_bits, equality_phase_flip
from .toy import TOY_TINY, ToySpec, build_toy_hash, toy_compress

__all__ = [
    "GroverCostEstimate",
    "build_toy_grover",
    "diffusion",
    "grover_cost_estimate",
    "grover_iterations",
]


def grover_iterations(search_bits: int, solutions: int = 1) -> int:
    """``floor((pi/4) * sqrt(N / M))`` -- the optimal number of Grover iterations."""
    n = 2**search_bits
    if not 0 < solutions <= n:
        raise ValueError("solutions must lie in 1..2^search_bits")
    return max(1, math.floor((math.pi / 4) * math.sqrt(n / solutions)))


def diffusion(b: CircuitBuilder, registers: Sequence[Word], label: str = "diffusion") -> None:
    """The Grover diffusion operator (inversion about the mean) on ``registers``.

    ``H^n X^n (multi-controlled Z) X^n H^n``, with the multi-controlled Z built
    from the same marker-plus-AND-tree construction as the predicate, so its
    ancilla cost is explicit and countable.
    """
    bits = digest_bits(list(registers))
    n = len(bits)
    with b.section(label):
        for q in bits:
            b.h(q)
            b.x(q)
        need = and_tree_ancilla_count(n)
        with b.ancillas.borrow(need + 1, "diff_anc") as anc:
            marker = anc[0]
            tree = list(anc.qubits[1:])
            and_tree_mcx(b, bits, marker, tree)
            b.z(marker)
            and_tree_mcx(b, bits, marker, tree)
        for q in bits:
            b.x(q)
            b.h(q)


def build_toy_grover(
    target_digest: int | None = None,
    spec: ToySpec = TOY_TINY,
    iterations: int | None = None,
    compare_bits: int | None = None,
    message: list[int] | None = None,
):
    """Assemble a complete, executable Grover search over the toy hash.

    Returns ``(builder, message_registers, iterations, target_digest)``.

    If ``target_digest`` is not given, it is taken from hashing ``message``
    (default ``[1, 2]``) with the classical toy reference -- so the search has a
    known planted solution and the demonstration is falsifiable.

    ``compare_bits`` truncates the digest comparison to the low bits, which
    keeps the AND tree small enough for statevector simulation; the resulting
    predicate may then have several solutions, and the iteration count is
    adjusted accordingly.
    """
    search_bits = spec.message_words * spec.word_bits
    compare_bits = compare_bits or spec.digest_bits

    if target_digest is None:
        words = toy_compress(message or [1, 2], spec)
        full = sum(v << (i * spec.word_bits) for i, v in enumerate(words))
        target_digest = full & ((1 << compare_bits) - 1)

    # Count solutions classically so the iteration count is right and the demo
    # can be checked against ground truth.
    solutions = _toy_solutions(target_digest, spec, compare_bits)
    if not solutions:
        raise ValueError("target digest has no preimage in the toy search space")
    if iterations is None:
        iterations = grover_iterations(search_bits, len(solutions))

    b = CircuitBuilder(f"{spec.name}_grover_{iterations}it")
    msg = b.add_words(spec.message_words, spec.word_bits, "M")
    # Allocated once and reused by every iteration: the oracle returns them to
    # |0>, so a fresh set per iteration would be pure waste.
    state = b.add_words(spec.state_words, spec.word_bits, "S")

    with b.section("uniform superposition"):
        for q in digest_bits(msg):
            b.h(q)

    for i in range(iterations):
        with b.section(f"grover iteration[{i}]"):
            _toy_oracle(b, msg, state, spec, target_digest, compare_bits)
            diffusion(b, msg)

    return b, msg, iterations, target_digest, solutions


def _toy_oracle(
    b: CircuitBuilder,
    msg: list[Word],
    state: list[Word],
    spec: ToySpec,
    target_digest: int,
    compare_bits: int,
) -> None:
    """Forward toy hash, phase-flip on match, inverse toy hash."""
    start = len(b.circuit.data)
    with b.section("forward toy hash"):
        toy = build_toy_hash(spec, builder=b, message=msg, state=state)
    end = len(b.circuit.data)

    # Compare only the low `compare_bits` of the digest.
    bits = digest_bits(toy.state)[:compare_bits]
    equality_phase_flip(b, [Word(bits, "digest_low")], target_digest, label="target")

    with b.section("inverse toy hash"):
        b.append_reversed(start, end)


def _toy_solutions(target: int, spec: ToySpec, compare_bits: int) -> list[tuple[int, ...]]:
    """Brute-force the toy search space classically -- ground truth for the demo."""
    mask = (1 << compare_bits) - 1
    out = []
    space = 1 << (spec.message_words * spec.word_bits)
    for candidate in range(space):
        words = [(candidate >> (i * spec.word_bits)) & spec.mask for i in range(spec.message_words)]
        digest = toy_compress(words, spec)
        full = sum(v << (i * spec.word_bits) for i, v in enumerate(digest))
        if full & mask == target:
            out.append(tuple(words))
    return out


# --------------------------------------------------------------------------
# Extrapolated cost of full-scale Grover
# --------------------------------------------------------------------------


@dataclass
class GroverCostEstimate:
    """Full-scale Grover cost.  Inputs measured; totals arithmetic on them."""

    #: MEASURED from a constructed oracle circuit.
    oracle_qubits: int
    oracle_toffoli: int
    oracle_t_count: int
    oracle_depth: int
    oracle_toffoli_depth: int

    #: Search space and analytic query count.
    search_bits: int
    solutions: int
    iterations_log2: float

    #: EXTRAPOLATED totals.
    total_t_count_log2: float
    total_depth_log2: float
    total_toffoli_log2: float

    provenance: dict[str, str] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        def p2(x: float) -> str:
            return f"~2^{x:.1f}"

        lines = [
            "Grover Preimage Cost Estimate",
            "=" * 40,
            "",
            "SHA-256 preimage oracle  [MEASURED from a constructed circuit]",
            f"  logical qubits:       {self.oracle_qubits:,}",
            f"  Toffoli gates:        {self.oracle_toffoli:,}",
            f"  T-count:              {self.oracle_t_count:,}",
            f"  circuit depth:        {self.oracle_depth:,}",
            f"  Toffoli depth:        {self.oracle_toffoli_depth:,}",
            "",
            "Grover query count  [ANALYTICAL]",
            f"  search space:         2^{self.search_bits}",
            f"  expected solutions:   {self.solutions}",
            f"  iterations:           {p2(self.iterations_log2)}  ((pi/4) * sqrt(N/M))",
            "",
            "Totals  [EXTRAPOLATED: measured oracle cost x analytic query count]",
            f"  total T gates:        {p2(self.total_t_count_log2)}",
            f"  total Toffoli gates:  {p2(self.total_toffoli_log2)}",
            f"  total circuit depth:  {p2(self.total_depth_log2)}",
            "",
            "Caveats",
            "-------",
        ]
        lines += [f"  * {c}" for c in self.caveats]
        return "\n".join(lines)


def grover_cost_estimate(
    oracle_report,
    search_bits: int = 256,
    solutions: int = 1,
) -> GroverCostEstimate:
    """Combine a measured oracle report with the analytic Grover query count.

    ``oracle_report`` is a
    :class:`~qsha256.quantum.resources.analyzer.ResourceReport` for a preimage
    oracle circuit.  Every per-oracle number is measured; the totals are that
    measurement multiplied by ``(pi/4) sqrt(2^search_bits / solutions)``, kept in
    log2 because the results run to hundreds of bits.
    """
    iterations_log2 = math.log2(math.pi / 4) + 0.5 * (search_bits - math.log2(solutions))

    def scaled(value: int) -> float:
        return math.log2(max(1, value)) + iterations_log2

    return GroverCostEstimate(
        oracle_qubits=oracle_report.width,
        oracle_toffoli=oracle_report.toffoli_count,
        oracle_t_count=oracle_report.t_count,
        oracle_depth=oracle_report.depth["depth"],
        oracle_toffoli_depth=oracle_report.depth["toffoli_depth"],
        search_bits=search_bits,
        solutions=solutions,
        iterations_log2=iterations_log2,
        total_t_count_log2=scaled(oracle_report.t_count),
        total_toffoli_log2=scaled(oracle_report.toffoli_count),
        total_depth_log2=scaled(oracle_report.depth["depth"]),
        provenance={
            "oracle_costs": "MEASURED from a constructed circuit",
            "iterations": "ANALYTICAL (Grover query complexity)",
            "totals": "EXTRAPOLATED (product of the two above)",
            "t_count_model": oracle_report.clifford_t.get("model", "?"),
        },
        caveats=[
            "This is a logical-resource extrapolation. It has not been simulated "
            "and has certainly not been executed on hardware.",
            "Grover iterations are inherently serial: the total depth figure is a "
            "single sequential circuit, not something that parallelises away. "
            "Distributing the search over m machines buys only sqrt(m).",
            "Real attack costs are evaluated under a depth cap (NIST's MAXDEPTH). "
            "Under any plausible cap the full iteration count cannot be run, and "
            "the effective security is higher than the query count suggests.",
            "No error correction is included. Every T gate here is a logical T "
            "requiring a distilled magic state; see the physical estimator for "
            "what that implies under an explicit hardware model.",
            "Assumes a unique preimage and an idealised oracle success "
            "probability; real preimage search over a 256-bit digest with a "
            "shorter message has a different solution-density structure.",
        ],
    )
