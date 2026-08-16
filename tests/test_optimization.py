"""Design-space search, gate-level rewriting, equivalence checking, hardware ranking."""

from __future__ import annotations

import pytest
from qiskit import QuantumCircuit

from qsha256.quantum.optimization.hardware import rank_for_hardware
from qsha256.quantum.optimization.rewrite import (
    REWRITE_PASSES,
    apply_rewrites,
    cancel_involutions,
    commutes,
    constant_fold,
)
from qsha256.quantum.optimization.search import (
    OBJECTIVES,
    compare_designs,
    pareto_front,
    search_designs,
)
from qsha256.quantum.optimization.verify import Assurance, check_equivalence
from qsha256.quantum.registers import CircuitBuilder
from qsha256.quantum.sha256.compression import build_compression
from qsha256.quantum.strategies import (
    PRESETS,
    Strategy,
    enumerate_strategies,
    get_preset,
)
from qsha256.spec import TOY4


class TestStrategies:
    def test_every_axis_value_builds(self):
        count = sum(1 for _ in enumerate_strategies())
        assert count == 3 * 2 * 2 * 3 * 2

    def test_invalid_axis_value_is_rejected(self):
        with pytest.raises(ValueError, match=r"strategy\.adder"):
            Strategy(adder="magic")

    def test_labels_are_stable_and_unique(self):
        labels = {s.label() for s in enumerate_strategies()}
        assert len(labels) == sum(1 for _ in enumerate_strategies())

    @pytest.mark.parametrize("name", sorted(PRESETS))
    def test_presets_resolve(self, name):
        assert isinstance(get_preset(name), Strategy)

    def test_unknown_preset_is_rejected(self):
        with pytest.raises(KeyError, match="unknown preset"):
            get_preset("fastest")

    def test_pinning_an_axis_restricts_the_space(self):
        strategies = list(enumerate_strategies(adder="cdkm"))
        assert strategies and all(s.adder == "cdkm" for s in strategies)


class TestCommutation:
    def _one(self, name, *qubits):
        qc = QuantumCircuit(4)
        getattr(qc, name)(*qubits)
        return qc.data[0]

    def test_disjoint_gates_commute(self):
        assert commutes(self._one("x", 0), self._one("x", 1))
        assert commutes(self._one("cx", 0, 1), self._one("cx", 2, 3))

    def test_target_onto_control_does_not_commute(self):
        assert not commutes(self._one("cx", 0, 1), self._one("cx", 1, 2))

    def test_shared_target_commutes(self):
        """Two X-type gates writing the same target commute: X and X commute."""
        assert commutes(self._one("cx", 0, 2), self._one("cx", 1, 2))

    def test_diagonal_gates_commute_with_each_other(self):
        assert commutes(self._one("z", 0), self._one("z", 0))

    def test_x_flips_the_basis_under_a_z(self):
        assert not commutes(self._one("z", 1), self._one("cx", 0, 1))

    def test_unknown_gates_are_assumed_not_to_commute(self):
        qc = QuantumCircuit(2)
        qc.h(0)
        assert not commutes(qc.data[0], self._one("x", 1))


class TestRewrite:
    def test_adjacent_involutions_cancel(self):
        qc = QuantumCircuit(2)
        qc.cx(0, 1)
        qc.cx(0, 1)
        assert len(cancel_involutions(qc).data) == 0

    def test_cancellation_sees_through_commuting_gates(self):
        qc = QuantumCircuit(4)
        qc.cx(0, 1)
        qc.cx(2, 3)  # disjoint, commutes
        qc.cx(0, 1)
        out = cancel_involutions(qc)
        assert len(out.data) == 1
        assert out.data[0].operation.name == "cx"

    def test_cancellation_respects_non_commuting_gates(self):
        qc = QuantumCircuit(3)
        qc.cx(0, 1)
        qc.cx(1, 2)  # reads qubit 1, does not commute
        qc.cx(0, 1)
        assert len(cancel_involutions(qc).data) == 3

    def test_constant_folding_drops_zero_controlled_gates(self):
        qc = QuantumCircuit(3)
        qc.cx(0, 1)  # qubit 0 provably |0>
        folded = constant_fold(qc, known_zero=set(qc.qubits))
        assert len(folded.data) == 0

    def test_constant_folding_demotes_one_controlled_gates(self):
        qc = QuantumCircuit(3)
        qc.x(0)  # qubit 0 now provably |1>
        qc.ccx(0, 1, 2)  # qubit 1 is an unknown input
        folded = constant_fold(qc, known_zero={qc.qubits[0]})
        names = [i.operation.name for i in folded.data]
        assert names == ["x", "cx"], "a |1> control should demote CCX to CX"

    def test_constant_folding_drops_a_gate_with_both_kinds_of_known_control(self):
        """|1> on one control and |0> on another still leaves the gate an identity."""
        qc = QuantumCircuit(3)
        qc.x(0)
        qc.ccx(0, 1, 2)
        folded = constant_fold(qc, known_zero=set(qc.qubits))
        assert [i.operation.name for i in folded.data] == ["x"]

    def test_constant_folding_respects_unknown_inputs(self):
        qc = QuantumCircuit(2)
        qc.cx(0, 1)
        assert len(constant_fold(qc, known_zero=set()).data) == 1

    @pytest.mark.parametrize("const_add", ["load", "vbe_const"])
    def test_rewriting_preserves_the_function(self, const_add):
        comp = build_compression(TOY4, Strategy(const_add=const_add), rounds=8)
        result = apply_rewrites(comp.builder)
        free = [q for w in comp.state + comp.message for q in w.qubits]
        assert check_equivalence(comp.circuit, result.circuit, free_qubits=free, trials=64)

    def test_rewriting_actually_removes_gates(self):
        comp = build_compression(TOY4, Strategy(), rounds=8)
        result = apply_rewrites(comp.builder)
        assert result.removed > 0
        assert result.after["ccx"] < result.before["ccx"]
        assert "-" in result.summary(), "a reduction must be reported as negative"

    def test_rewriting_finds_the_hand_written_constant_specialisation(self):
        """constfold should derive automatically what vbe_const encodes by hand."""
        loaded = apply_rewrites(
            build_compression(TOY4, Strategy(const_add="load"), rounds=8).builder
        )
        manual = apply_rewrites(
            build_compression(TOY4, Strategy(const_add="vbe_const"), rounds=8).builder
        )
        assert loaded.after["ccx"] == manual.after["ccx"]

    def test_pass_registry_is_complete(self):
        assert set(REWRITE_PASSES) == {"cancel", "constfold"}


