"""Formal verification: symbolic execution, SAT proofs, pebbling, bounds."""

from __future__ import annotations

import pytest

from qsha256 import SHA256, TOY4
from qsha256.formal import spec_aig as S
from qsha256.formal.aig import (
    AIG,
    CONST_FALSE,
    CONST_TRUE,
    AIGTooLarge,
    UnsupportedForSymbolicExecution,
    symbolic_execute,
)
from qsha256.formal.bounds import (
    circuit_bound_report,
    component_bounds,
    is_affine,
    multiplicative_complexity,
    truth_table,
)
from qsha256.formal.compositional import (
    prove_chaining,
    prove_copy_in,
    prove_schedule_step,
    prove_structure,
)
from qsha256.formal.equivalence import prove_ancillas_clean, prove_equivalent
from qsha256.formal.pebbling import minimise_pebbles, schedule_dag, solve_pebbling
from qsha256.formal.sha256_proofs import prove_adder, prove_boolean, prove_round, prove_sigma
from qsha256.formal.superopt import permutation_of, synthesise_optimal
from qsha256.quantum.primitives.add import add_into
from qsha256.quantum.registers import CircuitBuilder
from qsha256.quantum.strategies import Strategy


class TestAIG:
    def test_constant_folding(self):
        aig = AIG()
        x = aig.new_input("x")
        assert aig.and_(x, CONST_FALSE) == CONST_FALSE
        assert aig.and_(x, CONST_TRUE) == x
        assert aig.and_(x, x) == x
        assert aig.and_(x, x ^ 1) == CONST_FALSE

    def test_structural_hashing_shares_nodes(self):
        aig = AIG()
        x, y = aig.new_input(), aig.new_input()
        assert aig.and_(x, y) == aig.and_(y, x)
        before = aig.num_ands
        aig.and_(x, y)
        assert aig.num_ands == before

    def test_xor_cancels_structurally(self):
        """The property that makes compute/uncompute pairs free to verify."""
        aig = AIG()
        x, y, z = (aig.new_input() for _ in range(3))
        assert aig.xor(aig.xor(x, y), x) == y
        assert aig.xor(aig.xor(x, y), aig.xor(x, y)) == CONST_FALSE
        assert aig.xor(aig.xor(aig.xor(x, y), z), aig.xor(y, z)) == x

    def test_evaluate_matches_semantics(self):
        import itertools

        aig = AIG()
        x, y, z = (aig.new_input() for _ in range(3))
        f = aig.xor(aig.and_(x, y), z)
        g = aig.majority(x, y, z)
        for bits in itertools.product((0, 1), repeat=3):
            got = aig.evaluate([f, g], list(bits))
            assert got[0] == ((bits[0] & bits[1]) ^ bits[2])
            assert got[1] == (sum(bits) >= 2)

    def test_node_budget_is_enforced(self):
        aig = AIG(max_nodes=50)
        acc = aig.new_input()
        with pytest.raises(AIGTooLarge):
            for _ in range(200):
                acc = aig.and_(acc, aig.new_input())

    def test_rejects_non_classical_gates(self):
        from qiskit import QuantumCircuit

        qc = QuantumCircuit(1)
        qc.h(0)
        with pytest.raises(UnsupportedForSymbolicExecution):
            symbolic_execute(qc)

    def test_aiger_export_is_wellformed(self):
        aig = AIG()
        x, y = aig.new_input("x"), aig.new_input("y")
        text = aig.to_aiger([aig.and_(x, y)])
        assert text.startswith("aag ")
        header = text.splitlines()[0].split()
        assert int(header[2]) == 2  # inputs
        assert int(header[4]) == aig.num_ands


class TestSATProofs:
    @pytest.mark.parametrize("adder", ["cdkm", "vbe", "gidney"])
    @pytest.mark.parametrize("width", [4, 8, 32])
    def test_adders_proved_correct(self, adder, width):
        """Proof over ALL inputs, not a sample."""
        proof = prove_adder(adder, width)
        assert proof.proved, [str(p) for p in proof.proofs if not p.proved]

    @pytest.mark.parametrize("which", ["ch", "maj"])
    def test_boolean_proved(self, which):
        assert prove_boolean(which, 32).proved

    @pytest.mark.parametrize("which", ["big_sigma0", "big_sigma1", "small_sigma0", "small_sigma1"])
    def test_sigmas_proved(self, which):
        assert prove_sigma(which, SHA256).proved

    @pytest.mark.slow
    @pytest.mark.parametrize("layout", ["serial", "wide"])
    def test_round_proved_at_full_width(self, layout):
        assert prove_round(SHA256, Strategy(round_layout=layout)).proved

    @pytest.mark.slow
    def test_gidney_round_proved(self):
        assert prove_round(SHA256, Strategy(adder="gidney")).proved

    def test_a_broken_circuit_is_refuted_with_a_counterexample(self):
        """The proof must fail on a circuit that is actually wrong."""
        b = CircuitBuilder("broken")
        a, t = b.add_word(8, "a"), b.add_word(8, "b")
        add_into(b, a, t, "cdkm")
        b.x(t[0])  # sabotage one bit
        state = symbolic_execute(b.circuit, free_qubits=a.qubits + t.qubits)
        spec = S.add_mod(
            state.aig,
            [state.inputs[q] for q in a.qubits],
            [state.inputs[q] for q in t.qubits],
        )
        proof = prove_equivalent(
            state.aig, [state.values[q] for q in t.qubits], spec, name="sabotaged"
        )
        assert not proof.proved
        assert proof.counterexample is not None

    def test_a_leaked_ancilla_is_detected(self):
        from qsha256.quantum.primitives.boolean import ch_word_into

        b = CircuitBuilder("leak")
        x, y, z = (b.add_word(4, c) for c in "xyz")
        tmp = b.ancillas.acquire(4, "tmp")
        ch_word_into(b, x, y, z, tmp)  # computed, never uncomputed
        state = symbolic_execute(b.circuit, free_qubits=x.qubits + y.qubits + z.qubits)
        proof = prove_ancillas_clean(state.aig, [state.values[q] for q in b.ancillas.all])
        assert not proof.proved

    def test_structural_proof_needs_no_solver(self):
        """XOR-awareness should settle a compute/uncompute pair for free."""
        from qsha256.quantum.primitives.boolean import ch_word_into

        b = CircuitBuilder("pair")
        x, y, z = (b.add_word(8, c) for c in "xyz")
        with b.ancillas.borrow(8, "tmp") as tmp:
            ch_word_into(b, x, y, z, tmp)
            ch_word_into(b, x, y, z, tmp)
        # XOR-awareness is what makes this free; the equivalence prover leaves
        # it off because it enlarges the graph. See AIG.xor_aware.
        state = symbolic_execute(
            b.circuit, free_qubits=x.qubits + y.qubits + z.qubits, xor_aware=True
        )
        proof = prove_ancillas_clean(state.aig, [state.values[q] for q in b.ancillas.all])
        assert proof.proved
        assert "folded" in proof.detail


