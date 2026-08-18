"""Reversible primitives, verified exhaustively wherever the input space allows."""

from __future__ import annotations

import itertools

import pytest
from conftest import assert_ancillas_clean, run_circuit
from qiskit.quantum_info import Statevector

from qsha256.classical.sha256 import ch, maj, rotr, shr
from qsha256.quantum.primitives.add import ADDERS, add_const_into, add_into, get_adder
from qsha256.quantum.primitives.boolean import (
    and_tree_ancilla_count,
    and_tree_mcx,
    ch_word_into,
    maj_word_into,
)
from qsha256.quantum.primitives.csa import csa_layer, sum_addends
from qsha256.quantum.primitives.rotate import rotate_gate_cost, rotate_in_place_cost
from qsha256.quantum.primitives.shift import (
    in_place_shift_is_reversible,
    shift_cnot_cost,
    shift_gate_cost,
)
from qsha256.quantum.primitives.xor import xor_const, xor_terms, xor_word
from qsha256.quantum.registers import CircuitBuilder
from qsha256.validation.basis_sim import BasisSimulator

BASIS_ADDERS = [name for name, a in ADDERS.items() if a.basis_simulable]


class TestWordViews:
    """Rotation and shift are wiring. This is where that claim is pinned down."""

    def test_rotr_is_pure_relabelling(self, builder):
        word = builder.add_word(8, "x")
        before = len(builder.circuit.data)
        rotated = word.rotr(3)
        assert len(builder.circuit.data) == before, "rotation emitted gates"
        assert rotated.bits == word.bits[3:] + word.bits[:3]

    def test_shr_binds_vacated_positions_to_zero(self, builder):
        word = builder.add_word(8, "x")
        shifted = word.shr(3)
        assert shifted.bits[-3:] == (None, None, None)
        assert shifted.bits[:5] == word.bits[3:]

    def test_rotation_and_shift_are_free(self):
        assert rotate_gate_cost() == 0
        assert shift_gate_cost() == 0

    def test_shift_is_cheaper_than_rotation_when_xored(self):
        """SHR^n contributes n fewer CNOTs, because n source bits are constant zero."""
        assert shift_cnot_cost(32, 10) == 22

    def test_in_place_shift_is_documented_as_irreversible(self):
        assert in_place_shift_is_reversible() is False

    def test_physical_rotation_cost_is_reported_for_contrast(self):
        assert rotate_in_place_cost(32, 0) == 0
        assert rotate_in_place_cost(32, 1) == 31
        assert rotate_in_place_cost(32, 16) == 16  # gcd(32,16)=16

    def test_cannot_xor_into_a_constant_zero_position(self, builder):
        src = builder.add_word(4, "s")
        with pytest.raises(ValueError, match="constant-zero"):
            xor_word(builder, src, src.shr(1))


class TestXor:
    def test_xor_word_is_self_inverse(self, builder, rng):
        a, b = builder.add_word(6, "a"), builder.add_word(6, "b")
        xor_word(builder, a, b)
        xor_word(builder, a, b)
        for _ in range(20):
            av, bv = rng.getrandbits(6), rng.getrandbits(6)
            sim, out = run_circuit(builder, {a: av, b: bv})
            assert (sim.read(out, a), sim.read(out, b)) == (av, bv)

    def test_xor_const_uses_one_x_per_set_bit(self, builder):
        word = builder.add_word(8, "w")
        xor_const(builder, 0b10110001, word)
        assert builder.circuit.count_ops().get("x", 0) == 4

    @pytest.mark.parametrize(
        "terms,ref",
        [
            (
                (("rotr", 2), ("rotr", 13), ("rotr", 22)),
                lambda x: rotr(x, 2, 32) ^ rotr(x, 13, 32) ^ rotr(x, 22, 32),
            ),
            (
                (("rotr", 7), ("rotr", 18), ("shr", 3)),
                lambda x: rotr(x, 7, 32) ^ rotr(x, 18, 32) ^ shr(x, 3, 32),
            ),
        ],
    )
    def test_xor_terms_matches_classical(self, builder, rng, terms, ref):
        x, t = builder.add_word(32, "x"), builder.add_word(32, "t")
        xor_terms(builder, x, terms, t)
        for value in [0, 1, 0xFFFFFFFF] + [rng.getrandbits(32) for _ in range(20)]:
            sim, out = run_circuit(builder, {x: value})
            assert sim.read(out, t) == ref(value)
            assert sim.read(out, x) == value, "source register was mutated"

    def test_sigma_uses_no_toffoli_and_no_ancilla(self, builder):
        x, t = builder.add_word(32, "x"), builder.add_word(32, "t")
        xor_terms(builder, x, (("rotr", 2), ("rotr", 13), ("rotr", 22)), t)
        ops = builder.circuit.count_ops()
        assert ops.get("ccx", 0) == 0
        assert builder.ancilla_qubits == 0
        assert ops["cx"] == 96


