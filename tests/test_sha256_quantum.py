"""The reversible SHA-256 construction, checked against the classical model."""

from __future__ import annotations

import hashlib

import pytest

from conftest import assert_ancillas_clean, run_circuit

from qsha256.classical.sha256 import (
    big_sigma0,
    big_sigma1,
    compress,
    message_schedule,
    pad_message,
    parse_blocks,
    round_step,
    small_sigma0,
    small_sigma1,
)
from qsha256.quantum.registers import CircuitBuilder
from qsha256.quantum.sha256.compression import build_compression
from qsha256.quantum.sha256.functions import (
    big_sigma0_into,
    big_sigma1_into,
    small_sigma0_into,
    small_sigma1_into,
)
from qsha256.quantum.sha256.round import build_round_circuit
from qsha256.quantum.sha256.schedule import build_schedule
from qsha256.quantum.strategies import Strategy
from qsha256.spec import SHA256, TOY4, TOY8

LAYOUTS = ["serial", "wide", "csa"]
SCHEDULES = ["rolling", "store_all"]


class TestSigmaFunctions:
    @pytest.mark.parametrize("fn,ref", [
        (big_sigma0_into, big_sigma0),
        (big_sigma1_into, big_sigma1),
        (small_sigma0_into, small_sigma0),
        (small_sigma1_into, small_sigma1),
    ])
    def test_matches_classical_at_full_width(self, rng, fn, ref):
        b = CircuitBuilder("sigma")
        x, t = b.add_word(32, "x"), b.add_word(32, "t")
        fn(b, x, t, SHA256)
        for value in [0, 1, 0xFFFFFFFF, 0x80000000] + [rng.getrandbits(32) for _ in range(30)]:
            sim, out = run_circuit(b, {x: value})
            assert sim.read(out, t) == ref(value, SHA256)
            assert sim.read(out, x) == value


class TestRound:
    @pytest.mark.parametrize("spec", [TOY4, TOY8, SHA256])
    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_round_matches_classical(self, rng, spec, layout):
        b, st_in, w, st_out = build_round_circuit(spec, Strategy(round_layout=layout), t=0)
        for _ in range(10):
            values = [rng.getrandbits(spec.word_bits) for _ in range(8)]
            wv = rng.getrandbits(spec.word_bits)
            sim, out = run_circuit(b, dict(zip(st_in, values)) | {w: wv})
            expected, _ = round_step(tuple(values), wv, spec.k[0], spec)
            assert tuple(sim.read(out, r) for r in st_out) == expected
            assert sim.read(out, w) == wv, "W[t] was mutated"
            assert_ancillas_clean(b, sim, out)

    def test_round_allocates_no_permanent_qubits(self):
        """Six of eight state words are renamed, not moved; only temporaries are borrowed."""
        b, st_in, w, st_out = build_round_circuit(SHA256, Strategy(), t=0)
        assert b.data_qubits == 9 * 32  # a..h plus W
        assert set(st_out) == set(st_in), "round should permute, not reallocate"

    def test_register_permutation_costs_no_gates(self):
        b, st_in, w, st_out = build_round_circuit(SHA256, Strategy(), t=0)
        assert "swap" not in b.circuit.count_ops()

    def test_layouts_agree_with_each_other(self, rng):
        """Different layouts must compute the same function, differing only in cost."""
        results = []
        for layout in LAYOUTS:
            b, st_in, w, st_out = build_round_circuit(SHA256, Strategy(round_layout=layout), t=0)
            values = [i * 0x01010101 + 7 for i in range(8)]
            sim, out = run_circuit(b, dict(zip(st_in, values)) | {w: 0xDEADBEEF})
            results.append(tuple(sim.read(out, r) for r in st_out))
        assert len(set(results)) == 1

    def test_rejects_wrong_state_size(self):
        from qsha256.quantum.sha256.round import apply_round

        b = CircuitBuilder("bad")
        with pytest.raises(ValueError, match="exactly 8 state words"):
            apply_round(b, (b.add_word(32, "a"),), b.add_word(32, "w"), 0)


class TestSchedule:
    @pytest.mark.parametrize("name", SCHEDULES)
    @pytest.mark.parametrize("spec", [TOY4, SHA256])
    def test_schedule_matches_classical(self, rng, name, spec):
        b = CircuitBuilder(f"sched_{name}")
        sched = build_schedule(b, spec, Strategy(schedule=name))
        for t in range(spec.rounds):
            sched.word(t)
        # The rolling window only keeps the most recent block_words entries.
        alive = range(spec.rounds - spec.block_words, spec.rounds) if name == "rolling" \
            else range(spec.rounds)
        registers = {t: sched.word(t) for t in alive}
        for _ in range(3):
            block = [rng.getrandbits(spec.word_bits) for _ in range(spec.block_words)]
            expected = message_schedule(block, spec)
            sim, out = run_circuit(b, dict(zip(sched.message, block)))
            for t, reg in registers.items():
                assert sim.read(out, reg) == expected[t], f"W[{t}] mismatch ({name})"
            assert_ancillas_clean(b, sim, out)

    def test_store_all_costs_more_qubits_than_rolling(self):
        widths = {}
        for name in SCHEDULES:
            b = CircuitBuilder(name)
            sched = build_schedule(b, SHA256, Strategy(schedule=name))
            for t in range(64):
                sched.word(t)
            widths[name] = b.circuit.num_qubits
        assert widths["store_all"] - widths["rolling"] == 48 * 32

    def test_rolling_window_rejects_expired_words(self):
        b = CircuitBuilder("roll")
        sched = build_schedule(b, SHA256, Strategy(schedule="rolling"))
        sched.word(63)
        with pytest.raises(ValueError, match="already been overwritten"):
            sched.word(0)