class TestEquivalence:
    def test_identical_circuits_are_equivalent(self):
        comp = build_compression(TOY4, Strategy(), rounds=2)
        assert check_equivalence(comp.circuit, comp.circuit.copy())

    def test_a_difference_is_detected(self):
        comp = build_compression(TOY4, Strategy(), rounds=2)
        broken = comp.circuit.copy()
        broken.x(0)
        result = check_equivalence(comp.circuit, broken, free_qubits=comp.state[0].qubits)
        assert not result
        assert result.counterexample is not None

    def test_narrow_circuits_are_checked_exhaustively(self):
        b = CircuitBuilder("small")
        w = b.add_word(4, "w")
        b.cx(w[0], w[1])
        result = check_equivalence(b.circuit, b.circuit.copy(), free_qubits=w.qubits)
        assert result.assurance == Assurance.EXHAUSTIVE
        assert result.trials == 16

    def test_wide_circuits_fall_back_to_randomized(self):
        comp = build_compression(TOY4, Strategy(), rounds=2)
        free = [q for w in comp.state + comp.message for q in w.qubits]
        result = check_equivalence(comp.circuit, comp.circuit.copy(), free_qubits=free, trials=8)
        assert result.assurance == Assurance.RANDOMIZED

    def test_mismatched_widths_are_rejected(self):
        assert not check_equivalence(QuantumCircuit(2), QuantumCircuit(3))

    def test_unsupported_gates_are_reported_not_guessed(self):
        qc = QuantumCircuit(1)
        qc.h(0)
        result = check_equivalence(qc, qc.copy())
        assert result.assurance == Assurance.UNSUPPORTED


@pytest.fixture(scope="module")
def search_result():
    return search_designs(TOY4, rounds=4, verify_trials=1, adder=("cdkm", "vbe"))


class TestSearch:
    @pytest.fixture
    def result(self, search_result):
        return search_result

    def test_every_basis_simulable_design_verifies(self, result):
        unverified = [
            p for p in result.points if not p.verified and "UNSUPPORTED" not in p.verification
        ]
        assert not unverified, [p.label for p in unverified]

    def test_search_produces_a_pareto_front(self, result):
        assert result.front
        assert len(result.front) <= len(result.points)

    def test_pareto_front_members_are_undominated(self, result):
        objectives = result.objectives
        for candidate in result.front:
            cm = candidate.metrics(objectives)
            for other in result.points:
                om = other.metrics(objectives)
                dominates = all(o <= c for o, c in zip(om, cm)) and any(
                    o < c for o, c in zip(om, cm)
                )
                assert not dominates, f"{other.label} dominates front member {candidate.label}"

    def test_best_by_objective_is_a_real_minimum(self, result):
        for objective in OBJECTIVES:
            best = result.best(objective)
            assert OBJECTIVES[objective](best.report) == min(
                OBJECTIVES[objective](p.report) for p in result.points
            )

    def test_result_serialises(self, result):
        data = result.to_dict()
        assert data["spec"] == "toy4"
        assert len(data["points"]) == len(result.points)

    def test_rendering_marks_the_front(self, result):
        assert "* = on the Pareto front" in str(result)

    def test_pareto_front_of_a_single_point(self):
        class P:
            label = "only"

            def metrics(self, objectives):
                return (1, 2, 3)

        assert len(pareto_front([P()], ("a",))) == 1

    def test_comparison_quantifies_the_trade(self, result):
        text = compare_designs(result.points[0], result.points[-1], result.objectives)
        assert "%" in text or "identical" in text


class TestHardwareRanking:
    def test_ranking_orders_by_spacetime_volume(self):
        result = search_designs(
            TOY4,
            rounds=4,
            verify=False,
            rewrite=False,
            adder="cdkm",
            round_layout=("serial", "wide"),
        )
        ranking = rank_for_hardware(result.points, "superconducting")
        volumes = [
            s.spacetime_volume for s in sorted(ranking.scored, key=lambda s: s.spacetime_volume)
        ]
        assert volumes == sorted(volumes)
        assert ranking.best is not None

    def test_ranking_serialises_and_renders(self):
        result = search_designs(TOY4, rounds=2, verify=False, rewrite=False, adder="cdkm")
        ranking = rank_for_hardware(result.points, "superconducting")
        assert ranking.to_dict()["hardware_model"] == "superconducting"
        assert "ASSUMPTION-DEPENDENT" in str(ranking)
