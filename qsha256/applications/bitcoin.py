"""Bitcoin's double SHA-256, and what a quantum attack on it would actually cost.

Bitcoin proof-of-work is the most-quoted quantum target in existence, and the
public numbers for it are almost uniformly garbage -- usually ``2^128``
handwaved from Grover's query count, with no circuit underneath.  qSHA256 has
the machinery to do it properly, so this module does.

The target
----------

A miner searches for a nonce such that::

    SHA256(SHA256(block_header)) < difficulty_target

Three details make this cheaper than "two SHA-256 evaluations":

**The midstate is free.**  The header is 80 bytes, which pads to two 512-bit
blocks.  The first block is entirely determined by the version, previous block
hash and Merkle root -- none of which the nonce touches.  So its compression can
be done *classically*, once, and folded into the circuit as the initial chaining
value.  Only the second block is searched over.  Real miners do this; a quantum
attack would too, and an estimate that charges for it is overcharging.

**The second hash is a fixed one-block message.**  Its input is the 32-byte
digest of the first, padded to exactly one 512-bit block with constants. Those
padding words are loaded with X gates rather than occupying input qubits.

**The predicate is a threshold, not an equality.**  Mining does not look for a
specific digest; it looks for one below a target, which in practice means a
run of leading zero bits.  Comparing against ``d`` leading zeros needs an AND
tree over ``d`` bits, not 256 -- much cheaper than a preimage oracle, and it
means the number of solutions is enormous rather than one, which changes the
Grover iteration count.

What this does not claim
------------------------

Nothing here breaks Bitcoin, and the numbers say the opposite of the usual
headline.  Grover on this oracle is deeply serial, the iteration count is
astronomically beyond any depth budget, and the classical network already
computes ~10^20 hashes per second in parallel -- which Grover cannot match,
because it parallelises only as ``sqrt``.  The point of computing the real
circuit cost is to make that concrete rather than rhetorical.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from ..classical.sha256 import compress, pad_message, parse_blocks, sha256
from ..quantum.oracle.predicate import digest_bits
from ..quantum.primitives.boolean import and_tree_ancilla_count, and_tree_mcx
from ..quantum.registers import CircuitBuilder, Word
from ..quantum.sha256.compression import build_compression
from ..quantum.strategies import ORACLE, Strategy
from ..spec import SHA256

__all__ = [
    "BitcoinOracle",
    "BlockHeader",
    "build_mining_oracle",
    "double_sha256",
    "leading_zero_bits",
    "midstate",
]


@dataclass
class BlockHeader:
    """An 80-byte Bitcoin block header."""

    version: int = 0x20000000
    prev_block: bytes = b"\x00" * 32
    merkle_root: bytes = b"\x00" * 32
    timestamp: int = 0
    bits: int = 0x1D00FFFF
    nonce: int = 0

    def to_bytes(self) -> bytes:
        return (
            struct.pack("<I", self.version)
            + self.prev_block
            + self.merkle_root
            + struct.pack("<III", self.timestamp, self.bits, self.nonce)
        )

    def __len__(self) -> int:
        return 80


def double_sha256(data: bytes) -> bytes:
    """``SHA256(SHA256(data))``, the Bitcoin hash."""
    return sha256(sha256(data))


def leading_zero_bits(digest: bytes) -> int:
    """Leading zero bits of a digest, read the way Bitcoin displays it.

    Bitcoin compares the digest as a little-endian integer, so the leading zeros
    that miners talk about are at the *end* of the byte string.
    """
    value = int.from_bytes(digest, "little")
    return 256 - value.bit_length() if value else 256


def midstate(header: bytes) -> tuple[tuple[int, ...], list[int]]:
    """Split a header into the classically-computable midstate and the searched block.

    Returns ``(chaining_value_after_block_0, block_1_words)``.  The nonce lives
    in block 1, so block 0's compression is done once on a classical machine and
    never enters the circuit.
    """
    if len(header) != 80:
        raise ValueError("a Bitcoin header is exactly 80 bytes")
    blocks = parse_blocks(pad_message(header), SHA256)
    if len(blocks) != 2:
        raise ValueError("an 80-byte header must pad to exactly two blocks")
    state = compress(tuple(SHA256.h0), blocks[0], SHA256)
    return state, blocks[1]


@dataclass
class BitcoinOracle:
    """A built mining oracle plus the registers a caller needs."""

    builder: CircuitBuilder
    #: The searched block's message registers; the nonce lives in word 3.
    message: list[Word]
    #: Word indices pinned to classical values (everything but the nonce).
    fixed_words: dict[int, int]
    difficulty_bits: int
    rounds: int
    midstate: tuple[int, ...]

    @property
    def circuit(self):
        return self.builder.circuit

    @property
    def search_qubits(self) -> int:
        return sum(len(w) for i, w in enumerate(self.message) if i not in self.fixed_words)

    def to_dict(self) -> dict[str, Any]:
        ops = dict(self.circuit.count_ops())
        return {
            "qubits": self.circuit.num_qubits,
            "gates": sum(ops.values()),
            "gate_counts": ops,
            "difficulty_bits": self.difficulty_bits,
            "search_qubits": self.search_qubits,
            "rounds": self.rounds,
        }


def build_mining_oracle(
    header: BlockHeader | bytes | None = None,
    difficulty_bits: int = 32,
    strategy: Strategy = ORACLE,
    rounds: int = 64,
    search_words: tuple[int, ...] = (3,),
) -> BitcoinOracle:
    """Build the reversible mining predicate: ``leading_zeros(dSHA256(header)) >= d``.

    ``search_words`` names the message words left free.  Word 3 of the second
    block holds the nonce; miners also roll the timestamp and extranonce, which
    is why it is a parameter rather than a constant.

    Structure::

        SHA-256 on block 1 from the classical midstate  -> first digest
        SHA-256 on the padded first digest              -> second digest
        phase flip iff the top `difficulty_bits` are zero
        both hashes inverted, restoring every work register

    The whole thing is one Grover oracle call.
    """
    header_bytes = (
        header.to_bytes()
        if isinstance(header, BlockHeader)
        else (header if header is not None else BlockHeader().to_bytes())
    )
    state0, block1 = midstate(header_bytes)

    b = CircuitBuilder(f"bitcoin_oracle_d{difficulty_bits}")
    message = b.add_words(SHA256.block_words, SHA256.word_bits, "M")
    fixed = {i: word for i, word in enumerate(block1) if i not in search_words}

    start = len(b.circuit.data)

    with b.section("first SHA-256 (from classical midstate)"):
        first = build_compression(
            SHA256,
            strategy,
            rounds=rounds,
            initial_state=state0,
            message_constants=fixed,
            builder=b,
            message=message,
            output="digest",
            uncompute=False,
        )

    # The second hash's message is the first digest padded to one block: the
    # 0x80 marker and the 256-bit length are constants loaded with X gates.
    with b.section("second SHA-256 (over the padded first digest)"):
        second_message: list[Word] = list(first.digest)
        pad_words = b.add_words(8, SHA256.word_bits, "P")
        second_message += pad_words
        second = build_compression(
            SHA256,
            strategy,
            rounds=rounds,
            initial_state=SHA256.h0,
            message_constants={8: 0x80000000, 15: 256},
            builder=b,
            message=second_message,
            output="digest",
            uncompute=False,
        )

    end = len(b.circuit.data)

    with b.section(f"difficulty predicate (>= {difficulty_bits} leading zero bits)"):
        # Bitcoin reads the digest little-endian, so the leading zeros are the
        # high bits of the last words. Require the top `difficulty_bits` to be 0.
        bits = digest_bits(second.digest)
        checked = bits[-difficulty_bits:] if difficulty_bits else []
        need = and_tree_ancilla_count(len(checked))
        with b.ancillas.borrow(need + 1, "diff") as anc:
            marker, tree = anc[0], list(anc.qubits[1:])
            for bit in checked:
                b.x(bit)
            and_tree_mcx(b, checked, marker, tree)
            b.z(marker)
            and_tree_mcx(b, checked, marker, tree)
            for bit in checked:
                b.x(bit)

    with b.section("inverse of both hashes"):
        b.append_reversed(start, end)

    return BitcoinOracle(
        builder=b,
        message=message,
        fixed_words=fixed,
        difficulty_bits=difficulty_bits,
        rounds=rounds,
        midstate=state0,
    )