class TestBoolean:
    @pytest.mark.parametrize("fn,ref,name", [(ch_word_into, ch, "Ch"), (maj_word_into, maj, "Maj")])
    def test_exhaustive_over_three_bit_words(self, builder, fn, ref, name):
        width = 3
        x, y, z, t = (builder.add_word(width, c) for c in "xyzt")
        fn(builder, x, y, z, t)
        mask = (1 << width) - 1
        for xv, yv, zv, tv in itertools.product(range(1 << width), repeat=4):
            sim, out = run_circuit(builder, {x: xv, y: yv, z: zv, t: tv})
            assert sim.read(out, t) == tv ^ (ref(xv, yv, zv) & mask)
            assert (sim.read(out, x), sim.read(out, y), sim.read(out, z)) == (xv, yv, zv)

    @pytest.mark.parametrize("fn,toffoli,cnot", [(ch_word_into, 1, 3), (maj_word_into, 1, 5)])
    def test_one_toffoli_per_bit_and_no_ancilla(self, builder, fn, toffoli, cnot):
        """The whole point of the algebraic rewrites in boolean.py."""
        words = [builder.add_word(8, c) for c in "xyzt"]
        fn(builder, *words)
        ops = builder.circuit.count_ops()
        assert ops["ccx"] == 8 * toffoli
        assert ops["cx"] == 8 * cnot
        assert builder.ancilla_qubits == 0

    @pytest.mark.parametrize("fn", [ch_word_into, maj_word_into])
    def test_applying_twice_uncomputes(self, builder, rng, fn):
        words = [builder.add_word(5, c) for c in "xyzt"]
        fn(builder, *words)
        fn(builder, *words)
        for _ in range(20):
            values = {w: rng.getrandbits(5) for w in words}
            sim, out = run_circuit(builder, values)
            assert all(sim.read(out, w) == v for w, v in values.items())

    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8])
    def test_and_tree_computes_the_conjunction(self, builder, n):
        controls = builder.add_word(n, "c")
        target = builder.add_word(1, "t")
        anc = builder.add_word(max(1, and_tree_ancilla_count(n)), "a")
        and_tree_mcx(builder, controls.qubits, target[0], anc.qubits)
        for value in range(1 << n):
            sim, out = run_circuit(builder, {controls: value})
            assert sim.read(out, target) == int(value == (1 << n) - 1)
            assert sim.read(out, anc) == 0, "AND tree left ancillas dirty"

    def test_and_tree_rejects_insufficient_ancilla(self, builder):
        controls = builder.add_word(6, "c")
        target = builder.add_word(1, "t")
        with pytest.raises(ValueError, match="needs 4 ancillas"):
            and_tree_mcx(builder, controls.qubits, target[0], [])


