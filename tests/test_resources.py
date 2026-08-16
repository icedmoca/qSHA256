"""Resource analysis, Clifford+T modelling, reports and fault-tolerant estimation."""

from __future__ import annotations

import csv
import io
import json

import pytest
from qiskit import QuantumCircuit, transpile

from qsha256.quantum.registers import CircuitBuilder
from qsha256.quantum.resources import analyze, estimate_physical, measure_depth
from qsha256.quantum.resources.analyzer import Provenance
from qsha256.quantum.resources.clifford_t import (
    CLIFFORD_T_BASIS,
    TOFFOLI_MODELS,
    clifford_t_cost,
    get_model,
    rz_t_count,
)
from qsha256.quantum.resources.leaderboard import (
    PUBLISHED,
    Comparability,
    build_leaderboard,
    render_leaderboard,
)
from qsha256.quantum.resources.physical import (
    HARDWARE_MODELS,
    HardwareModel,
    choose_code_distance,
    logical_error_rate,
)
from qsha256.quantum.resources.reports import log2_str, pow2_str, render, to_csv, to_markdown
from qsha256.quantum.sha256.compression import build_compression
from qsha256.quantum.strategies import Strategy
from qsha256.spec import TOY4


@pytest.fixture(scope="module")
def toy_report():
    comp = build_compression(TOY4, Strategy(), rounds=4)
    return analyze(comp, spec=TOY4, strategy=comp.strategy, rounds=4, target="toy")


class TestCliffordT:
    def test_qiskit_reproduces_the_standard_toffoli_decomposition(self):
        """Anchors the analytical model to a real compiler: 7 T gates per Toffoli."""
        qc = QuantumCircuit(3)
        qc.ccx(0, 1, 2)
        ops = transpile(qc, basis_gates=CLIFFORD_T_BASIS, optimization_level=0).count_ops()
        assert ops.get("t", 0) + ops.get("tdg", 0) == TOFFOLI_MODELS["standard"].t_count

    def test_analytical_t_count_matches_transpiled_at_scale(self):
        """If these ever diverge, one of the two is wrong."""
        comp = build_compression(TOY4, Strategy(), rounds=8)
        report = analyze(comp, spec=TOY4, rounds=8, transpile_t=True)
        assert report.t_count == report.clifford_t["t_count_transpiled"]
        assert report.t_count == report.toffoli_count * 7

    @pytest.mark.parametrize("name", sorted(TOFFOLI_MODELS))
    def test_every_model_is_documented(self, name):
        model = get_model(name)
        assert model.reference and model.notes
        assert model.t_count > 0 and model.t_depth > 0

    def test_models_disagree_which_is_the_point(self):
        counts = {"ccx": 1000}
        standard = clifford_t_cost(counts, "standard")
        jones = clifford_t_cost(counts, "jones")
        assert standard["t_count"] == 7000
        assert jones["t_count"] == 4000
        assert jones["measurements"] == 1000, "Jones needs measurement + feedforward"
        assert standard["measurements"] == 0

    def test_unknown_model_is_rejected(self):
        with pytest.raises(KeyError, match="unknown Toffoli model"):
            get_model("magic")

    def test_rotation_synthesis_is_precision_dependent(self):
        assert rz_t_count(1e-10) > rz_t_count(1e-5)
        with pytest.raises(ValueError):
            rz_t_count(0)

    def test_rotations_make_the_cost_inexact(self):
        assert clifford_t_cost({"ccx": 10})["exact"] is True
        assert clifford_t_cost({"cp": 10})["exact"] is False

    def test_t_depth_bound_is_labelled_serial(self):
        cost = clifford_t_cost({"ccx": 100}, "standard")
        assert cost["t_depth_serial_bound"] == 400  # fully serialised upper bound


