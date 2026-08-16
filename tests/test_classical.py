"""The classical reference model: it is the ground truth for everything else."""

from __future__ import annotations

import hashlib

import pytest

from qsha256.classical.sha256 import (
    add_mod,
    big_sigma0,
    big_sigma1,
    ch,
    compress,
    compression_trace,
    digest_from_state,
    maj,
    message_schedule,
    pad_message,
    parse_blocks,
    rotr,
    round_step,
    sha256,
    sha256_hex,
    shr,
    small_sigma0,
    small_sigma1,
)
from qsha256.spec import SHA256, TOY4, TOY8, first_primes, frac_bits_of_root, integer_root
from qsha256.validation.vectors import NIST_VECTORS, PADDING_BOUNDARY_LENGTHS


class TestConstants:
    """K and H0 are *derived* here, not transcribed, so the derivation is tested."""

    def test_k_matches_fips(self):
        assert SHA256.k[0] == 0x428A2F98
        assert SHA256.k[1] == 0x71374491
        assert SHA256.k[63] == 0xC67178F2
        assert len(SHA256.k) == 64

    def test_h0_matches_fips(self):
        assert SHA256.h0 == (
            0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
            0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19,
        )

    @pytest.mark.parametrize("value,degree", [(8, 3), (27, 3), (1000, 3), (144, 2), (2**60, 2)])
    def test_integer_root_is_exact_floor(self, value, degree):
        root = integer_root(value, degree)
        assert root**degree <= value < (root + 1) ** degree

    def test_first_primes(self):
        assert first_primes(10) == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]

    def test_frac_bits_uses_exact_arithmetic(self):
        # float(2 ** (1/3)) is not accurate enough to reproduce this.
        assert frac_bits_of_root(2, 3, 32) == 0x428A2F98


class TestWordOperations:
    @pytest.mark.parametrize("n", range(32))
    def test_rotr_is_bijective(self, n):
        seen = {rotr(x, n, 8) for x in range(256)} if n < 8 else None
        if seen is not None:
            assert len(seen) == 256

    def test_rotr_round_trips(self, rng):
        for _ in range(200):
            x, n = rng.getrandbits(32), rng.randrange(32)
            assert rotr(rotr(x, n, 32), 32 - n if n else 0, 32) == x

    def test_shr_discards_low_bits(self):
        assert shr(0b1011, 2, 4) == 0b10
        assert shr(0xFFFFFFFF, 10, 32) == 0x003FFFFF

    def test_shr_is_not_bijective(self):
        """The property that makes SHR interesting in a reversible circuit."""
        assert shr(0b100, 2, 4) == shr(0b101, 2, 4) == shr(0b110, 2, 4) == shr(0b111, 2, 4)

    def test_add_mod_wraps(self):
        assert add_mod(32, 0xFFFFFFFF, 1) == 0
        assert add_mod(32, 0xFFFFFFFF, 0xFFFFFFFF) == 0xFFFFFFFE
        assert add_mod(4, 9, 9, 9) == 27 % 16

    def test_ch_is_a_multiplexer(self):
        """Ch selects y where x is 1 and z where x is 0."""
        assert ch(0b1010, 0b1111, 0b0000) == 0b1010
        for x, y, z in [(0, 5, 9), (0xFFFFFFFF, 5, 9)]:
            assert ch(x, y, z) & 0xFFFFFFFF == (y if x else z)

    def test_maj_is_bitwise_majority(self):
        for x in range(2):
            for y in range(2):
                for z in range(2):
                    assert maj(x, y, z) == (x + y + z >= 2)

    def test_sigmas_match_definitions(self, rng):
        for _ in range(100):
            x = rng.getrandbits(32)
            assert big_sigma0(x) == rotr(x, 2, 32) ^ rotr(x, 13, 32) ^ rotr(x, 22, 32)
            assert big_sigma1(x) == rotr(x, 6, 32) ^ rotr(x, 11, 32) ^ rotr(x, 25, 32)
            assert small_sigma0(x) == rotr(x, 7, 32) ^ rotr(x, 18, 32) ^ shr(x, 3, 32)
            assert small_sigma1(x) == rotr(x, 17, 32) ^ rotr(x, 19, 32) ^ shr(x, 10, 32)


