"""Classical SHA-256 reference model, generic over :class:`~qsha256.spec.ShaSpec`.

Every function here is the *ground truth* against which a reversible quantum
circuit is compared.  The functions are written to mirror FIPS 180-4 section 6.2
literally, one line of code per line of the standard, so that a reader can check
the model against the standard by eye before trusting it to validate circuits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..spec import SHA256, ShaSpec, Term

__all__ = [
    "RoundTrace",
    "add_mod",
    "big_sigma0",
    "big_sigma1",
    "ch",
    "compress",
    "compression_trace",
    "digest_from_state",
    "maj",
    "message_schedule",
    "pad_message",
    "parse_blocks",
    "rotr",
    "round_step",
    "sha256",
    "sha256_hex",
    "shr",
    "small_sigma0",
    "small_sigma1",
]


# --------------------------------------------------------------------------
# Word-level operations
# --------------------------------------------------------------------------


def rotr(x: int, n: int, bits: int) -> int:
    """Circular right rotation.  Bijective: no information is lost."""
    n %= bits
    mask = (1 << bits) - 1
    x &= mask
    return ((x >> n) | (x << (bits - n))) & mask


def shr(x: int, n: int, bits: int) -> int:
    """Logical right shift.  *Not* bijective -- the low ``n`` bits are discarded.

    The distinction from :func:`rotr` matters enormously in the reversible
    setting; see ``qsha256.quantum.primitives.shift``.
    """
    return (x & ((1 << bits) - 1)) >> n


def add_mod(bits: int, *values: int) -> int:
    """Addition modulo ``2 ** bits``."""
    mask = (1 << bits) - 1
    total = 0
    for v in values:
        total = (total + v) & mask
    return total


def ch(x: int, y: int, z: int) -> int:
    """``Ch(x, y, z) = (x AND y) XOR ((NOT x) AND z)`` -- a bitwise multiplexer."""
    return (x & y) ^ (~x & z)


def maj(x: int, y: int, z: int) -> int:
    """``Maj(x, y, z) = (x AND y) XOR (x AND z) XOR (y AND z)`` -- bitwise majority."""
    return (x & y) ^ (x & z) ^ (y & z)


def _apply_terms(x: int, terms: tuple[Term, ...], bits: int) -> int:
    """XOR-fold a sigma function's rotate/shift terms."""
    acc = 0
    for kind, amount in terms:
        acc ^= rotr(x, amount, bits) if kind == "rotr" else shr(x, amount, bits)
    return acc & ((1 << bits) - 1)


def big_sigma0(x: int, spec: ShaSpec = SHA256) -> int:
    """SHA-256: ``ROTR^2(x) XOR ROTR^13(x) XOR ROTR^22(x)``."""
    return _apply_terms(x, spec.big_sigma0, spec.word_bits)


def big_sigma1(x: int, spec: ShaSpec = SHA256) -> int:
    """SHA-256: ``ROTR^6(x) XOR ROTR^11(x) XOR ROTR^25(x)``."""
    return _apply_terms(x, spec.big_sigma1, spec.word_bits)


def small_sigma0(x: int, spec: ShaSpec = SHA256) -> int:
    """SHA-256: ``ROTR^7(x) XOR ROTR^18(x) XOR SHR^3(x)``."""
    return _apply_terms(x, spec.small_sigma0, spec.word_bits)


def small_sigma1(x: int, spec: ShaSpec = SHA256) -> int:
    """SHA-256: ``ROTR^17(x) XOR ROTR^19(x) XOR SHR^10(x)``."""
    return _apply_terms(x, spec.small_sigma1, spec.word_bits)


# --------------------------------------------------------------------------
# Padding and parsing
# --------------------------------------------------------------------------


def pad_message(message: bytes, spec: ShaSpec = SHA256) -> bytes:
    """FIPS 180-4 section 5.1.1 padding.

    Appends ``0x80``, then zero bytes, then the 64-bit big-endian bit length, so
    that the result is a whole number of 512-bit blocks.

    Only defined for the real 32-bit spec: byte-oriented padding is meaningless
    for toy word sizes, where the quantum circuits operate directly on
    pre-parsed word blocks instead.
    """
    if not spec.is_sha256:
        raise ValueError("byte-level padding is only defined for the sha256 spec")
    length_bits = len(message) * 8
    padded = bytearray(message)
    padded.append(0x80)
    while len(padded) % 64 != 56:
        padded.append(0x00)
    padded += length_bits.to_bytes(8, "big")
    return bytes(padded)


def parse_blocks(padded: bytes, spec: ShaSpec = SHA256) -> list[list[int]]:
    """Split padded data into blocks of ``spec.block_words`` big-endian words."""
    word_bytes = spec.word_bits // 8
    block_bytes = spec.block_words * word_bytes
    if len(padded) % block_bytes:
        raise ValueError(f"padded length {len(padded)} is not a multiple of {block_bytes}")
    blocks = []
    for off in range(0, len(padded), block_bytes):
        chunk = padded[off : off + block_bytes]
        blocks.append(
            [
                int.from_bytes(chunk[i : i + word_bytes], "big")
                for i in range(0, block_bytes, word_bytes)
            ]
        )
    return blocks