class TestAdders:
    @pytest.mark.parametrize("name", BASIS_ADDERS)
    @pytest.mark.parametrize("width", [2, 3, 4, 5])
    def test_exhaustive_modular_addition(self, name, width):
        b = CircuitBuilder(f"{name}{width}")
        a, t = b.add_word(width, "a"), b.add_word(width, "b")
        add_into(b, a, t, name)
        for x, y in itertools.product(range(1 << width), repeat=2):
            sim, out = run_circuit(b, {a: x, t: y})
            assert sim.read(out, t) == (x + y) % (1 << width)
            assert sim.read(out, a) == x, "addend was mutated"
            assert_ancillas_clean(b, sim, out)

    @pytest.mark.parametrize("name,ccx,cx", [("cdkm", 2, 4), ("vbe", 4, 4)])
    def test_published_gate_counts(self, name, ccx, cx):
        """CDKM: 2(n-1) Toffoli / 4n-2 CNOT. VBE: 4(n-1) Toffoli / 4n-2 CNOT.

        The CDKM figure is 2(n-1) and not 2n because the top MAJ/UMA pair
        cancels once the carry out is discarded, which is what makes this
        addition modular. Amy et al. 2016's circuit reaches the same 62 for
        n=32, and comparing against it is how the discrepancy was found.
        """
        n = 32
        b = CircuitBuilder("count")
        a, t = b.add_word(n, "a"), b.add_word(n, "b")
        add_into(b, a, t, name)
        ops = b.circuit.count_ops()
        expected_ccx = 4 * (n - 1) if name == "vbe" else 2 * (n - 1)
        expected_cx = 4 * n - 2
        assert ops["ccx"] == expected_ccx
        assert ops["cx"] == expected_cx

    @pytest.mark.parametrize("name,ancillas", [("cdkm", 1), ("vbe", 32), ("qft", 0)])
    def test_ancilla_counts(self, name, ancillas):
        assert get_adder(name).ancilla_count(32) == ancillas

    def test_adder_rejects_width_mismatch(self, builder):
        a, t = builder.add_word(4, "a"), builder.add_word(5, "b")
        with pytest.raises(ValueError, match="width mismatch"):
            add_into(builder, a, t)

    def test_unknown_adder_is_rejected(self):
        with pytest.raises(KeyError, match="unknown adder"):
            get_adder("nope")

    @pytest.mark.parametrize("strategy", ["load", "vbe_const"])
    @pytest.mark.parametrize("width", [2, 3, 4])
    def test_constant_addition_exhaustive(self, strategy, width):
        for k in range(1 << width):
            b = CircuitBuilder("k")
            t = b.add_word(width, "b")
            add_const_into(b, k, t, "cdkm", strategy)
            for y in range(1 << width):
                sim, out = run_circuit(b, {t: y})
                assert sim.read(out, t) == (y + k) % (1 << width)
                assert_ancillas_clean(b, sim, out)

    def test_unknown_const_strategy_is_rejected(self, builder):
        t = builder.add_word(4, "b")
        with pytest.raises(KeyError, match="unknown constant-add strategy"):
            add_const_into(builder, 1, t, "cdkm", "nope")

    @pytest.mark.parametrize("width", [2, 3, 4])
    def test_qft_adder_via_statevector(self, width):
        """The QFT adder leaves the computational basis, so it needs a statevector."""
        from qiskit.quantum_info import Statevector

        for x, y in itertools.product(range(1 << width), repeat=2):
            b = CircuitBuilder("qft")
            a, t = b.add_word(width, "a"), b.add_word(width, "b")
            for i in range(width):
                if (x >> i) & 1:
                    b.x(a[i])
                if (y >> i) & 1:
                    b.x(t[i])
            add_into(b, a, t, "qft")
            probabilities = Statevector.from_instruction(b.circuit).probabilities_dict()
            top = max(probabilities, key=probabilities.get)
            bits = top[::-1]
            assert probabilities[top] > 0.99
            assert sum(int(bits[width + i]) << i for i in range(width)) == (x + y) % (1 << width)

    def test_qft_adder_is_flagged_as_not_basis_simulable(self):
        assert get_adder("qft").basis_simulable is False
        assert get_adder("qft").native_clifford_t is False