class TestCompression:
    @pytest.mark.parametrize("spec,rounds", [(TOY4, 8), (TOY8, 8)])
    @pytest.mark.parametrize("layout", LAYOUTS)
    @pytest.mark.parametrize("schedule", SCHEDULES)
    def test_toy_compression_matches_classical(self, rng, spec, rounds, layout, schedule):
        strategy = Strategy(round_layout=layout, schedule=schedule, uncompute_working=True)
        comp = build_compression(spec, strategy, rounds=rounds)
        reduced = spec.with_rounds(rounds)
        for _ in range(3):
            state = [rng.getrandbits(spec.word_bits) for _ in range(8)]
            block = [rng.getrandbits(spec.word_bits) for _ in range(spec.block_words)]
            sim, out = run_circuit(
                comp.builder, dict(zip(comp.state, state)) | dict(zip(comp.message, block))
            )
            assert tuple(sim.read(out, r) for r in comp.digest) == compress(
                tuple(state), block, reduced
            )
            assert_ancillas_clean(comp.builder, sim, out)

    @pytest.mark.parametrize("rounds", [1, 2, 4, 16])
    def test_reduced_round_sha256_matches_classical(self, rng, rounds):
        comp = build_compression(SHA256, Strategy(), rounds=rounds)
        reduced = SHA256.with_rounds(rounds)
        state = [rng.getrandbits(32) for _ in range(8)]
        block = [rng.getrandbits(32) for _ in range(16)]
        sim, out = run_circuit(
            comp.builder, dict(zip(comp.state, state)) | dict(zip(comp.message, block))
        )
        assert tuple(sim.read(out, r) for r in comp.digest) == compress(
            tuple(state), block, reduced
        )

    def test_uncomputed_circuit_is_garbage_free(self, rng):
        """The precondition for oracle use: everything but the digest comes back."""
        comp = build_compression(TOY8, Strategy(uncompute_working=True), rounds=8)
        state = [rng.getrandbits(8) for _ in range(8)]
        block = [rng.getrandbits(8) for _ in range(TOY8.block_words)]
        sim, out = run_circuit(
            comp.builder, dict(zip(comp.state, state)) | dict(zip(comp.message, block))
        )
        assert comp.uncomputed
        assert all(sim.read(out, r) == 0 for r in comp.working), "work registers not cleared"
        assert [sim.read(out, r) for r in comp.message] == block, "message not restored"
        assert [sim.read(out, r) for r in comp.state] == state, "chaining input mutated"
        assert_ancillas_clean(comp.builder, sim, out)

    def test_forward_circuit_is_not_garbage_free(self, rng):
        """And the report must not claim otherwise."""
        comp = build_compression(TOY8, Strategy(), rounds=8)
        assert not comp.uncomputed
        assert comp.digest is comp.state

    def test_uncompute_rejects_the_qft_adder(self):
        with pytest.raises(ValueError, match="not built from self-inverse gates"):
            build_compression(TOY4, Strategy(adder="qft", uncompute_working=True), rounds=4)

    def test_rejects_out_of_range_rounds(self):
        with pytest.raises(ValueError, match="rounds must be in"):
            build_compression(SHA256, Strategy(), rounds=65)

    def test_uncompute_requires_a_digest_register(self):
        with pytest.raises(ValueError, match="requires output='digest'"):
            build_compression(TOY4, Strategy(), rounds=4, output="in_place", uncompute=True)


@pytest.mark.slow
class TestFullScaleSha256:
    """The headline claim: the real 32-bit, 64-round circuit computes SHA-256."""

    def test_digest_of_abc_matches_hashlib(self):
        blocks = parse_blocks(pad_message(b"abc"), SHA256)
        comp = build_compression(SHA256, Strategy(), initial_state=SHA256.h0)
        sim, out = run_circuit(comp.builder, dict(zip(comp.message, blocks[0])))
        digest = b"".join(sim.read(out, r).to_bytes(4, "big") for r in comp.state)
        assert digest == hashlib.sha256(b"abc").digest()

    def test_empty_message_digest_matches_hashlib(self):
        blocks = parse_blocks(pad_message(b""), SHA256)
        comp = build_compression(SHA256, Strategy(), initial_state=SHA256.h0)
        sim, out = run_circuit(comp.builder, dict(zip(comp.message, blocks[0])))
        digest = b"".join(sim.read(out, r).to_bytes(4, "big") for r in comp.state)
        assert digest == hashlib.sha256(b"").digest()

    def test_multi_block_chaining_matches_hashlib(self):
        """Two blocks, chained through the circuit exactly as the standard requires."""
        message = b"a" * 100
        blocks = parse_blocks(pad_message(message), SHA256)
        assert len(blocks) == 2
        state = tuple(SHA256.h0)
        comp = build_compression(SHA256, Strategy(), rounds=64)
        for block in blocks:
            sim, out = run_circuit(
                comp.builder, dict(zip(comp.state, state)) | dict(zip(comp.message, block))
            )
            state = tuple(sim.read(out, r) for r in comp.state)
        digest = b"".join(w.to_bytes(4, "big") for w in state)
        assert digest == hashlib.sha256(message).digest()

    def test_garbage_free_full_scale(self, rng):
        comp = build_compression(SHA256, Strategy(uncompute_working=True), rounds=64)
        state = [rng.getrandbits(32) for _ in range(8)]
        block = [rng.getrandbits(32) for _ in range(16)]
        sim, out = run_circuit(
            comp.builder, dict(zip(comp.state, state)) | dict(zip(comp.message, block))
        )
        assert tuple(sim.read(out, r) for r in comp.digest) == compress(
            tuple(state), block, SHA256
        )
        assert all(sim.read(out, r) == 0 for r in comp.working)
        assert_ancillas_clean(comp.builder, sim, out)