class TestAnalyzer:
    def test_measured_fields_match_the_circuit(self, toy_report):
        comp = build_compression(TOY4, Strategy(), rounds=4)
        assert toy_report.width == comp.circuit.num_qubits
        assert toy_report.toffoli_count == comp.circuit.count_ops().get("ccx", 0)
        assert toy_report.total_gates == sum(comp.circuit.count_ops().values())

    def test_components_are_disjoint_and_sum_to_the_total(self, toy_report):
        total = sum(c.get("_total", 0) for c in toy_report.component_costs.values())
        ccx = sum(c.get("_ccx", 0) for c in toy_report.component_costs.values())
        assert total == toy_report.total_gates
        assert ccx == toy_report.toffoli_count

    def test_assumptions_are_always_present(self, toy_report):
        text = " ".join(toy_report.assumptions).lower()
        assert "logical resources only" in text
        assert "all-to-all connectivity" in text
        assert "t-count model" in text

    def test_forward_circuit_is_declared_not_garbage_free(self):
        comp = build_compression(TOY4, Strategy(), rounds=4)
        report = analyze(comp, spec=TOY4, strategy=comp.strategy, rounds=4)
        assert any("NOT garbage-free" in a for a in report.assumptions)

    def test_uncomputed_circuit_is_declared_garbage_free(self):
        comp = build_compression(TOY4, Strategy(uncompute_working=True), rounds=4)
        report = analyze(comp, spec=TOY4, strategy=comp.strategy, rounds=4)
        assert any("garbage-free" in a and "NOT" not in a for a in report.assumptions)

    def test_hardware_execution_is_never_claimed(self, toy_report):
        assert toy_report.hardware_executed is False

    def test_provenance_is_recorded(self):
        comp = build_compression(TOY4, Strategy(), rounds=4)
        assert analyze(comp, transpile_t=True).t_count_provenance == Provenance.TRANSPILED
        assert analyze(comp, transpile_t=False).t_count_provenance == Provenance.ANALYTICAL

    def test_rotation_circuits_are_not_transpiled(self):
        """No exact Clifford+T form exists, so the analytical model must be used."""
        comp = build_compression(TOY4, Strategy(adder="qft"), rounds=4)
        report = analyze(comp, spec=TOY4, rounds=4, transpile_t=True)
        assert report.t_count_provenance == Provenance.ANALYTICAL
        assert "transpile_skipped" in report.clifford_t

    def test_accepts_builder_circuit_or_wrapper(self):
        comp = build_compression(TOY4, Strategy(), rounds=2)
        assert analyze(comp).width == analyze(comp.builder).width == analyze(comp.circuit).width

    def test_rejects_unknown_source(self):
        with pytest.raises(TypeError):
            analyze("not a circuit")


class TestDepth:
    def test_depth_filters_are_distinct(self):
        b = CircuitBuilder("d")
        w = b.add_word(3, "w")
        b.x(w[0])
        b.cx(w[0], w[1])
        b.ccx(w[0], w[1], w[2])
        metrics = measure_depth(b.circuit)
        assert metrics.toffoli == 1
        assert metrics.two_qubit == 2
        assert metrics.total >= metrics.two_qubit >= metrics.toffoli


class TestReports:
    def test_json_round_trips(self, toy_report):
        data = json.loads(render(toy_report, "json"))
        assert data["width"] == toy_report.width
        assert data["environment"]["qiskit_version"]

    def test_csv_has_a_header_and_one_row(self, toy_report):
        rows = list(csv.reader(io.StringIO(to_csv([toy_report]))))
        assert rows[0][0] == "spec"
        assert len(rows) == 2

    def test_markdown_is_a_table(self, toy_report):
        text = to_markdown([toy_report])
        assert text.startswith("| Circuit |")
        assert "---" in text

    def test_text_report_labels_provenance(self, toy_report):
        text = str(toy_report)
        assert "[MEASURED]" in text
        assert "Assumptions" in text
        assert "Run on hardware:      no" in text

    def test_unknown_format_is_rejected(self, toy_report):
        with pytest.raises(KeyError, match="unknown format"):
            render(toy_report, "xml")

    def test_large_numbers_use_powers_of_two(self):
        assert pow2_str(1000) == "1,000"
        assert pow2_str(2**80).startswith("~2^80")
        assert log2_str(1024) == "2^10.0"