class TestCarrySave:
    @pytest.mark.parametrize("width", [3, 4])
    def test_csa_layer_preserves_the_sum(self, width):
        """S + C == x + y + z, which is the whole point of carry-save."""
        for x, y, z in itertools.product(range(1 << width), repeat=3):
            b = CircuitBuilder("csa")
            wx, wy, wz = (b.add_word(width, c) for c in "xyz")
            s, c = b.add_word(width, "s"), b.add_word(width, "c")
            csa_layer(b, wx, wy, wz, s, c)
            sim, out = run_circuit(b, {wx: x, wy: y, wz: z})
            total = (sim.read(out, s) + sim.read(out, c)) % (1 << width)
            assert total == (x + y + z) % (1 << width)
            assert (sim.read(out, wx), sim.read(out, wy), sim.read(out, wz)) == (x, y, z)

    @pytest.mark.parametrize("n_addends", [2, 3, 4, 5])
    def test_sum_addends_and_uncomputes(self, rng, n_addends):
        width = 4
        b = CircuitBuilder("sum")
        words = [b.add_word(width, f"a{i}") for i in range(n_addends)]
        result = b.add_word(width, "r")
        with sum_addends(b, words, "cdkm") as total:
            xor_word(b, total, result)
        for _ in range(25):
            values = {w: rng.getrandbits(width) for w in words}
            sim, out = run_circuit(b, values)
            assert sim.read(out, result) == sum(values.values()) % (1 << width)
            assert all(sim.read(out, w) == v for w, v in values.items())
            assert_ancillas_clean(b, sim, out)

    def test_sum_addends_rejects_empty(self, builder):
        with pytest.raises(ValueError, match="at least one addend"):
            with sum_addends(builder, [], "cdkm"):
                pass


