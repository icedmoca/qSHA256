"""The design space: every knob that changes the circuit without changing the function.

A :class:`Strategy` is a point in qSHA256's design space.  Two circuits built
from the same :class:`~qsha256.spec.ShaSpec` but different strategies compute
*exactly the same function* while differing -- often by large factors -- in
qubits, Toffoli count, T-depth and total depth.

That is what makes automated search possible: the strategy is a small, discrete,
fully-enumerable parameter vector, and every point in it is correct by
construction.  Search never has to invent a circuit and hope it is right; it
explores a space in which correctness is an invariant, and the equivalence
checker in :mod:`qsha256.quantum.optimization.verify` confirms it empirically.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from itertools import product
from typing import Any, Iterator

__all__ = [
    "Strategy",
    "DEFAULT",
    "MIN_QUBITS",
    "MIN_DEPTH",
    "PRESETS",
    "get_preset",
    "STRATEGY_AXES",
    "enumerate_strategies",
]


#: The discrete axes of the design space, with their legal values.  Search
#: enumerates the product of these; each combination yields a correct circuit.
STRATEGY_AXES: dict[str, tuple[Any, ...]] = {
    "adder": ("cdkm", "vbe", "qft"),
    "const_add": ("load", "vbe_const"),
    "schedule": ("rolling", "store_all"),
    "round_layout": ("serial", "wide", "csa"),
    "uncompute_working": (False, True),
}


@dataclass(frozen=True)
class Strategy:
    """A complete, self-describing choice of circuit architecture."""

    #: Reversible modular adder used for every quantum-quantum addition.
    adder: str = "cdkm"

    #: How round constants ``K[t]`` are added.  ``"load"`` materialises the
    #: constant in a borrowed register; ``"vbe_const"`` folds the classical bits
    #: into the gate sequence at build time.
    const_add: str = "load"

    #: ``"rolling"`` keeps a 16-word window and transforms ``W[t-16]`` into
    #: ``W[t]`` in place (fewest qubits).  ``"store_all"`` materialises all 64
    #: schedule words in dedicated registers (fewer gates: the first term of
    #: each new word is a free CNOT copy into a zeroed register rather than an
    #: addition, saving one adder per word).
    schedule: str = "rolling"

    #: ``"serial"`` accumulates the round's addends one at a time through a
    #: single recycled temporary (fewest qubits).  ``"wide"`` computes the four
    #: independent sub-expressions into separate temporaries so they occupy
    #: disjoint qubits and compile into parallel layers.  ``"csa"`` replaces the
    #: chain of ripple adders with a carry-save tree plus a single carry
    #: propagation (see :mod:`qsha256.quantum.primitives.csa`).
    round_layout: str = "serial"

    #: Whether to run the inverse round sequence after the final chaining
    #: addition, returning the working registers to ``|0>``.  Required for use
    #: inside a Grover oracle; roughly doubles the round cost.
    uncompute_working: bool = False

    #: Names of peephole rewrite passes applied after construction.
    rewrites: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        for axis, allowed in STRATEGY_AXES.items():
            value = getattr(self, axis)
            if value not in allowed:
                raise ValueError(f"strategy.{axis}={value!r} not in {allowed}")

    # -- presentation ------------------------------------------------------

    def label(self) -> str:
        """Compact, stable identifier used in reports and benchmark tables."""
        parts = [self.adder, self.const_add, self.schedule, self.round_layout]
        if self.uncompute_working:
            parts.append("uncomp")
        if self.rewrites:
            parts.append("+".join(self.rewrites))
        return "/".join(parts)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rewrites"] = list(self.rewrites)
        d["label"] = self.label()
        return d

    def with_(self, **changes: Any) -> Strategy:
        return replace(self, **changes)


DEFAULT = Strategy()

#: Minimise logical qubits: in-place rolling schedule, one recycled temporary,
#: constant addition without a constant register.
MIN_QUBITS = Strategy(
    adder="cdkm", const_add="vbe_const", schedule="rolling", round_layout="serial"
)

#: Minimise depth: independent sub-expressions on disjoint qubits, all schedule
#: words materialised so schedule and rounds do not contend for registers.
MIN_DEPTH = Strategy(adder="cdkm", const_add="load", schedule="store_all", round_layout="csa")

#: Oracle-shaped: everything returned to |0> so the circuit can be inverted.
ORACLE = Strategy(uncompute_working=True)

PRESETS: dict[str, Strategy] = {
    "default": DEFAULT,
    "min-qubits": MIN_QUBITS,
    "min-depth": MIN_DEPTH,
    "oracle": ORACLE,
}


def get_preset(name: str) -> Strategy:
    try:
        return PRESETS[name]
    except KeyError:
        raise KeyError(f"unknown preset {name!r}; available: {sorted(PRESETS)}") from None


def enumerate_strategies(
    axes: dict[str, tuple[Any, ...]] | None = None,
    **fixed: Any,
) -> Iterator[Strategy]:
    """Enumerate the design space, optionally pinning some axes.

    ``enumerate_strategies(uncompute_working=False)`` walks every architecture
    that does not uncompute; ``enumerate_strategies(adder=("cdkm", "vbe"))``
    restricts the adder axis.  This is the generator the Pareto search consumes.
    """
    space = dict(axes or STRATEGY_AXES)
    for name, value in fixed.items():
        space[name] = value if isinstance(value, tuple) else (value,)
    names = list(space)
    for combo in product(*(space[n] for n in names)):
        yield Strategy(**dict(zip(names, combo)))