class TestCompositional:
    def test_copy_in_and_chaining(self):
        assert prove_copy_in(SHA256).proved
        assert prove_chaining(SHA256).proved

    @pytest.mark.slow
    @pytest.mark.parametrize("schedule", ["rolling", "store_all"])
    def test_schedule_step(self, schedule):
        assert prove_schedule_step(SHA256, Strategy(schedule=schedule), t=16).proved

    @pytest.mark.slow
    def test_structure_covers_every_gate(self):
        proof = prove_structure(SHA256, Strategy(), 64)
        assert proof.proved, proof.detail
        assert "fully covered" in proof.detail


class TestPebbling:
    def test_dag_shape(self):
        dag = schedule_dag(SHA256)
        assert len(dag) == 64
        assert len(dag.inputs) == 16
        assert len(dag.targets) == 48
        assert dag.preds[16] == sorted({0, 1, 9, 14})

    @pytest.mark.slow
    def test_rolling_register_count_is_optimal(self):
        """16 registers suffice and 15 provably do not."""
        dag = schedule_dag(SHA256)
        assert solve_pebbling(dag, 16, steps=48, timeout=90).found
        assert solve_pebbling(dag, 15, steps=48, timeout=90).proved_impossible

    def test_toy_optimum_matches_the_implementation(self):
        dag = schedule_dag(TOY4)
        best, _ = minimise_pebbles(dag, steps=16, timeout=30)
        assert best == TOY4.block_words

    def test_inplace_move_is_what_makes_it_match(self):
        """Without the in-place move the model contradicts the real circuit."""
        dag = schedule_dag(TOY4)
        with_inplace, _ = minimise_pebbles(dag, steps=16, timeout=30, allow_inplace=True)
        without, _ = minimise_pebbles(dag, steps=16, timeout=30, allow_inplace=False)
        assert with_inplace < without

    def test_timeout_is_not_reported_as_a_proof(self):
        dag = schedule_dag(SHA256)
        result = solve_pebbling(dag, 20, steps=200, timeout=0.001)
        assert result.status in ("UNKNOWN", "STRATEGY", "IMPOSSIBLE")
        if result.status == "UNKNOWN":
            assert not result.found and not result.proved_impossible


class TestBounds:
    def test_affine_detection(self):
        assert is_affine(truth_table(lambda x, y: x ^ y, 2), 2)
        assert not is_affine(truth_table(lambda x, y: x & y, 2), 2)

    def test_ch_and_maj_need_exactly_one_and(self):
        from qsha256.classical.sha256 import ch, maj

        for fn in (ch, maj):
            table = truth_table(lambda x, y, z, _f=fn: _f(x, y, z) & 1, 3)
            result = multiplicative_complexity(table, 3)
            assert result.value == 1 and result.exact

    def test_a_degree_three_function_needs_two(self):
        table = truth_table(lambda x, y, z: x & y & z, 3)
        result = multiplicative_complexity(table, 3)
        assert result.value == 2 and result.exact

    def test_gidney_adder_and_boolean_primitives_are_optimal(self):
        bounds = {b.component.split(" ")[0]: b for b in component_bounds(32, timeout=20)}
        assert bounds["gidney"].optimal
        assert bounds["Ch"].optimal
        assert bounds["Maj"].optimal
        assert not bounds["cdkm"].optimal

    def test_whole_circuit_overhead_is_reported(self):
        report = circuit_bound_report(rounds=4, timeout=20)
        assert report.lower_bound > 0
        assert report.overhead >= 1.0
        assert any("floor" in n for n in report.notes)


class TestSuperoptimization:
    def test_permutation_of_a_known_circuit(self):
        perm = permutation_of([("c1x", (0,), 1)], 2)
        assert perm == (0, 3, 2, 1)

    def test_finds_the_optimal_cnot(self):
        target = permutation_of([("c1x", (0,), 1)], 2)
        result = synthesise_optimal(target, 2, max_cost=4, timeout=30)
        assert result.found and result.optimal
        assert len(result.circuit) == 1

    def test_shortest_is_not_cheapest(self):
        """The headline finding: gate count is the wrong objective here."""
        from qsha256.formal.superopt import verify_primitive_optimality

        for entry in verify_primitive_optimality(timeout=60):
            assert entry["proved_optimal"]
            assert entry["qsha256_and_count"] <= entry["shortest_and_count"]
            assert entry["qsha256_gate_count"] >= entry["shortest_gate_count"]
