"""SHA-512, SHA-3, Bitcoin, layout, cross-validation and the ancilla guard."""

from __future__ import annotations

import hashlib
import random

import pytest

from qsha256 import SHA256, SHA512
from qsha256.applications.bitcoin import (
    BlockHeader,
    build_mining_oracle,
    double_sha256,
    leading_zero_bits,
    midstate,
)
from qsha256.classical.sha256 import pad_message, parse_blocks
from qsha256.classical.sha256 import sha256 as sha_generic
from qsha256.interop import cross_validate, qualtran_available
from qsha256.quantum.ancilla_check import AncillaLeak
from qsha256.quantum.keccak.keccak import (
    KECCAK_ROUND_CONSTANTS,
    LANE_BITS,
    LANES,
    build_keccak_f,
    keccak_f,
    sha3_256,
)
from qsha256.quantum.primitives.boolean import ch_word_into
from qsha256.quantum.registers import CircuitBuilder
from qsha256.quantum.resources import analyze, compare_layouts, lattice_surgery_layout
from qsha256.quantum.sha256.compression import build_compression
from qsha256.quantum.strategies import Strategy
from qsha256.validation.basis_sim import BasisSimulator


class TestSHA512:
    def test_constants_derive_correctly(self):
        assert SHA512.k[0] == 0x428A2F98D728AE22
        assert SHA512.k[79] == 0x6C44198C4A475817
        assert SHA512.h0[0] == 0x6A09E667F3BCC908

    @pytest.mark.parametrize("length", [0, 1, 111, 112, 128, 300])
    def test_classical_matches_hashlib(self, length):
        message = b"a" * length
        assert sha_generic(message, SHA512) == hashlib.sha512(message).digest()

    def test_padding_uses_a_128_bit_length_field(self):
        assert SHA512.length_field_bytes == 16
        assert SHA256.length_field_bytes == 8
        assert len(pad_message(b"a" * 200, SHA512)) % 128 == 0

    @pytest.mark.slow
    def test_quantum_circuit_matches_hashlib(self):
        blocks = parse_blocks(pad_message(b"abc", SHA512), SHA512)
        comp = build_compression(SHA512, Strategy(), initial_state=SHA512.h0)
        sim = BasisSimulator(comp.circuit)
        out, _ = sim.run(sim.load(dict(zip(comp.message, blocks[0]))))
        digest = b"".join(sim.read(out, r).to_bytes(8, "big") for r in comp.state)
        assert digest == hashlib.sha512(b"abc").digest()


class TestKeccak:
    def test_round_constants_derive_correctly(self):
        assert KECCAK_ROUND_CONSTANTS[0] == 0x1
        assert KECCAK_ROUND_CONSTANTS[1] == 0x8082
        assert KECCAK_ROUND_CONSTANTS[23] == 0x8000000080008008

    @pytest.mark.parametrize("length", [0, 1, 134, 135, 136, 137, 200])
    def test_classical_matches_hashlib(self, length):
        """135 is the pad10*1 edge case a naive implementation gets wrong."""
        message = b"a" * length
        assert sha3_256(message) == hashlib.sha3_256(message).digest()

    @pytest.mark.parametrize("rounds", [1, 2, 4])
    def test_circuit_matches_classical(self, rounds):
        kc = build_keccak_f(rounds=rounds)
        sim = BasisSimulator(kc.circuit)
        rng = random.Random(rounds)
        state = [[rng.getrandbits(LANE_BITS) for _ in range(LANES)] for _ in range(LANES)]
        out, _ = sim.run(
            sim.load({kc.state[x][y]: state[x][y] for x in range(LANES) for y in range(LANES)})
        )
        got = [[sim.read(out, kc.output[x][y]) for y in range(LANES)] for x in range(LANES)]
        assert got == keccak_f([row[:] for row in state], rounds)

    @pytest.mark.slow
    def test_full_permutation_matches_classical(self):
        kc = build_keccak_f(rounds=24)
        sim = BasisSimulator(kc.circuit)
        rng = random.Random(1)
        state = [[rng.getrandbits(LANE_BITS) for _ in range(LANES)] for _ in range(LANES)]
        out, _ = sim.run(
            sim.load({kc.state[x][y]: state[x][y] for x in range(LANES) for y in range(LANES)})
        )
        got = [[sim.read(out, kc.output[x][y]) for y in range(LANES)] for x in range(LANES)]
        assert got == keccak_f([row[:] for row in state], 24)

    def test_rho_and_pi_are_free(self):
        """The linear steps must contribute no non-linear gates."""
        one = build_keccak_f(rounds=1).circuit.count_ops()
        two = build_keccak_f(rounds=2).circuit.count_ops()
        # Each round adds exactly the same non-linear cost.
        assert two.get("ccx", 0) == 2 * one.get("ccx", 0)