class TestTemporaryAnd:
    """Gidney's measurement-based AND -- the project's biggest T-count lever."""

    def test_compute_circuit_is_exactly_the_and(self):
        """4 T gates, and no relative *or* global phase."""
        import numpy as np
        from qiskit import QuantumCircuit
        from qiskit.quantum_info import Operator

        from qsha256.quantum.primitives.temporary_and import (
            GIDNEY_AND_T_COUNT,
            gidney_and_circuit,
        )

        circuit = gidney_and_circuit()
        ops = circuit.count_ops()
        assert ops.get("t", 0) + ops.get("tdg", 0) == GIDNEY_AND_T_COUNT == 4

        # Only defined when the target starts |0>, so compare on that subspace.
        for x in (0, 1):
            for y in (0, 1):
                prep = QuantumCircuit(3)
                if x:
                    prep.x(0)
                if y:
                    prep.x(1)
                state = Statevector.from_instruction(prep.compose(circuit))
                index = x + 2 * y + 4 * (x & y)
                assert np.isclose(state.data[index], 1.0, atol=1e-9), (
                    f"AND({x},{y}) wrong or carries a phase"
                )
        assert Operator(circuit) is not None

    def test_uncompute_uses_no_t_gates(self):
        from qsha256.quantum.primitives.temporary_and import gidney_uncompute_circuit

        ops = gidney_uncompute_circuit().count_ops()
        assert ops.get("t", 0) + ops.get("tdg", 0) == 0
        assert ops.get("measure", 0) == 1

    def test_measurement_based_uncompute_clears_the_target(self):
        """Check both measurement branches by projecting, as Statevector cannot measure."""
        import numpy as np
        from qiskit import QuantumCircuit
        from qiskit.quantum_info import Statevector

        from qsha256.quantum.primitives.temporary_and import gidney_and_circuit

        for x in (0, 1):
            for y in (0, 1):
                prep = QuantumCircuit(3)
                if x:
                    prep.x(0)
                if y:
                    prep.x(1)
                after_and = Statevector.from_instruction(prep.compose(gidney_and_circuit()))
                # H on the target, then both outcomes are equally likely.
                h = QuantumCircuit(3)
                h.h(2)
                state = after_and.evolve(h)
                for outcome in (0, 1):
                    amps = [state.data[i] for i in range(8) if ((i >> 2) & 1) == outcome]
                    norm = float(np.sqrt(sum(abs(a) ** 2 for a in amps)))
                    assert np.isclose(norm**2, 0.5, atol=1e-9), "outcome not equiprobable"
                    # The surviving amplitude carries (-1)^(outcome * (x AND y)),
                    # which CZ(x, y) cancels -- Clifford, no T gates.
                    sign = (-1) ** (outcome * (x & y))
                    live = [a for a in amps if abs(a) > 1e-9]
                    assert len(live) == 1
                    assert np.isclose(live[0] * sign, abs(live[0]), atol=1e-9)

    def test_and_gate_inverses_are_each_other(self):
        from qsha256.quantum.primitives.temporary_and import AndDgGate, AndGate

        assert AndGate().inverse().name == "and_g_dg"
        assert AndDgGate().inverse().name == "and_g"

    def test_strict_simulation_catches_a_dirty_target(self, builder):
        from qsha256.validation.basis_sim import PreconditionViolated

        w = builder.add_word(3, "q")
        builder.and_g(w[0], w[1], w[2])
        sim = BasisSimulator(builder.circuit, strict=True)
        with pytest.raises(PreconditionViolated, match="target to be"):
            sim.run([1, 1, 1])

    def test_strict_simulation_catches_a_bad_uncompute(self, builder):
        from qsha256.validation.basis_sim import PreconditionViolated

        w = builder.add_word(3, "q")
        builder.and_g_dg(w[0], w[1], w[2])
        sim = BasisSimulator(builder.circuit, strict=True)
        with pytest.raises(PreconditionViolated, match="x AND y"):
            sim.run([1, 1, 0])

    def test_reverse_replay_swaps_and_for_and_dagger(self, builder):
        """Reversing an and_g must emit and_g_dg, not another and_g."""
        w = builder.add_word(3, "q")
        start = len(builder.circuit.data)
        builder.and_g(w[0], w[1], w[2])
        builder.append_reversed(start, len(builder.circuit.data))
        names = [i.operation.name for i in builder.circuit.data]
        assert names == ["and_g", "and_g_dg"]
        sim = BasisSimulator(builder.circuit, strict=True)
        for x in (0, 1):
            for y in (0, 1):
                assert sim.run([x, y, 0])[0] == [x, y, 0]

    @pytest.mark.parametrize("width", [2, 3, 4, 5])
    def test_gidney_adder_exhaustive(self, width):
        b = CircuitBuilder(f"gidney{width}")
        a, t = b.add_word(width, "a"), b.add_word(width, "b")
        add_into(b, a, t, "gidney")
        sim = BasisSimulator(b.circuit, strict=True)  # checks every AND precondition
        for x, y in itertools.product(range(1 << width), repeat=2):
            out, _ = sim.run(sim.load({a: x, t: y}))
            assert sim.read(out, t) == (x + y) % (1 << width)
            assert sim.read(out, a) == x
            assert_ancillas_clean(b, sim, out)

    def test_gidney_uses_and_pairs_not_toffolis(self):
        b = CircuitBuilder("g32")
        a, t = b.add_word(32, "a"), b.add_word(32, "b")
        add_into(b, a, t, "gidney")
        ops = b.circuit.count_ops()
        assert ops.get("ccx", 0) == 0, "should contain no Toffolis at all"
        assert ops["and_g"] == ops["and_g_dg"] == 31, "n-1 compute/uncompute pairs"
        assert b.ancilla_qubits == 31

    def test_gidney_beats_cdkm_on_t_count(self):
        """The headline claim: 124 T vs 448 for a 32-bit addition."""
        from qsha256.quantum.resources.clifford_t import clifford_t_cost

        counts = {}
        for name in ("cdkm", "gidney"):
            b = CircuitBuilder(name)
            a, t = b.add_word(32, "a"), b.add_word(32, "b")
            add_into(b, a, t, name)
            counts[name] = clifford_t_cost(dict(b.circuit.count_ops()))["t_count"]
        assert counts["cdkm"] == 434  # 7 adders x 2(n-1), the Cuccaro cost
        assert counts["gidney"] == 124
        assert counts["gidney"] < counts["cdkm"] / 3

    def test_gidney_requires_feedforward_and_says_so(self):
        from qsha256.quantum.primitives.add import get_adder
        from qsha256.quantum.resources.clifford_t import clifford_t_cost

        assert get_adder("gidney").needs_measurement is True
        b = CircuitBuilder("g")
        a, t = b.add_word(8, "a"), b.add_word(8, "b")
        add_into(b, a, t, "gidney")
        cost = clifford_t_cost(dict(b.circuit.count_ops()))
        assert cost["needs_feedforward"] is True
        assert cost["measurements"] == 7
        assert cost["transpiler_can_reproduce"] is False
