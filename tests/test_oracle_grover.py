"""Preimage oracle, phase behaviour, toy Grover, and cost extrapolation."""

from __future__ import annotations

import math

import pytest

from qsha256.classical.sha256 import compress
from qsha256.quantum.oracle.grover import (
    build_toy_grover,
    diffusion,
    grover_cost_estimate,
    grover_iterations,
)
from qsha256.quantum.oracle.predicate import equality_ancilla_count, equality_phase_flip
from qsha256.quantum.oracle.preimage import build_preimage_oracle
from qsha256.quantum.oracle.toy import TOY_TINY, build_toy_hash, toy_compress
from qsha256.quantum.registers import CircuitBuilder
from qsha256.quantum.resources import analyze
from qsha256.quantum.strategies import Strategy
from qsha256.spec import SHA256, TOY4
from qsha256.validation.basis_sim import BasisSimulator


class TestPredicate:
    @pytest.mark.parametrize("width", [2, 3, 4])
    def test_phase_flips_exactly_the_target(self, width):
        for target in range(1 << width):
            b = CircuitBuilder("cmp")
            w = b.add_word(width, "d")
            equality_phase_flip(b, [w], target)
            sim = BasisSimulator(b.circuit)
            for value in range(1 << width):
                out, phase = sim.run(sim.load({w: value}))
                assert (phase == -1) == (value == target), f"target={target} value={value}"
                assert sim.read(out, w) == value, "register mutated"
                assert not sim.nonzero_indices(out, exclude=[w]), "ancilla left dirty"

    def test_ancilla_cost_is_explicit(self):
        assert equality_ancilla_count(256) == 255  # 254 tree ancillas + 1 marker

    def test_rejects_an_oversized_target(self):
        b = CircuitBuilder("cmp")
        w = b.add_word(4, "d")
        with pytest.raises(ValueError, match="does not fit"):
            equality_phase_flip(b, [w], 1 << 5)


class TestToyHash:
    def test_matches_its_classical_reference_exhaustively(self):
        spec = TOY_TINY
        toy = build_toy_hash(spec)
        sim = BasisSimulator(toy.circuit)
        space = 1 << (spec.message_words * spec.word_bits)
        for candidate in range(space):
            words = [
                (candidate >> (i * spec.word_bits)) & spec.mask for i in range(spec.message_words)
            ]
            out, _ = sim.run(sim.load(dict(zip(toy.message, words))))
            assert tuple(sim.read(out, r) for r in toy.state) == toy_compress(words, spec)
            assert not sim.nonzero_indices(out, exclude=toy.message + toy.state)

    def test_is_small_enough_to_simulate(self):
        assert build_toy_hash(TOY_TINY).circuit.num_qubits < 24

    def test_toy_is_never_labelled_sha256(self):
        assert "sha" not in TOY_TINY.name.lower()
        assert "toy" in TOY_TINY.name.lower()


class TestPreimageOracle:
    def test_flips_exactly_the_preimages_over_a_whole_space(self):
        spec = TOY4
        iv = tuple(spec.h0)
        reference = [3, 1, 4, 1]
        target_state = compress(iv, reference, spec)
        target = sum(v << (i * spec.word_bits) for i, v in enumerate(target_state))
        oracle = build_preimage_oracle(
            spec, Strategy(uncompute_working=True), target_digest=target, initial_state=iv
        )
        sim = BasisSimulator(oracle.circuit)
        flipped = []
        for m0 in range(1 << spec.word_bits):
            for m1 in range(1 << spec.word_bits):
                block = [m0, m1, 4, 1]
                out, phase = sim.run(sim.load(dict(zip(oracle.message, block))))
                if phase == -1:
                    flipped.append((m0, m1))
                assert [sim.read(out, r) for r in oracle.message] == block
                assert not sim.nonzero_indices(out, exclude=oracle.message), "garbage left"
        expected = [
            (m0, m1)
            for m0 in range(1 << spec.word_bits)
            for m1 in range(1 << spec.word_bits)
            if compress(iv, [m0, m1, 4, 1], spec) == target_state
        ]
        assert flipped == expected

    def test_leaves_no_garbage_which_grover_requires(self):
        spec = TOY4
        oracle = build_preimage_oracle(
            spec,
            Strategy(uncompute_working=True),
            target_digest=0,
            initial_state=tuple(spec.h0),
        )
        sim = BasisSimulator(oracle.circuit)
        out, _ = sim.run(sim.load(dict.fromkeys(oracle.message, 5)))
        assert not sim.nonzero_indices(out, exclude=oracle.message)

    def test_rejects_the_qft_adder(self):
        with pytest.raises(ValueError, match="not built from self-inverse"):
            build_preimage_oracle(TOY4, Strategy(adder="qft"), rounds=4)

    def test_fixed_words_shrink_the_search_space(self):
        oracle = build_preimage_oracle(
            TOY4,
            Strategy(uncompute_working=True),
            rounds=4,
            fixed_words={2: 4, 3: 1},
            initial_state=tuple(TOY4.h0),
        )
        assert oracle.search_qubits == 2 * TOY4.word_bits


