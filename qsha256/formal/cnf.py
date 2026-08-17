"""Tseitin encoding of AIGs into CNF, and the SAT queries built on top.

An AIG node ``n = a AND b`` becomes three clauses::

    (-n or a) and (-n or b) and (n or -a or -b)

which is satisfied exactly when ``n`` really is ``a AND b``.  Adding those
clauses for every node in the cone of interest gives a formula whose models are
precisely the consistent assignments of the circuit -- so questions about the
circuit become questions for a SAT solver.

Two query shapes cover everything this project needs:

**Is this literal ever true?**  Used for ancilla cleanliness: assert the
ancilla's output function and ask for a model.  UNSAT means "false for every
input", i.e. the ancilla is provably returned to ``|0>``.

**Are these two literal vectors ever different?**  A *miter*: XOR them
pairwise, OR the results, assert it.  UNSAT means the two functions are
identical on every input -- a complete equivalence proof.

Both answers are one-sided in the useful direction: UNSAT is a proof, SAT hands
back a concrete counterexample that :mod:`qsha256.formal.equivalence` decodes
into readable register values.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from .aig import AIG, CONST_FALSE, CONST_TRUE, Lit, node_of

__all__ = ["DEFAULT_SOLVER", "SOLVERS", "CNFEncoder", "SatResult", "solve"]

#: Solvers bundled with python-sat.  Cadical is the strongest general-purpose
#: choice here; the others are kept so results can be cross-checked.
SOLVERS = ("cadical195", "glucose4", "minisat22")
DEFAULT_SOLVER = "cadical195"


@dataclass
class CNFEncoder:
    """Incremental Tseitin encoder for an :class:`~qsha256.formal.aig.AIG`."""

    aig: AIG
    clauses: list[list[int]] = field(default_factory=list)
    #: AIG node id -> CNF variable
    _var: dict[int, int] = field(default_factory=dict)
    _next_var: int = 1

    def __post_init__(self) -> None:
        # Variable 1 is pinned to false and represents the constant node.
        self._var[0] = self._fresh()
        self.clauses.append([-self._var[0]])

    def _fresh(self) -> int:
        var = self._next_var
        self._next_var += 1
        return var

    def literal(self, lit: Lit) -> int:
        """CNF literal for an AIG literal, encoding its cone on first use."""
        self._encode_cone(node_of(lit))
        var = self._var[node_of(lit)]
        return -var if lit & 1 else var

    def _encode_cone(self, root: int) -> None:
        if root in self._var:
            return
        # Iterative post-order so deep graphs cannot blow the Python stack.
        stack = [(root, False)]
        while stack:
            node, expanded = stack.pop()
            if node in self._var:
                continue
            if node not in self.aig.ands:
                self._var[node] = self._fresh()
                continue
            left, right = self.aig.ands[node]
            if not expanded:
                stack.append((node, True))
                stack.append((node_of(right), False))
                stack.append((node_of(left), False))
                continue
            var = self._fresh()
            self._var[node] = var
            lv = self._var[node_of(left)] * (-1 if left & 1 else 1)
            rv = self._var[node_of(right)] * (-1 if right & 1 else 1)
            self.clauses.append([-var, lv])
            self.clauses.append([-var, rv])
            self.clauses.append([var, -lv, -rv])

    # -- query builders ----------------------------------------------------

    def assert_true(self, lit: Lit) -> None:
        if lit == CONST_TRUE:
            return
        if lit == CONST_FALSE:
            self.clauses.append([])  # trivially unsatisfiable
            return
        self.clauses.append([self.literal(lit)])

    def any_true(self, lits: Sequence[Lit]) -> None:
        """Assert that at least one of ``lits`` is true."""
        clause: list[int] = []
        for lit in lits:
            if lit == CONST_TRUE:
                return  # trivially satisfiable, nothing to assert
            if lit == CONST_FALSE:
                continue
            clause.append(self.literal(lit))
        self.clauses.append(clause)  # empty clause if all were constant-false

    def miter(self, left: Sequence[Lit], right: Sequence[Lit]) -> None:
        """Assert that the two vectors differ somewhere.

        UNSAT afterwards proves they are equal for every input.
        """
        if len(left) != len(right):
            raise ValueError(f"vector length mismatch: {len(left)} vs {len(right)}")
        differences = [self.aig.xor(a, b) for a, b in zip(left, right)]
        self.any_true(differences)

    @property
    def num_vars(self) -> int:
        return self._next_var - 1


@dataclass
class SatResult:
    """Outcome of a SAT query.  ``unsat`` is the side that constitutes a proof."""

    satisfiable: bool
    model: list[int] | None = None
    num_vars: int = 0
    num_clauses: int = 0
    solver: str = ""
    seconds: float = 0.0
    #: True when the solver hit its budget. Such a result is never a proof.
    timed_out: bool = False
    #: True when the answer fell out of AIG constant folding and structural
    #: hashing, with no solver call. A stronger result than a SAT proof: the
    #: two functions are not merely equal, they are the *same graph node*.
    structural: bool = False

    @property
    def proved(self) -> bool:
        """True when the query was UNSAT, i.e. the property holds universally."""
        return not self.satisfiable and not self.timed_out


DEFAULT_TIMEOUT = 120.0


def solve(
    encoder: CNFEncoder, solver: str = DEFAULT_SOLVER, timeout: float | None = DEFAULT_TIMEOUT
) -> SatResult:
    """Run a SAT solver over the encoded formula.

    ``timeout`` bounds the search in seconds. On expiry the result is reported
    as *not proved* with ``timed_out`` set, never as a proof: an unfinished
    search establishes nothing.
    """
    from pysat.solvers import Solver

    started = time.time()

    # An empty clause is unsatisfiable by definition. It arises when the AIG's
    # constant folding and structural hashing already collapsed the query --
    # e.g. every miter difference folded to constant false, meaning the two
    # functions are literally the same node. That is a proof, and a stronger
    # one than the solver could give, so short-circuit rather than handing the
    # solver a formula it cannot bootstrap from.
    if any(not clause for clause in encoder.clauses):
        return SatResult(
            satisfiable=False,
            num_vars=encoder.num_vars,
            num_clauses=len(encoder.clauses),
            solver="structural (no SAT call)",
            seconds=time.time() - started,
            structural=True,
        )

    with Solver(name=solver, bootstrap_with=encoder.clauses, use_timer=True) as sat:
        if timeout is not None:
            from threading import Timer

            interrupt = Timer(timeout, lambda s: s.interrupt(), [sat])
            interrupt.start()
            try:
                satisfiable = sat.solve_limited(expect_interrupt=True)
            finally:
                interrupt.cancel()
            if satisfiable is None:
                return SatResult(
                    satisfiable=True,
                    num_vars=encoder.num_vars,
                    num_clauses=len(encoder.clauses),
                    solver=solver,
                    seconds=time.time() - started,
                    timed_out=True,
                )
        else:
            satisfiable = sat.solve()
        model = sat.get_model() if satisfiable else None
    return SatResult(
        satisfiable=satisfiable,
        model=model,
        num_vars=encoder.num_vars,
        num_clauses=len(encoder.clauses),
        solver=solver,
        seconds=time.time() - started,
    )


def model_assignment(encoder: CNFEncoder, model: Sequence[int], aig: AIG) -> list[int]:
    """Decode a SAT model into the AIG's input bit vector."""
    positive = {abs(v) for v in model if v > 0}
    bits = []
    for i in range(aig.num_inputs):
        node = i + 1
        var = encoder._var.get(node)
        bits.append(1 if var is not None and var in positive else 0)
    return bits