class TestBitcoin:
    def test_genesis_block(self):
        merkle = bytes.fromhex("3ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a")
        header = BlockHeader(
            version=1,
            prev_block=b"\x00" * 32,
            merkle_root=merkle,
            timestamp=1231006505,
            bits=0x1D00FFFF,
            nonce=2083236893,
        )
        digest = double_sha256(header.to_bytes())
        assert digest[::-1].hex() == (
            "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
        )
        assert leading_zero_bits(digest) == 43

    def test_midstate_splits_the_header(self):
        header = BlockHeader().to_bytes()
        state, block1 = midstate(header)
        assert len(state) == 8
        assert len(block1) == 16

    def test_midstate_rejects_a_wrong_length_header(self):
        with pytest.raises(ValueError, match="exactly 80 bytes"):
            midstate(b"short")

    @pytest.mark.slow
    def test_oracle_builds_and_search_space_is_the_nonce(self):
        oracle = build_mining_oracle(difficulty_bits=8, rounds=4)
        assert oracle.search_qubits == 32  # one 32-bit nonce word
        assert oracle.circuit.num_qubits > 0


class TestLayout:
    def test_buying_area_buys_time(self):
        comp = build_compression(SHA256, Strategy(), rounds=4)
        report = analyze(comp, spec=SHA256, rounds=4, transpile_t=False)
        estimates = {e.layout: e for e in compare_layouts(report)}
        assert estimates["fast"].tiles > estimates["compact"].tiles
        assert estimates["fast"].code_cycles <= estimates["compact"].code_cycles

    def test_assumptions_are_stated(self):
        comp = build_compression(SHA256, Strategy(), rounds=2)
        report = analyze(comp, spec=SHA256, rounds=2, transpile_t=False)
        estimate = lattice_surgery_layout(report, "intermediate")
        text = " ".join(estimate.assumptions).lower()
        assert "litinski" in text
        assert "no hardware was involved" in text
        assert estimate.provenance == "ASSUMPTION-DEPENDENT"

    def test_unknown_layout_is_rejected(self):
        from qsha256.quantum.resources.layout import get_layout

        with pytest.raises(KeyError, match="unknown layout"):
            get_layout("teleporting")


class TestCrossValidation:
    def test_estimators_agree(self):
        comp = build_compression(SHA256, Strategy(), rounds=2)
        result = cross_validate(comp.circuit, "sha256 r=2")
        assert result.agree, result.agreements

    def test_gidney_circuit_also_agrees(self):
        comp = build_compression(SHA256, Strategy(adder="gidney"), rounds=2)
        assert cross_validate(comp.circuit, "gidney r=2").agree

    def test_qasm_counter_ignores_gate_definitions(self):
        """It must count invocations, not the bodies of custom gate blocks."""
        from qsha256.interop import count_via_qasm_text, count_via_qiskit

        comp = build_compression(SHA256, Strategy(adder="gidney"), rounds=1)
        assert (
            count_via_qasm_text(comp.circuit).total_gates
            == count_via_qiskit(comp.circuit).total_gates
        )

    @pytest.mark.skipif(not qualtran_available(), reason="qualtran not installed")
    def test_qualtran_confirms_the_toffoli_count(self):
        from qsha256.interop import count_via_qualtran

        comp = build_compression(SHA256, Strategy(), rounds=2)
        theirs = count_via_qualtran(comp.circuit)
        assert theirs.available
        assert theirs.toffoli == comp.circuit.count_ops().get("ccx", 0)


class TestAncillaGuard:
    def test_correct_code_passes_without_a_solver_call(self):
        b = CircuitBuilder("ok", guard_ancillas=True)
        x, y, z = (b.add_word(8, c) for c in "xyz")
        with b.ancillas.borrow(8, "tmp") as tmp:
            ch_word_into(b, x, y, z, tmp)
            ch_word_into(b, x, y, z, tmp)
        assert b.guard.folded_clean == 1
        assert b.guard.solver_calls == 0

    def test_a_missing_uncompute_raises_at_the_release_site(self):
        b = CircuitBuilder("bad", guard_ancillas=True)
        x, y, z = (b.add_word(8, c) for c in "xyz")
        with pytest.raises(AncillaLeak, match="without being"):
            with b.ancillas.borrow(8, "tmp") as tmp:
                ch_word_into(b, x, y, z, tmp)

    @pytest.mark.parametrize("adder", ["cdkm", "vbe", "gidney"])
    def test_adders_pass_the_guard(self, adder):
        from qsha256.quantum.primitives.add import add_into

        b = CircuitBuilder(adder, guard_ancillas=True)
        a, t = b.add_word(16, "a"), b.add_word(16, "b")
        add_into(b, a, t, adder)
        assert b.guard.checked >= 1

    def test_guard_is_off_by_default(self):
        assert CircuitBuilder("plain").guard is None
