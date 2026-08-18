"""The reversible pebble game, solved exactly by SAT.

qSHA256 offers two message-schedule strategies -- ``rolling`` (16 registers,
transform in place) and ``store_all`` (64 registers) -- and the benchmark
measures which is cheaper.  But measuring two hand-picked points does not say
what the *best possible* tradeoff is.  That question has a precise formulation.

The schedule is a DAG: ``W[t]`` depends on ``W[t-2]``, ``W[t-7]``, ``W[t-15]``
and ``W[t-16]``.  Computing it in a reversible circuit with a bounded number of
registers is exactly the **reversible pebble game** (Bennett 1989):

* a pebble on a node means "this value is currently held in a register";
* a pebble may be **placed** on a node only when all its predecessors are
  pebbled -- you can only compute a value from values you still have;
* a pebble may be **removed** under the *same* condition, because in a
  reversible circuit erasing a value means running its computation backwards,
  which needs the same inputs;
* the number of simultaneously-placed pebbles is the register count.

The symmetry between placement and removal is what makes it *reversible*
pebbling, and it is what makes the problem hard: you cannot simply drop a value
you no longer want.

Encoding this into SAT and searching for the smallest pebble count gives a
**provably optimal** space/recomputation frontier, rather than two points
someone happened to implement.  The encoding follows Meuli, Soeken, Roetteler,
Bjorner and De Micheli, *Reversible pebbling game for quantum memory
management* (DATE 2019).

Answers come in two flavours, and the code never conflates them:

* a **strategy** is a witness -- SAT found a concrete move sequence, so that
  many pebbles provably suffice;
* an **impossibility** is a proof -- UNSAT for a given (pebbles, steps) bound,
  so no strategy exists within it.

A timeout is neither, and is reported as ``UNKNOWN``.

What an impossibility does and does not establish
-------------------------------------------------

Read the step bound carefully.  UNSAT at ``k`` pebbles and ``S`` steps proves
that no strategy exists **using at most S moves**.  It does not prove that none
exists with more, and that distinction is the whole subject: extra steps buy
recomputation, and recomputation is exactly what trades against registers.  A
result quoted without its step budget is not a result.

For SHA-256 the impossibility at 15 registers has been checked at step budgets
of 48, 64, 96, 128, 192 and 256 -- the last being 5.3x the 48-move minimum --
and holds at every one.  That is strong evidence and it is still a bounded
statement; an unbounded-step lower bound is not established here.

The move set is also part of the theorem
----------------------------------------

Change the moves and the answer changes; this module learned that the hard way
(see ``allow_inplace``).  The rules in force are stated exactly:

1. Initially the input nodes are pebbled and nothing else.
2. **place(v)** -- pebble ``v``; requires every predecessor of ``v`` pebbled.
3. **remove(v)** -- unpebble ``v``; requires the same, since uncomputing means
   running the computation backwards.
4. **move(u -> v)** -- transform ``u``'s register into ``v`` in place; requires
   ``u`` to be a predecessor of ``v`` and every other predecessor pebbled.
   Enabled by ``allow_inplace``; not part of the classical game.
5. At most one move per step.  For a question about *space* this is without
   loss of generality: allowing simultaneous moves cannot lower the peak
   number of simultaneously-pebbled nodes.
6. The cost measured is the maximum number of simultaneously-pebbled nodes.

The DAG is the schedule's dependency graph at **word** granularity.  A circuit
that restructured the recurrence algebraically, or worked at bit granularity,
is outside the model entirely.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..classical.sha256 import schedule_offsets
from ..spec import SHA256, ShaSpec

__all__ = [
    "PebbleDAG",
    "PebblingResult",
    "minimise_pebbles",
    "pebbling_frontier",
    "schedule_dag",
    "solve_pebbling",
]


@dataclass
class PebbleDAG:
    """A dependency DAG for the pebble game."""

    #: node -> its predecessors
    preds: dict[int, list[int]] = field(default_factory=dict)
    #: nodes that start pebbled (the message block)
    inputs: list[int] = field(default_factory=list)
    #: nodes that must be pebbled at some point (the expanded schedule words)
    targets: list[int] = field(default_factory=list)
    name: str = ""

    @property
    def nodes(self) -> list[int]:
        return sorted(self.preds)

    def __len__(self) -> int:
        return len(self.preds)


def schedule_dag(spec: ShaSpec = SHA256, rounds: int | None = None) -> PebbleDAG:
    """The message-schedule dependency DAG for a given spec."""
    rounds = spec.rounds if rounds is None else rounds
    o16, o15, o7, o2 = schedule_offsets(spec)
    m = spec.block_words
    preds: dict[int, list[int]] = {t: [] for t in range(m)}
    for t in range(m, rounds):
        preds[t] = sorted({t - o2, t - o7, t - o15, t - o16})
    return PebbleDAG(
        preds=preds,
        inputs=list(range(m)),
        targets=list(range(m, rounds)),
        name=f"{spec.name} schedule r={rounds}",
    )


@dataclass
class PebblingResult:
    """Outcome of one pebbling query.

    Always carries its ``steps`` budget, because an impossibility is only ever
    an impossibility *within that budget*.
    """

    status: str  # "STRATEGY" | "IMPOSSIBLE" | "UNKNOWN"
    pebbles: int
    steps: int
    dag: str = ""
    #: ``(step, node, placed)`` for each move, when a strategy was found.
    moves: list[tuple[int, int, bool]] = field(default_factory=list)
    seconds: float = 0.0
    num_vars: int = 0
    num_clauses: int = 0

    @property
    def found(self) -> bool:
        return self.status == "STRATEGY"

    @property
    def proved_impossible(self) -> bool:
        return self.status == "IMPOSSIBLE"

    @property
    def computations(self) -> int:
        """Placements: how many times a value is computed, recomputation included."""
        return sum(1 for _, _, placed in self.moves if placed)

    #: The move set the result is relative to; part of the theorem statement.
    allow_inplace: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pebbles": self.pebbles,
            "steps": self.steps,
            "dag": self.dag,
            "computations": self.computations,
            "allow_inplace": self.allow_inplace,
            "claim": (
                f"{self.pebbles} registers suffice within {self.steps} moves"
                if self.found
                else f"no strategy with {self.pebbles} registers within {self.steps} moves"
                if self.proved_impossible
                else "undetermined (timeout)"
            ),
            "seconds": round(self.seconds, 2),
            "cnf_vars": self.num_vars,
            "cnf_clauses": self.num_clauses,
        }

    def __str__(self) -> str:
        if self.found:
            return (
                f"{self.pebbles} pebbles, {self.steps} steps: STRATEGY FOUND "
                f"({self.computations} computations, {self.seconds:.1f}s)"
            )
        if self.proved_impossible:
            return (
                f"{self.pebbles} pebbles: PROVED IMPOSSIBLE WITHIN {self.steps} "
                f"STEPS ({self.seconds:.1f}s)"
            )
        return (
            f"{self.pebbles} pebbles, {self.steps} steps: UNKNOWN "
            f"(timeout after {self.seconds:.1f}s)"
        )


def solve_pebbling(
    dag: PebbleDAG,
    pebbles: int,
    steps: int,
    timeout: float = 60.0,
    require_cleanup: bool = False,
    allow_inplace: bool = True,
    solver: str = "cadical195",
) -> PebblingResult:
    """Is there a reversible pebbling of ``dag`` with ``pebbles`` in ``steps``?

    Three move types, one per step:

    ``place(v)``      pebble ``v``; needs every predecessor of ``v`` pebbled.
    ``remove(v)``     unpebble ``v``; needs the same, because uncomputing means
                      running the computation backwards.
    ``move(u -> v)``  transform ``u``'s register into ``v`` in place; needs
                      ``u`` to be a predecessor of ``v`` and every *other*
                      predecessor pebbled. Enabled by ``allow_inplace``.

    The in-place move is not part of the classical reversible pebble game, and
    including it is the difference between modelling this circuit and modelling
    a different one. SHA-256's recurrence has ``W[t-16]`` as an *addend* of
    ``W[t]``, so accumulating the other three terms into that register turns it
    into ``W[t]`` without the two ever coexisting. Standard pebbling would
    charge a pebble for the new value while the old one is still held; this
    circuit does not pay that, and the measured 16-register rolling schedule is
    the proof. Without this move the model would declare the implementation
    impossible, which is a good reminder that a formalisation is only as
    truthful as its move set.
    """
    from pysat.card import CardEnc, EncType
    from pysat.formula import IDPool

    from .cnf import solve_bounded

    started = time.time()
    pool = IDPool()
    nodes = dag.nodes
    inputs = set(dag.inputs)

    def pebbled(node: int, step: int) -> int:
        return pool.id(("p", node, step))

    clauses: list[list[int]] = []

    # Step 0: exactly the inputs are pebbled.
    for node in nodes:
        clauses.append([pebbled(node, 0)] if node in inputs else [-pebbled(node, 0)])

    for step in range(1, steps + 1):
        actions: list[int] = []
        # touching[node] collects every action that changes this node's state,
        # so the frame axiom can say "unchanged unless some action touched it".
        touching: dict[int, list[int]] = {node: [] for node in nodes}

        for node in nodes:
            preds = dag.preds[node]

            place = pool.id(("place", node, step))
            actions.append(place)
            touching[node].append(place)
            clauses.append([-place, -pebbled(node, step - 1)])
            clauses.append([-place, pebbled(node, step)])
            for pred in preds:
                clauses.append([-place, pebbled(pred, step - 1)])

            remove = pool.id(("remove", node, step))
            actions.append(remove)
            touching[node].append(remove)
            clauses.append([-remove, pebbled(node, step - 1)])
            clauses.append([-remove, -pebbled(node, step)])
            for pred in preds:
                clauses.append([-remove, pebbled(pred, step - 1)])

            if allow_inplace:
                for source in preds:
                    move = pool.id(("move", source, node, step))
                    actions.append(move)
                    touching[node].append(move)
                    touching[source].append(move)
                    clauses.append([-move, pebbled(source, step - 1)])
                    clauses.append([-move, -pebbled(node, step - 1)])
                    clauses.append([-move, -pebbled(source, step)])
                    clauses.append([-move, pebbled(node, step)])
                    for pred in preds:
                        if pred != source:
                            clauses.append([-move, pebbled(pred, step - 1)])

        # At most one action per step.
        clauses.extend(
            CardEnc.atmost(lits=actions, bound=1, vpool=pool, encoding=EncType.seqcounter)
        )

        # Frame: a node's state changes only if some action touched it.
        for node in nodes:
            before, after = pebbled(node, step - 1), pebbled(node, step)
            clauses.append([before, -after] + touching[node])
            clauses.append([-before, after] + touching[node])

    # Register budget at every step.
    for step in range(steps + 1):
        live = [pebbled(node, step) for node in nodes]
        clauses.extend(
            CardEnc.atmost(lits=live, bound=pebbles, vpool=pool, encoding=EncType.seqcounter)
        )

    # Every target must be pebbled at some point.
    for target in dag.targets:
        clauses.append([pebbled(target, step) for step in range(steps + 1)])

    if require_cleanup:
        for node in nodes:
            if node not in inputs:
                clauses.append([-pebbled(node, steps)])

    # Bounded in a subprocess so the strongest solver can still be used: on
    # these instances CaDiCaL answers in a fraction of a second where the
    # interruptible solvers do not finish at all.
    answer, model = solve_bounded(clauses, solver, timeout)

    elapsed = time.time() - started
    status = "UNKNOWN" if answer is None else ("STRATEGY" if answer else "IMPOSSIBLE")

    moves: list[tuple[int, int, bool]] = []
    if answer and model:
        truth = {abs(v) for v in model if v > 0}
        for step in range(1, steps + 1):
            for node in nodes:
                before = pebbled(node, step - 1) in truth
                after = pebbled(node, step) in truth
                if before != after:
                    moves.append((step, node, after))

    return PebblingResult(
        status=status,
        pebbles=pebbles,
        steps=steps,
        dag=dag.name,
        moves=moves,
        seconds=elapsed,
        num_vars=pool.top,
        num_clauses=len(clauses),
        allow_inplace=allow_inplace,
    )


def minimise_pebbles(
    dag: PebbleDAG,
    steps: int,
    low: int = 1,
    high: int | None = None,
    timeout: float = 60.0,
    require_cleanup: bool = False,
    allow_inplace: bool = True,
) -> tuple[int | None, list[PebblingResult]]:
    """Binary-search the smallest pebble count that admits a strategy.

    Returns ``(best, trace)``.  ``best`` is ``None`` when no bound was settled
    within the timeout, and the trace records every query so the report can say
    which bounds were *proved* and which merely timed out.
    """
    high = high if high is not None else len(dag)
    trace: list[PebblingResult] = []
    best: int | None = None
    while low <= high:
        mid = (low + high) // 2
        result = solve_pebbling(dag, mid, steps, timeout, require_cleanup, allow_inplace)
        trace.append(result)
        if result.found:
            best = mid
            high = mid - 1
        elif result.proved_impossible:
            low = mid + 1
        else:
            break  # UNKNOWN: neither bound moves, stop rather than guess
    return best, trace


def pebbling_frontier(
    dag: PebbleDAG,
    step_budgets: Sequence[int],
    timeout: float = 60.0,
    require_cleanup: bool = False,
) -> list[tuple[int, int | None]]:
    """The space/time frontier: minimum pebbles achievable per step budget.

    More steps means more recomputation is allowed, which permits fewer
    registers.  This is the curve the ``rolling`` and ``store_all`` strategies
    each occupy a single point on.
    """
    frontier = []
    for steps in step_budgets:
        best, _ = minimise_pebbles(dag, steps, timeout=timeout, require_cleanup=require_cleanup)
        frontier.append((steps, best))
    return frontier
