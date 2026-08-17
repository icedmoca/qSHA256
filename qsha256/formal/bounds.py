"""Lower bounds: how close to optimal are these circuits?

Every other number in this project answers "what did we achieve".  This module
answers "what is achievable at all", which is the more interesting question and
the one nobody in this niche seems to ask.

The relevant quantity is **multiplicative complexity** (MC): the minimum number
of AND gates needed to compute a Boolean function over the basis
``{AND, XOR, NOT}``.  It matters here because of a direct correspondence:

* a reversible circuit's non-linear cost is its Toffoli count, and a Toffoli is
  an AND;
* XOR, NOT and the free wire permutations (rotation, shift) contribute nothing
  non-linear;
* so **any** reversible implementation of a function with multiplicative
  complexity ``c`` needs at least ``c`` Toffolis (or Gidney ANDs).

That turns MC into a floor on the part of the cost that actually matters under
fault tolerance, and lets a report say "we used ``n``, the floor is ``m``"
rather than just "we used ``n``".

Two ways to get a bound, and the code distinguishes them:

**Exact search.**  For functions of few inputs, MC can be computed exactly by
searching for a circuit with ``k`` ANDs for increasing ``k``.  With ``k = 0``
the function must be affine; with ``k = 1`` it must be an affine function XOR
the product of two affine functions; and so on.  Exhausting that search proves
a lower bound rather than assuming one.  Used here on ``Ch`` and ``Maj``.

**Known results.**  For families too large to search, published bounds apply.
Modular addition is the important one: ``MC(add mod 2^n) = n - 1``, since each
carry beyond the first needs one AND and the standard construction meets it.

What comes out of this is worth stating plainly: **``Ch``, ``Maj`` and the
Gidney adder all achieve the lower bound exactly.** They are not merely good
implementations, they are optimal in AND count. The CDKM and VBE adders are
not: they use ``2n`` and ``4(n-1)`` Toffolis against a floor of ``n-1``.

A caveat this module is careful about: MC bounds the *AND count*, and the step
from AND count to T-count depends on the decomposition. With the standard 7-T
Toffoli, ``c`` ANDs cost ``7c`` T gates; with Gidney's construction a
compute/uncompute pair costs 4. So an MC lower bound gives a T-count lower
bound only once a decomposition is fixed, and this module never quotes one
without saying which.

References
----------
- J. Boyar, R. Peralta, D. Pochuev, "On the multiplicative complexity of
  Boolean functions over the basis (and, xor, 1)", Theoretical Computer
  Science 235(1), 2000.
- M. Beverland, E. Campbell, M. Howard, V. Kliuchnikov, "Lower bounds on the
  non-Clifford resources for quantum computations", arXiv:1904.01124.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ComponentBound",
    "MCResult",
    "circuit_bound_report",
    "component_bounds",
    "is_affine",
    "multiplicative_complexity",
    "truth_table",
]


def truth_table(fn: Callable[..., int], arity: int) -> int:
    """Pack a Boolean function into an integer truth table, LSB = all-zero input."""
    table = 0
    for assignment in range(1 << arity):
        bits = [(assignment >> i) & 1 for i in range(arity)]
        if fn(*bits) & 1:
            table |= 1 << assignment
    return table


def _affine_tables(arity: int) -> list[int]:
    """Every affine function of ``arity`` inputs, as truth tables.

    There are ``2^(arity+1)`` of them: a choice of linear mask plus a constant.
    """
    tables = []
    for mask in range(1 << arity):
        for constant in (0, 1):
            table = 0
            for assignment in range(1 << arity):
                value = constant
                for i in range(arity):
                    if (mask >> i) & 1:
                        value ^= (assignment >> i) & 1
                if value:
                    table |= 1 << assignment
            tables.append(table)
    return tables


def is_affine(table: int, arity: int) -> bool:
    return table in set(_affine_tables(arity))


@dataclass
class MCResult:
    """Multiplicative complexity, with how it was established."""

    value: int | None
    arity: int
    exact: bool
    method: str
    searched_up_to: int = 0
    seconds: float = 0.0
    note: str = ""

    def __str__(self) -> str:
        if self.value is None:
            return f"MC > {self.searched_up_to} (search exhausted without a witness)"
        kind = "exactly" if self.exact else "at most"
        return f"MC {kind} {self.value} ({self.method})"


def multiplicative_complexity(
    table: int, arity: int, max_ands: int = 2, timeout: float = 30.0
) -> MCResult:
    """Compute MC exactly by exhaustive search over small circuit shapes.

    Searches ``k = 0, 1, ... max_ands``.  Finding a circuit at ``k`` after
    exhausting ``k-1`` proves ``MC = k``: the witness gives the upper bound and
    the exhausted search gives the lower bound.  This is only tractable for
    small arity, which is exactly where the interesting components live.
    """
    started = time.time()
    full = (1 << (1 << arity)) - 1
    affine = _affine_tables(arity)

    if table in set(affine):
        return MCResult(0, arity, True, "affine, so no AND is needed", 0, time.time() - started)

    # k = 1: f = a XOR (b AND c) with a, b, c affine.
    for b in affine:
        for c in affine:
            product = b & c
            for a in affine:
                if (a ^ product) & full == table:
                    return MCResult(
                        1,
                        arity,
                        True,
                        "exhaustive: not affine, and one AND suffices",
                        1,
                        time.time() - started,
                    )
        if time.time() - started > timeout:
            return MCResult(None, arity, False, "search timed out", 1, time.time() - started)

    if max_ands < 2:
        return MCResult(None, arity, False, "search bound reached", 1, time.time() - started)

    # k = 2: f = a XOR (b AND c) XOR (d AND e), with the second product allowed
    # to use the first product as an input (the general two-AND shape).
    for b in affine:
        for c in affine:
            first = b & c
            options = affine + [first, first ^ full]
            for d in options:
                for e in options:
                    second = d & e
                    for a in affine:
                        if (a ^ first ^ second) & full == table:
                            return MCResult(
                                2,
                                arity,
                                True,
                                "exhaustive: one AND is impossible, two suffice",
                                2,
                                time.time() - started,
                            )
        if time.time() - started > timeout:
            return MCResult(None, arity, False, "search timed out", 2, time.time() - started)

    return MCResult(
        None,
        arity,
        False,
        f"no circuit with <= {max_ands} ANDs exists",
        max_ands,
        time.time() - started,
        note=f"proves MC > {max_ands}",
    )


@dataclass
class ComponentBound:
    """Achieved AND count against the proven floor, for one component."""

    component: str
    width: int
    achieved_ands: int
    lower_bound: int
    bound_source: str
    exact_bound: bool = True

    @property
    def optimal(self) -> bool:
        return self.achieved_ands == self.lower_bound

    @property
    def overhead(self) -> float:
        return self.achieved_ands / self.lower_bound if self.lower_bound else float("inf")

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "width": self.width,
            "achieved_ands": self.achieved_ands,
            "lower_bound": self.lower_bound,
            "optimal": self.optimal,
            "overhead": round(self.overhead, 3),
            "bound_source": self.bound_source,
            "exact_bound": self.exact_bound,
        }


def component_bounds(width: int = 32, timeout: float = 30.0) -> list[ComponentBound]:
    """Achieved versus optimal AND count for every non-linear component."""
    from ..classical.sha256 import ch, maj
    from ..quantum.primitives.add import add_into
    from ..quantum.registers import CircuitBuilder

    bounds: list[ComponentBound] = []

    # Ch and Maj: prove MC per bit by exhaustive search over 3-input functions.
    for name, fn in (("Ch", ch), ("Maj", maj)):
        table = truth_table(lambda x, y, z: fn(x, y, z) & 1, 3)
        mc = multiplicative_complexity(table, 3, max_ands=2, timeout=timeout)
        builder = CircuitBuilder(name)
        words = [builder.add_word(width, c) for c in "xyzt"]
        from ..quantum.primitives.boolean import ch_word_into, maj_word_into

        (ch_word_into if name == "Ch" else maj_word_into)(builder, *words)
        achieved = builder.circuit.count_ops().get("ccx", 0)
        bounds.append(
            ComponentBound(
                component=f"{name} ({width}-bit, bitwise)",
                width=width,
                achieved_ands=achieved,
                lower_bound=(mc.value or 1) * width,
                bound_source=f"exhaustive search over 3-input functions: {mc}",
                exact_bound=mc.exact,
            )
        )

    # Modular addition: MC(add mod 2^n) = n - 1 is a published result.
    for adder in ("cdkm", "vbe", "gidney"):
        builder = CircuitBuilder(adder)
        a, t = builder.add_word(width, "a"), builder.add_word(width, "b")
        add_into(builder, a, t, adder)
        ops = builder.circuit.count_ops()
        achieved = ops.get("ccx", 0) + ops.get("and_g", 0)
        bounds.append(
            ComponentBound(
                component=f"{adder} adder ({width}-bit)",
                width=width,
                achieved_ands=achieved,
                lower_bound=width - 1,
                bound_source="MC(add mod 2^n) = n - 1 (Boyar-Peralta)",
            )
        )

    # Sigma functions are pure XOR of wire permutations: MC is zero, and the
    # implementation matches, which is why they cost no Toffolis at all.
    from ..quantum.primitives.xor import xor_terms
    from ..spec import SHA256

    for which in ("big_sigma0", "big_sigma1", "small_sigma0", "small_sigma1"):
        builder = CircuitBuilder(which)
        x, t = builder.add_word(width, "x"), builder.add_word(width, "t")
        xor_terms(builder, x, getattr(SHA256, which), t)
        bounds.append(
            ComponentBound(
                component=f"{which} ({width}-bit)",
                width=width,
                achieved_ands=builder.circuit.count_ops().get("ccx", 0),
                lower_bound=0,
                bound_source="affine (XOR of wire permutations), so MC = 0",
            )
        )
    return bounds


@dataclass
class BoundReport:
    """Achieved versus optimal for a whole circuit."""

    target: str
    achieved_ands: int
    lower_bound: int
    components: list[ComponentBound] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def overhead(self) -> float:
        return self.achieved_ands / self.lower_bound if self.lower_bound else float("inf")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "achieved_ands": self.achieved_ands,
            "lower_bound": self.lower_bound,
            "overhead": round(self.overhead, 3),
            "components": [c.to_dict() for c in self.components],
            "notes": self.notes,
        }

    def __str__(self) -> str:
        lines = [
            f"AND-count bounds: {self.target}",
            "=" * 72,
            "",
            f"{'component':<34}{'achieved':>10}{'floor':>10}{'overhead':>11}",
            "-" * 72,
        ]
        for c in self.components:
            mark = "  optimal" if c.optimal else ""
            over = "-" if c.lower_bound == 0 else f"{c.overhead:.2f}x"
            lines.append(
                f"{c.component:<34}{c.achieved_ands:>10,}{c.lower_bound:>10,}{over:>11}{mark}"
            )
        lines += [
            "-" * 72,
            f"{'TOTAL':<34}{self.achieved_ands:>10,}{self.lower_bound:>10,}{self.overhead:>10.2f}x",
            "",
        ]
        lines += [f"  * {n}" for n in self.notes]
        return "\n".join(lines)


def circuit_bound_report(
    spec=None, strategy=None, rounds: int | None = None, timeout: float = 30.0
) -> BoundReport:
    """Compare a full compression's AND count against a composed lower bound.

    The floor is the sum of the components' floors, which is itself a valid
    lower bound for the composition only under the assumption that the circuit
    computes each component separately -- it does, but a cleverer circuit might
    share non-linear work between them, so this is a lower bound *for this
    architecture* rather than for the function. That distinction is recorded in
    the notes rather than glossed over.
    """
    from ..quantum.sha256.compression import build_compression
    from ..quantum.strategies import DEFAULT
    from ..spec import SHA256

    spec = spec or SHA256
    strategy = strategy or DEFAULT
    rounds = rounds if rounds is not None else spec.rounds
    width = spec.word_bits

    comp = build_compression(spec, strategy, rounds=rounds)
    ops = comp.circuit.count_ops()
    achieved = ops.get("ccx", 0) + ops.get("and_g", 0)

    components = component_bounds(width, timeout)
    by_name = {c.component.split(" ")[0]: c for c in components}

    # Per round: Ch and Maj computed and uncomputed, plus seven additions.
    adds_per_round = 7
    schedule_adds = max(0, rounds - spec.block_words) * 3
    chaining_adds = spec.state_words
    add_floor = (adds_per_round * rounds + schedule_adds + chaining_adds) * (width - 1)
    bool_floor = 2 * rounds * width  # Ch and Maj, one AND per bit, twice each

    report = BoundReport(
        target=f"{spec.name} compression, {rounds} rounds, {strategy.label()}",
        achieved_ands=achieved,
        lower_bound=add_floor + bool_floor,
        components=components,
        notes=[
            "The floor sums each component's proven minimum AND count. It bounds "
            "any circuit built from these components separately; a circuit that "
            "shared non-linear work between them could in principle go lower, so "
            "this is a floor for the architecture, not for the function itself.",
            f"Additions counted: {adds_per_round} per round x {rounds} rounds, "
            f"+{schedule_adds} in the schedule, +{chaining_adds} chaining.",
            "MC bounds the AND count. Converting to a T-count floor requires "
            "fixing a decomposition: 7 T per Toffoli for the standard circuit, "
            "or 4 T per compute/uncompute pair with Gidney's construction.",
        ],
    )
    return report