@pytest.mark.slow
class TestFullScaleOracle:
    def test_oracle_costs_about_twice_the_forward_circuit(self):
        """One Grover query is not one SHA-256 evaluation. Measure the factor."""
        from qsha256.quantum.sha256.compression import build_compression

        forward = build_compression(SHA256, Strategy(), rounds=64)
        oracle = build_preimage_oracle(
            SHA256,
            Strategy(uncompute_working=True),
            rounds=64,
            target_digest=0,
            initial_state=tuple(SHA256.h0),
        )
        f = forward.circuit.count_ops()["ccx"]
        o = oracle.circuit.count_ops()["ccx"]
        assert 1.9 < o / f < 2.2, f"oracle/forward Toffoli ratio was {o / f:.2f}"


class TestGrover:
    def test_iteration_count_follows_the_formula(self):
        assert grover_iterations(4, 1) == int((math.pi / 4) * math.sqrt(16))
        assert grover_iterations(8, 4) == int((math.pi / 4) * math.sqrt(256 / 4))

    def test_iteration_count_rejects_impossible_solution_counts(self):
        with pytest.raises(ValueError):
            grover_iterations(4, 0)
        with pytest.raises(ValueError):
            grover_iterations(2, 100)

    def test_diffusion_preserves_ancillas(self):
        b = CircuitBuilder("diff")
        w = b.add_word(4, "m")
        diffusion(b, [w])
        assert b.ancillas.live == 0

    def test_toy_grover_amplifies_a_planted_solution(self):
        """The one genuinely quantum result here: run it and check the amplitudes."""
        from qiskit.quantum_info import Statevector

        builder, message, _iterations, _target, solutions = build_toy_grover(compare_bits=4)
        circuit = builder.circuit
        assert circuit.num_qubits < 24, "toy must stay statevector-simulable"

        state = Statevector.from_instruction(circuit)
        indices = [circuit.find_bit(q).index for w in message for q in w.qubits]
        probabilities = state.probabilities_dict(qargs=indices)
        spec = TOY_TINY

        def decode(bits: str) -> tuple[int, ...]:
            value = sum(int(bits[::-1][i]) << i for i in range(len(bits)))
            return tuple(
                (value >> (i * spec.word_bits)) & spec.mask for i in range(spec.message_words)
            )

        found = sum(p for bits, p in probabilities.items() if decode(bits) in solutions)
        uniform = len(solutions) / 2 ** (spec.message_words * spec.word_bits)
        assert found > 0.9, f"amplification failed: {found:.4f}"
        assert found > 10 * uniform

    def test_toy_grover_rejects_an_unreachable_target(self):
        with pytest.raises(ValueError, match="no preimage"):
            build_toy_grover(target_digest=0xF, compare_bits=2)

    def test_grover_estimate_separates_measured_from_extrapolated(self):
        from qsha256.quantum.sha256.compression import build_compression

        comp = build_compression(TOY4, Strategy(uncompute_working=True), rounds=4)
        report = analyze(comp, spec=TOY4, strategy=comp.strategy, rounds=4)
        estimate = grover_cost_estimate(report, search_bits=256)

        assert estimate.oracle_t_count == report.t_count  # measured
        assert estimate.provenance["oracle_costs"].startswith("MEASURED")
        assert estimate.provenance["totals"].startswith("EXTRAPOLATED")
        # (pi/4) * 2^128 -- the number everyone quotes
        assert abs(estimate.iterations_log2 - (128 + math.log2(math.pi / 4))) < 1e-9
        assert estimate.total_t_count_log2 > estimate.iterations_log2

    def test_grover_estimate_carries_the_caveats(self):
        from qsha256.quantum.sha256.compression import build_compression

        comp = build_compression(TOY4, Strategy(uncompute_working=True), rounds=4)
        estimate = grover_cost_estimate(analyze(comp, spec=TOY4, rounds=4))
        text = " ".join(estimate.caveats).lower()
        assert "maxdepth" in text
        assert "sqrt(m)" in text
        assert "not been simulated" in text
        assert "error correction" in text