def digest_from_state(state: list[int] | tuple[int, ...], spec: ShaSpec = SHA256) -> bytes:
    """Serialise a chaining state to big-endian bytes."""
    word_bytes = (spec.word_bits + 7) // 8
    return b"".join(int(w).to_bytes(word_bytes, "big") for w in state)


# --------------------------------------------------------------------------
# Message schedule
# --------------------------------------------------------------------------


def message_schedule(block: list[int], spec: ShaSpec = SHA256) -> list[int]:
    """Expand one message block into ``spec.rounds`` schedule words.

    ``W[t] = sigma1(W[t-2]) + W[t-7] + sigma0(W[t-15]) + W[t-16]  (mod 2^w)``

    The back-references are expressed relative to ``block_words`` so that toy
    specs with fewer than 16 words per block use the analogous recurrence.
    """
    m = spec.block_words
    if len(block) != m:
        raise ValueError(f"block must contain {m} words, got {len(block)}")
    # Offsets 16, 15, 7, 2 for SHA-256; scaled proportionally for toy specs.
    o16, o15, o7, o2 = m, m - 1, max(2, m // 2 - 1), 2
    bits = spec.word_bits
    w = list(block)
    for t in range(m, spec.rounds):
        w.append(
            add_mod(
                bits,
                small_sigma1(w[t - o2], spec),
                w[t - o7],
                small_sigma0(w[t - o15], spec),
                w[t - o16],
            )
        )
    return w


def schedule_offsets(spec: ShaSpec = SHA256) -> tuple[int, int, int, int]:
    """The ``(16, 15, 7, 2)`` back-reference offsets used by :func:`message_schedule`."""
    m = spec.block_words
    return m, m - 1, max(2, m // 2 - 1), 2


# --------------------------------------------------------------------------
# Compression
# --------------------------------------------------------------------------


@dataclass
class RoundTrace:
    """Every intermediate value of a single compression round."""

    t: int
    state_in: tuple[int, ...]
    state_out: tuple[int, ...]
    w: int
    k: int
    big_sigma1_e: int
    ch_efg: int
    t1: int
    big_sigma0_a: int
    maj_abc: int
    t2: int


def round_step(
    state: tuple[int, ...],
    w_t: int,
    k_t: int,
    spec: ShaSpec = SHA256,
) -> tuple[tuple[int, ...], RoundTrace]:
    """One SHA-256 compression round, returning the new state and its trace.

    ::

        T1 = h + Sigma1(e) + Ch(e,f,g) + K[t] + W[t]
        T2 = Sigma0(a) + Maj(a,b,c)
        (a,b,c,d,e,f,g,h) <- (T1+T2, a, b, c, d+T1, e, f, g)
    """
    a, b, c, d, e, f, g, h = state
    bits = spec.word_bits

    s1 = big_sigma1(e, spec)
    chv = ch(e, f, g)
    t1 = add_mod(bits, h, s1, chv, k_t, w_t)

    s0 = big_sigma0(a, spec)
    mjv = maj(a, b, c)
    t2 = add_mod(bits, s0, mjv)

    new = (add_mod(bits, t1, t2), a, b, c, add_mod(bits, d, t1), e, f, g)
    trace = RoundTrace(
        t=-1,
        state_in=tuple(state),
        state_out=new,
        w=w_t,
        k=k_t,
        big_sigma1_e=s1,
        ch_efg=chv,
        t1=t1,
        big_sigma0_a=s0,
        maj_abc=mjv,
        t2=t2,
    )
    return new, trace


@dataclass
class CompressionTrace:
    """Full record of one block compression."""

    spec_name: str
    state_in: tuple[int, ...]
    block: tuple[int, ...]
    schedule: tuple[int, ...]
    rounds: list[RoundTrace] = field(default_factory=list)
    working_final: tuple[int, ...] = ()
    state_out: tuple[int, ...] = ()


def compression_trace(
    state: tuple[int, ...],
    block: list[int],
    spec: ShaSpec = SHA256,
) -> CompressionTrace:
    """Compress one block, recording every intermediate value."""
    if len(state) != spec.state_words:
        raise ValueError(f"state must contain {spec.state_words} words")
    w = message_schedule(block, spec)
    k = spec.k
    working = tuple(state)
    trace = CompressionTrace(
        spec_name=spec.name,
        state_in=tuple(state),
        block=tuple(block),
        schedule=tuple(w),
    )
    for t in range(spec.rounds):
        working, rt = round_step(working, w[t], k[t], spec)
        rt.t = t
        trace.rounds.append(rt)
    trace.working_final = working
    trace.state_out = tuple(
        add_mod(spec.word_bits, state[i], working[i]) for i in range(spec.state_words)
    )
    return trace


def compress(state: tuple[int, ...], block: list[int], spec: ShaSpec = SHA256) -> tuple[int, ...]:
    """Compress one block into the chaining state."""
    return compression_trace(state, block, spec).state_out


def sha256(message: bytes, spec: ShaSpec = SHA256) -> bytes:
    """Full SHA-256 of a byte string, via padding, parsing and block chaining."""
    state = tuple(spec.h0)
    for block in parse_blocks(pad_message(message, spec), spec):
        state = compress(state, block, spec)
    return digest_from_state(state, spec)


def sha256_hex(message: bytes, spec: ShaSpec = SHA256) -> str:
    return sha256(message, spec).hex()