class TestPhysical:
    def test_error_rate_falls_with_distance(self):
        model = HARDWARE_MODELS["superconducting"]
        assert logical_error_rate(11, model) < logical_error_rate(5, model)

    def test_above_threshold_is_not_achievable(self, toy_report):
        broken = HardwareModel(name="above-threshold", physical_error_rate=0.02, threshold=0.01)
        assert choose_code_distance(100, 1000, broken) is None
        estimate = estimate_physical(toy_report, broken)
        assert estimate.achievable is False
        assert "NOT ACHIEVABLE" in str(estimate)

    def test_better_hardware_needs_a_smaller_code(self, toy_report):
        good = estimate_physical(toy_report, "optimistic")
        bad = estimate_physical(toy_report, "conservative")
        assert good.code_distance < bad.code_distance
        assert good.physical_qubits_data < bad.physical_qubits_data

    def test_factory_footprint_can_dominate_a_small_circuit(self, toy_report):
        """Not a bug: for a tiny circuit the magic-state factories cost more than
        the data patches, so a *better* machine with more factories can report a
        larger total. Only the data footprint tracks the code distance."""
        good = estimate_physical(toy_report, "optimistic")
        assert good.physical_qubits_factories > good.physical_qubits_data

    def test_estimate_always_states_its_assumptions(self, toy_report):
        estimate = estimate_physical(toy_report, "superconducting")
        text = " ".join(estimate.assumptions).lower()
        assert "surface code" in text
        assert "physical error rate" in text
        assert "no hardware was involved" in text
        assert estimate.provenance == "ASSUMPTION-DEPENDENT"

    def test_logical_qubits_are_never_equated_with_physical(self, toy_report):
        estimate = estimate_physical(toy_report, "superconducting")
        assert estimate.physical_qubits_total > estimate.logical_qubits * 10

    def test_runtime_is_the_larger_of_the_two_limits(self, toy_report):
        estimate = estimate_physical(toy_report, "superconducting")
        assert estimate.code_cycles == max(
            estimate.code_cycles_reaction_limited, estimate.code_cycles_distillation_limited
        )

    def test_unknown_model_is_rejected(self, toy_report):
        with pytest.raises(KeyError, match="unknown hardware model"):
            estimate_physical(toy_report, "quantum-unicorn")


class TestLeaderboard:
    def test_published_entries_carry_citations(self):
        for entry in PUBLISHED.values():
            assert entry.citation and entry.source and entry.scope

    def test_incomparable_metrics_get_no_ratio(self):
        """A Toffoli-level depth must never be divided by a Clifford+T depth."""
        comp = build_compression(TOY4, Strategy(), rounds=4)
        untranspiled = analyze(comp, spec=TOY4, rounds=4, transpile_t=False)
        rows = {r.metric: r for r in build_leaderboard(untranspiled, "amy2016")}
        assert rows["depth"].comparability == Comparability.INCOMPARABLE
        assert rows["depth"].ratio is None

    def test_transpiled_report_enables_depth_comparison(self):
        comp = build_compression(TOY4, Strategy(), rounds=8)
        report = analyze(comp, spec=TOY4, rounds=8, transpile_t=True)
        rows = {r.metric: r for r in build_leaderboard(report, "amy2016")}
        assert rows["depth"].comparability == Comparability.QUALIFIED
        assert rows["depth"].ratio is not None

    def test_missing_published_values_are_not_invented(self):
        assert PUBLISHED["amy2016-opt"].toffoli_count is None

    def test_render_includes_the_citation(self, toy_report):
        text = render_leaderboard(toy_report, "amy2016")
        assert "arXiv:1603.09383" in text
        assert "Comparability notes" in text


class TestBasisSimulator:
    """The tool everything else is validated with, so it needs its own tests."""

    def test_rejects_gates_it_cannot_execute(self):
        from qsha256.validation.basis_sim import BasisSimulator, UnsupportedGate

        qc = QuantumCircuit(1)
        qc.h(0)
        with pytest.raises(UnsupportedGate, match="cannot be simulated"):
            BasisSimulator(qc)

    def test_tracks_phase_from_diagonal_gates(self):
        from qsha256.validation.basis_sim import BasisSimulator

        b = CircuitBuilder("z")
        w = b.add_word(1, "q")
        b.z(w[0])
        sim = BasisSimulator(b.circuit)
        assert sim.run([0])[1] == 1
        assert sim.run([1])[1] == -1

    def test_executes_toffoli_correctly(self):
        from qsha256.validation.basis_sim import BasisSimulator

        b = CircuitBuilder("ccx")
        w = b.add_word(3, "q")
        b.ccx(w[0], w[1], w[2])
        sim = BasisSimulator(b.circuit)
        for value in range(8):
            out, _ = sim.run(sim.load({w: value}))
            expected = value ^ (0b100 if (value & 0b011) == 0b011 else 0)
            assert sim.read(out, w) == expected

    def test_rejects_a_wrong_length_input(self):
        from qsha256.validation.basis_sim import BasisSimulator

        b = CircuitBuilder("x")
        b.add_word(3, "q")
        with pytest.raises(ValueError, match="expected 3 bits"):
            BasisSimulator(b.circuit).run([0, 0])