class TestPadding:
    @pytest.mark.parametrize("length", PADDING_BOUNDARY_LENGTHS)
    def test_padding_boundaries(self, length):
        padded = pad_message(b"a" * length)
        assert len(padded) % 64 == 0
        assert padded[length] == 0x80
        assert int.from_bytes(padded[-8:], "big") == length * 8

    def test_padding_rejects_toy_specs(self):
        with pytest.raises(ValueError, match="only defined for the sha256 spec"):
            pad_message(b"abc", TOY4)

    def test_parse_blocks_rejects_misaligned(self):
        with pytest.raises(ValueError, match="not a multiple"):
            parse_blocks(b"short")


class TestSha256:
    @pytest.mark.parametrize("message,expected", NIST_VECTORS)
    def test_public_vectors(self, message, expected):
        assert sha256_hex(message) == expected

    @pytest.mark.parametrize("length", [0, 1, 55, 56, 64, 65, 200, 1000])
    def test_matches_hashlib(self, length):
        message = b"x" * length
        assert sha256(message) == hashlib.sha256(message).digest()

    def test_matches_hashlib_on_random_input(self, rng):
        for _ in range(50):
            data = bytes(rng.getrandbits(8) for _ in range(rng.randrange(300)))
            assert sha256(data) == hashlib.sha256(data).digest()

    def test_multi_block_chaining(self):
        """Messages spanning several blocks exercise the chaining state."""
        for length in (64, 119, 128, 200, 512):
            message = bytes(range(256)) * 4
            message = message[:length]
            assert sha256(message) == hashlib.sha256(message).digest()


class TestIntermediates:
    """The reference model must expose intermediates, or it cannot validate circuits."""

    def test_schedule_length_and_recurrence(self):
        block = list(range(16))
        w = message_schedule(block, SHA256)
        assert len(w) == 64
        assert w[:16] == block
        for t in range(16, 64):
            assert w[t] == add_mod(
                32, small_sigma1(w[t - 2]), w[t - 7], small_sigma0(w[t - 15]), w[t - 16]
            )

    def test_round_step_permutes_state(self):
        state = tuple(range(1, 9))
        new, trace = round_step(state, 0xDEADBEEF, SHA256.k[0])
        # b,c,d and f,g,h are the previous a,b,c and e,f,g -- pure renaming.
        assert new[1:4] == state[0:3]
        assert new[5:8] == state[4:7]
        assert new[0] == add_mod(32, trace.t1, trace.t2)
        assert new[4] == add_mod(32, state[3], trace.t1)

    def test_compression_trace_is_complete(self):
        trace = compression_trace(tuple(SHA256.h0), list(range(16)), SHA256)
        assert len(trace.rounds) == 64
        assert len(trace.schedule) == 64
        assert trace.rounds[0].t == 0 and trace.rounds[63].t == 63
        assert trace.state_out == compress(tuple(SHA256.h0), list(range(16)), SHA256)

    def test_digest_serialisation(self):
        assert digest_from_state(SHA256.h0, SHA256).hex().startswith("6a09e667")


class TestToySpecs:
    """Toy specs must be self-consistent, and must not claim to be SHA-256."""

    @pytest.mark.parametrize("spec", [TOY4, TOY8])
    def test_spec_validates(self, spec):
        spec.validate()
        assert not spec.is_sha256

    @pytest.mark.parametrize("spec", [TOY4, TOY8])
    def test_compression_is_deterministic(self, spec):
        block = [1] * spec.block_words
        assert compress(tuple(spec.h0), block, spec) == compress(tuple(spec.h0), block, spec)

    def test_reduced_rounds_is_not_sha256(self):
        assert SHA256.with_rounds(32).is_sha256 is False
        assert SHA256.with_rounds(64) is SHA256

    def test_reduced_rounds_share_the_constant_prefix(self):
        assert SHA256.with_rounds(8).k == SHA256.k[:8]
