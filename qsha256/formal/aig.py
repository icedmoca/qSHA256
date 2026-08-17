"""And-Inverter Graphs, and symbolic execution of reversible circuits into them.

Every compute circuit qSHA256 builds is a permutation circuit: only ``X``,
``CNOT``, ``CCX`` and the Gidney AND gates, all of which act classically on the
computational basis.  That means each qubit's value at any point in the circuit
is a *Boolean function* of the circuit's inputs, and the whole circuit can be
symbolically executed -- propagating functions instead of bits -- to obtain an
exact functional description.

Representing those functions as an **And-Inverter Graph** (a DAG of two-input
AND nodes with inverted edges, the standard structure in hardware equivalence
checking) makes them cheap to build, structurally hashed so identical
subfunctions are shared, and directly encodable into CNF for a SAT solver.

This is what turns correctness from a *sample* into a *proof*.  Randomised
basis-state testing checks a few thousand inputs out of ``2^768``; symbolic
execution plus a SAT call settles all of them at once.

The classical reference model is executed through the *same* AIG API (see
:mod:`qsha256.formal.spec_aig`), so the two sides of an equivalence check are
built by different code paths and compared structurally.

AIGER export is provided so external tools (ABC, and anything else that reads
AIGER) can re-check the results independently, but nothing here depends on an
external binary: the bundled SAT solver does the work.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from qiskit import QuantumCircuit
from qiskit.circuit import Qubit

__all__ = [
    "AIG",
    "CONST_FALSE",
    "CONST_TRUE",
    "DEFAULT_MAX_NODES",
    "MAX_XOR_ATOMS",
    "AIGTooLarge",
    "Lit",
    "UnsupportedForSymbolicExecution",
    "symbolic_execute",
]


class UnsupportedForSymbolicExecution(Exception):
    """Raised for a gate that does not act classically on the computational basis."""


class AIGTooLarge(Exception):
    """Raised when a graph exceeds its node budget.

    Symbolic execution of a large circuit can produce an enormous graph, and an
    unbounded build will happily consume all available memory. Every entry point
    therefore carries a node budget and fails cleanly instead of thrashing.
    """


#: Default ceiling on AND nodes. Generous enough for a full 64-round circuit's
#: *structure*, small enough that overrunning it fails in seconds rather than
#: taking the machine down.
DEFAULT_MAX_NODES = 4_000_000

#: XOR canonicalisation keeps a set of atoms per literal. That is what makes
#: compute/uncompute pairs cancel structurally, but the sets themselves cost
#: memory proportional to their size, and deep arithmetic can grow them without
#: bound. Past this width a literal is treated as an opaque atom instead: the
#: graph stays correct, it just loses some structural cancellation.
MAX_XOR_ATOMS = 64


#: A literal is an integer: ``node << 1 | inverted``.  Node 0 is the constant
#: false, so literal 0 is FALSE and literal 1 is TRUE.  This is the standard
#: AIGER literal encoding.
Lit = int

CONST_FALSE: Lit = 0
CONST_TRUE: Lit = 1


def negate(lit: Lit) -> Lit:
    return lit ^ 1


def is_negated(lit: Lit) -> bool:
    return bool(lit & 1)


def node_of(lit: Lit) -> int:
    return lit >> 1


@dataclass
class AIG:
    """An And-Inverter Graph with structural hashing and constant folding.

    Nodes are numbered from 1; node 0 is the constant.  ``ands[i]`` holds the
    two input literals of node ``i``.  Inputs occupy the first ``num_inputs``
    node ids after the constant.
    """

    num_inputs: int = 0
    #: node id -> (left literal, right literal), for AND nodes only
    ands: dict[int, tuple[Lit, Lit]] = field(default_factory=dict)
    #: structural hash: (left, right) -> node id
    _hash: dict[tuple[Lit, Lit], int] = field(default_factory=dict, repr=False)
    #: XOR-awareness. SHA-256 is overwhelmingly XOR, and a plain AIG cannot see
    #: that ``a XOR b XOR a`` is ``b`` -- the operands hash to different nodes.
    #: Tracking each literal's decomposition into a parity of "atoms" (literals
    #: that are not themselves XORs) makes that cancellation structural, which
    #: is exactly what a compute/uncompute pair needs to fold away. This turns
    #: most cleanliness and equivalence obligations into constant folding with
    #: no solver call at all.
    _xor_atoms: dict[Lit, tuple[frozenset[Lit], int]] = field(default_factory=dict, repr=False)
    _xor_cache: dict[tuple[frozenset[Lit], int], Lit] = field(default_factory=dict, repr=False)
    _next_node: int = 1
    #: human-readable names for inputs, for debugging and AIGER symbol tables
    input_names: list[str] = field(default_factory=list)
    #: Ceiling on AND nodes; exceeding it raises :class:`AIGTooLarge`.
    max_nodes: int = DEFAULT_MAX_NODES

    # -- construction ------------------------------------------------------

    def new_input(self, name: str = "") -> Lit:
        node = self._next_node
        self._next_node += 1
        self.num_inputs += 1
        self.input_names.append(name or f"i{self.num_inputs - 1}")
        return node << 1

    def and_(self, a: Lit, b: Lit) -> Lit:
        """AND with constant folding and structural hashing.

        The folding rules matter for scale: SHA-256's circuits contain a great
        many trivially-constant subterms (freshly-zeroed ancillas, in
        particular), and collapsing them here keeps the graph from exploding.
        """
        if a == CONST_FALSE or b == CONST_FALSE:
            return CONST_FALSE
        if a == CONST_TRUE:
            return b
        if b == CONST_TRUE:
            return a
        if a == b:
            return a
        if a == negate(b):
            return CONST_FALSE
        if a > b:
            a, b = b, a
        key = (a, b)
        existing = self._hash.get(key)
        if existing is not None:
            return existing << 1
        if len(self.ands) >= self.max_nodes:
            raise AIGTooLarge(
                f"AND-node budget of {self.max_nodes:,} exhausted. Raise "
                "AIG.max_nodes if you have the memory, or prove a smaller "
                "instance -- these graphs grow quickly with round count."
            )
        node = self._next_node
        self._next_node += 1
        self.ands[node] = key
        self._hash[key] = node
        return node << 1

    def or_(self, a: Lit, b: Lit) -> Lit:
        return negate(self.and_(negate(a), negate(b)))

    def _atoms(self, lit: Lit) -> tuple[frozenset[Lit], int]:
        """Decompose a literal into (set of atoms, parity), so that the literal
        equals the XOR of the atoms, inverted when parity is 1."""
        if lit == CONST_FALSE:
            return frozenset(), 0
        if lit == CONST_TRUE:
            return frozenset(), 1
        known = self._xor_atoms.get(lit)
        if known is not None:
            return known
        # A non-XOR literal is its own atom; strip the inversion into parity so
        # that x and ~x share an atom and can cancel.
        return frozenset({lit & ~1}), lit & 1

    def _materialise_xor(self, atoms: frozenset[Lit], parity: int) -> Lit:
        if not atoms:
            return CONST_TRUE if parity else CONST_FALSE
        if len(atoms) == 1:
            (only,) = atoms
            return negate(only) if parity else only
        if len(atoms) > MAX_XOR_ATOMS:
            # Too wide to track. Build it, but do not record the decomposition:
            # the result becomes an opaque atom. Correctness is unaffected; only
            # the amount of free structural cancellation is.
            acc = CONST_FALSE
            for atom in sorted(atoms):
                acc = self._raw_xor(acc, atom)
            return negate(acc) if parity else acc
        cached = self._xor_cache.get((atoms, 0))
        if cached is None:
            acc = CONST_FALSE
            for atom in sorted(atoms):
                acc = self._raw_xor(acc, atom)
            self._xor_cache[(atoms, 0)] = acc
            self._xor_atoms[acc] = (atoms, 0)
            self._xor_atoms[negate(acc)] = (atoms, 1)
            cached = acc
        return negate(cached) if parity else cached

    def _raw_xor(self, a: Lit, b: Lit) -> Lit:
        """XOR built from two ANDs, with no atom bookkeeping."""
        if a == CONST_FALSE:
            return b
        if b == CONST_FALSE:
            return a
        if a == CONST_TRUE:
            return negate(b)
        if b == CONST_TRUE:
            return negate(a)
        if a == b:
            return CONST_FALSE
        if a == negate(b):
            return CONST_TRUE
        return self.or_(self.and_(a, negate(b)), self.and_(negate(a), b))

    def xor(self, a: Lit, b: Lit) -> Lit:
        """XOR, canonicalised over its atoms.  The workhorse of this project.

        Because operands are reduced to a parity of atoms, repeated terms cancel
        structurally: ``xor(xor(x, y), x)`` returns exactly the literal for
        ``y``. That is what makes a correct compute/uncompute pair collapse to
        the constant false without any search.
        """
        sa, pa = self._atoms(a)
        sb, pb = self._atoms(b)
        return self._materialise_xor(sa ^ sb, pa ^ pb)

    def mux(self, sel: Lit, if_true: Lit, if_false: Lit) -> Lit:
        return self.or_(self.and_(sel, if_true), self.and_(negate(sel), if_false))

    def majority(self, a: Lit, b: Lit, c: Lit) -> Lit:
        return self.or_(self.or_(self.and_(a, b), self.and_(a, c)), self.and_(b, c))

    # -- inspection --------------------------------------------------------

    @property
    def num_ands(self) -> int:
        return len(self.ands)

    @property
    def size(self) -> int:
        """AND-node count: the standard size measure, and a proxy for
        multiplicative complexity (see :mod:`qsha256.formal.bounds`)."""
        return len(self.ands)

    def cone(self, roots: Iterable[Lit]) -> set[int]:
        """Nodes in the transitive fanin of ``roots`` -- the part that matters."""
        seen: set[int] = set()
        stack = [node_of(r) for r in roots if node_of(r) > 0]
        while stack:
            node = stack.pop()
            if node in seen or node not in self.ands:
                continue
            seen.add(node)
            left, right = self.ands[node]
            stack.append(node_of(left))
            stack.append(node_of(right))
        return seen

    def evaluate(self, roots: Sequence[Lit], assignment: Sequence[int]) -> list[int]:
        """Evaluate the graph on a concrete input assignment.

        Used to cross-check symbolic execution against the fast basis simulator,
        and to decode SAT counterexamples into readable inputs.
        """
        if len(assignment) != self.num_inputs:
            raise ValueError(f"expected {self.num_inputs} inputs, got {len(assignment)}")
        value = {0: 0}
        for i, bit in enumerate(assignment):
            value[i + 1] = bit & 1
        for node in sorted(self.ands):
            left, right = self.ands[node]
            lv = value[node_of(left)] ^ (left & 1)
            rv = value[node_of(right)] ^ (right & 1)
            value[node] = lv & rv
        return [value[node_of(r)] ^ (r & 1) for r in roots]

    # -- export ------------------------------------------------------------

    def to_aiger(self, outputs: Sequence[Lit], comment: str = "") -> str:
        """Serialise to ASCII AIGER (``aag``) so external tools can re-check us.

        ABC will read this directly::

            abc -c "read circuit.aag; read spec.aag; cec"
        """
        lines = [f"aag {self._next_node - 1} {self.num_inputs} 0 {len(outputs)} {len(self.ands)}"]
        for i in range(self.num_inputs):
            lines.append(str((i + 1) << 1))
        for out in outputs:
            lines.append(str(out))
        for node in sorted(self.ands):
            left, right = self.ands[node]
            lines.append(f"{node << 1} {left} {right}")
        for i, name in enumerate(self.input_names):
            lines.append(f"i{i} {name}")
        if comment:
            lines.append("c")
            lines.append(comment)
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Symbolic execution of a Qiskit circuit
# --------------------------------------------------------------------------

#: Gates that act as a permutation of the computational basis and can therefore
#: be symbolically executed.  ``and_g``/``and_g_dg`` behave as Toffolis on the
#: basis; their preconditions are checked separately.
_X_TYPE_CONTROLS = {"x": 0, "cx": 1, "ccx": 2, "and_g": 2, "and_g_dg": 2}


@dataclass
class SymbolicState:
    """Result of symbolically executing a circuit."""

    aig: AIG
    #: qubit -> Boolean function of the inputs, after the circuit
    values: dict[Qubit, Lit]
    #: qubit -> its input literal (or constant), before the circuit
    inputs: dict[Qubit, Lit]
    #: literals of the ``and_g`` preconditions that must be provably false
    and_preconditions: list[tuple[str, Lit]] = field(default_factory=list)

    def value(self, qubit: Qubit) -> Lit:
        return self.values[qubit]

    def word_value(self, word) -> list[Lit]:
        """Literals of a :class:`~qsha256.quantum.registers.Word`, LSB first."""
        return [CONST_FALSE if q is None else self.values[q] for q in word]


def symbolic_execute(
    circuit: QuantumCircuit,
    free_qubits: Sequence[Qubit] | None = None,
    zero_qubits: Sequence[Qubit] | None = None,
    aig: AIG | None = None,
    check_and_preconditions: bool = True,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> SymbolicState:
    """Propagate Boolean functions through a permutation circuit.

    Parameters
    ----------
    free_qubits:
        Qubits treated as free Boolean inputs.  Everything else is taken to
        start in ``|0>``, which is exactly the ancilla convention used
        throughout the project, and which lets the constant folding in
        :meth:`AIG.and_` prune enormous amounts of the graph.
    zero_qubits:
        Explicitly constant-zero qubits.  Defaults to "everything not free".
    check_and_preconditions:
        Collect, for every Gidney ``and_g``, the function that must be
        identically false for its precondition to hold (namely the target's
        value at that point), and likewise for ``and_g_dg``.  These are
        discharged as separate SAT queries by
        :func:`qsha256.formal.equivalence.prove_and_preconditions`.
    """
    aig = aig if aig is not None else AIG(max_nodes=max_nodes)
    free = list(free_qubits) if free_qubits is not None else list(circuit.qubits)
    free_set = set(free)
    explicit_zero = set(zero_qubits) if zero_qubits is not None else None

    values: dict[Qubit, Lit] = {}
    inputs: dict[Qubit, Lit] = {}
    for qubit in circuit.qubits:
        if qubit in free_set:
            lit = aig.new_input(_qubit_name(circuit, qubit))
        elif explicit_zero is None or qubit in explicit_zero:
            lit = CONST_FALSE
        else:
            lit = aig.new_input(_qubit_name(circuit, qubit))
        values[qubit] = lit
        inputs[qubit] = lit

    preconditions: list[tuple[str, Lit]] = []

    for inst in circuit.data:
        name = inst.operation.name
        if name in ("barrier", "id"):
            continue
        if name == "swap":
            a, b = inst.qubits
            values[a], values[b] = values[b], values[a]
            continue
        if name in ("z", "cz", "ccz"):
            # Diagonal: no effect on basis-state values. Phase is tracked
            # separately by the oracle checks, not here.
            continue
        if name not in _X_TYPE_CONTROLS:
            raise UnsupportedForSymbolicExecution(
                f"gate {name!r} does not act classically on the computational "
                "basis and cannot be symbolically executed"
            )

        n_controls = _X_TYPE_CONTROLS[name]
        controls = list(inst.qubits[:n_controls])
        target = inst.qubits[n_controls]

        condition = CONST_TRUE
        for control in controls:
            condition = aig.and_(condition, values[control])

        if check_and_preconditions and name == "and_g":
            # and_g is only a Toffoli when its target is |0>: record the
            # target's current value, which must be provably false.
            preconditions.append(("and_g target must be |0>", values[target]))
        elif check_and_preconditions and name == "and_g_dg":
            # and_g_dg only clears the target when it holds exactly x AND y:
            # record the XOR, which must be provably false.
            preconditions.append(
                ("and_g_dg target must equal x AND y", aig.xor(values[target], condition))
            )

        values[target] = aig.xor(values[target], condition)

    return SymbolicState(aig=aig, values=values, inputs=inputs, and_preconditions=preconditions)


def _qubit_name(circuit: QuantumCircuit, qubit: Qubit) -> str:
    try:
        location = circuit.find_bit(qubit)
        register, index = location.registers[0]
        return f"{register.name}[{index}]"
    except Exception:
        return f"q{circuit.find_bit(qubit).index}"
