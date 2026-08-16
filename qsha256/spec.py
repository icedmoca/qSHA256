"""Parameterised specification of the SHA-256 family.

Everything in qSHA256 -- the classical reference model, the reversible quantum
circuits, and the validation harness -- is written against a :class:`ShaSpec`
rather than against hard-coded 32-bit constants.

That has one purpose: **testability**.  A full 32-bit SHA-256 circuit is far too
wide for exhaustive verification, but the *same code path* instantiated with 4-bit
words produces a circuit narrow enough to check against every input.  When a toy
spec and the real spec share an implementation, exhaustive tests on the toy are
meaningful evidence about the real circuit's structure.

Toy specs are **not** SHA-256 and are never presented as such.  They are reduced
models with the same algebraic architecture (same round equations, same message
schedule recurrence, same constant-derivation rule) and smaller parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "ShaSpec",
    "SHA256",
    "TOY4",
    "TOY8",
    "SPECS",
    "get_spec",
    "Term",
    "integer_root",
    "frac_bits_of_root",
    "first_primes",
]

# ``("rotr", 7)`` means ROTR^7(x); ``("shr", 3)`` means SHR^3(x).
Term = tuple[Literal["rotr", "shr"], int]


def first_primes(n: int) -> list[int]:
    """The first ``n`` primes, by trial division (n is tiny; clarity beats speed)."""
    primes: list[int] = []
    candidate = 2
    while len(primes) < n:
        if all(candidate % p for p in primes if p * p <= candidate):
            primes.append(candidate)
        candidate += 1
    return primes


def integer_root(value: int, degree: int) -> int:
    """Exact ``floor(value ** (1 / degree))`` using integer arithmetic only.

    Floating point is deliberately avoided: ``2 ** (1/3)`` in double precision is
    not accurate enough to reproduce the published SHA-256 constants.
    """
    if value < 0:
        raise ValueError("integer_root requires a non-negative value")
    if value == 0:
        return 0
    guess = 1 << ((value.bit_length() + degree - 1) // degree)
    while True:
        nxt = ((degree - 1) * guess + value // guess ** (degree - 1)) // degree
        if nxt >= guess:
            return guess
        guess = nxt


def frac_bits_of_root(prime: int, degree: int, bits: int) -> int:
    """Top ``bits`` bits of the fractional part of ``prime ** (1 / degree)``.

    This is the rule FIPS 180-4 uses to derive the SHA-256 constants: ``K[t]`` is
    the first 32 fractional bits of the cube root of the ``t``-th prime, and
    ``H[i]`` the first 32 fractional bits of the square root of the ``i``-th prime.
    """
    scaled = integer_root(prime << (degree * bits), degree)
    return scaled & ((1 << bits) - 1)


@dataclass(frozen=True)
class ShaSpec:
    """A SHA-256-shaped compression function with configurable parameters."""

    name: str
    word_bits: int
    state_words: int
    block_words: int
    rounds: int
    big_sigma0: tuple[Term, ...]
    big_sigma1: tuple[Term, ...]
    small_sigma0: tuple[Term, ...]
    small_sigma1: tuple[Term, ...]
    #: True only for the genuine FIPS 180-4 SHA-256 parameter set.
    is_sha256: bool = False

    # -- derived -----------------------------------------------------------
    @property
    def mask(self) -> int:
        return (1 << self.word_bits) - 1

    @property
    def digest_bits(self) -> int:
        return self.state_words * self.word_bits

    @property
    def block_bits(self) -> int:
        return self.block_words * self.word_bits

    @property
    def k(self) -> tuple[int, ...]:
        """Round constants: fractional bits of cube roots of the first primes."""
        primes = first_primes(self.rounds)
        return tuple(frac_bits_of_root(p, 3, self.word_bits) for p in primes)

    @property
    def h0(self) -> tuple[int, ...]:
        """Initial chaining value: fractional bits of square roots of the first primes."""
        primes = first_primes(self.state_words)
        return tuple(frac_bits_of_root(p, 2, self.word_bits) for p in primes)

    def validate(self) -> None:
        """Raise if the parameters cannot describe a well-formed compression function."""
        if self.word_bits < 2:
            raise ValueError("word_bits must be at least 2")
        if self.state_words != 8:
            raise ValueError("the SHA-256 round structure requires exactly 8 state words")
        if self.block_words < 2:
            raise ValueError("block_words must be at least 2")
        if self.rounds < self.block_words:
            raise ValueError("rounds must be at least block_words")
        for label, terms in (
            ("big_sigma0", self.big_sigma0),
            ("big_sigma1", self.big_sigma1),
            ("small_sigma0", self.small_sigma0),
            ("small_sigma1", self.small_sigma1),
        ):
            if not terms:
                raise ValueError(f"{label} needs at least one term")
            for kind, amount in terms:
                if kind not in ("rotr", "shr"):
                    raise ValueError(f"{label}: unknown term kind {kind!r}")
                if not 0 < amount < self.word_bits:
                    raise ValueError(
                        f"{label}: {kind}^{amount} is out of range for {self.word_bits}-bit words"
                    )

    def with_rounds(self, rounds: int) -> ShaSpec:
        """A copy truncated (or extended) to ``rounds`` compression rounds.

        Reduced-round variants are the project's primary scaling knob; they are
        genuine prefixes of the real round sequence, sharing ``K[0..rounds-1]``.
        """
        if rounds < self.block_words:
            raise ValueError(f"{self.name} needs at least {self.block_words} rounds")
        if rounds == self.rounds:
            return self
        suffix = "" if self.is_sha256 else "-"
        return ShaSpec(
            name=f"{self.name}{suffix}r{rounds}",
            word_bits=self.word_bits,
            state_words=self.state_words,
            block_words=self.block_words,
            rounds=rounds,
            big_sigma0=self.big_sigma0,
            big_sigma1=self.big_sigma1,
            small_sigma0=self.small_sigma0,
            small_sigma1=self.small_sigma1,
            # A truncated round count is no longer FIPS 180-4 SHA-256.
            is_sha256=False,
        )


#: FIPS 180-4 SHA-256.  ``K`` and ``H0`` are derived, not transcribed; the test
#: suite checks the derivation against the published tables.
SHA256 = ShaSpec(
    name="sha256",
    word_bits=32,
    state_words=8,
    block_words=16,
    rounds=64,
    big_sigma0=(("rotr", 2), ("rotr", 13), ("rotr", 22)),
    big_sigma1=(("rotr", 6), ("rotr", 11), ("rotr", 25)),
    small_sigma0=(("rotr", 7), ("rotr", 18), ("shr", 3)),
    small_sigma1=(("rotr", 17), ("rotr", 19), ("shr", 10)),
    is_sha256=True,
)

#: 8-bit reduced model.  Rotation amounts are the SHA-256 amounts reduced modulo
#: the word size, de-duplicated so each sigma stays a sum of distinct terms.
TOY8 = ShaSpec(
    name="toy8",
    word_bits=8,
    state_words=8,
    block_words=4,
    rounds=8,
    big_sigma0=(("rotr", 2), ("rotr", 5), ("rotr", 6)),
    big_sigma1=(("rotr", 6), ("rotr", 3), ("rotr", 1)),
    small_sigma0=(("rotr", 7), ("rotr", 2), ("shr", 3)),
    small_sigma1=(("rotr", 1), ("rotr", 3), ("shr", 2)),
)

#: 4-bit reduced model -- small enough that every primitive can be checked over
#: its entire input space, and a full compression round can be simulated exactly.
TOY4 = ShaSpec(
    name="toy4",
    word_bits=4,
    state_words=8,
    block_words=4,
    rounds=8,
    big_sigma0=(("rotr", 1), ("rotr", 2), ("rotr", 3)),
    big_sigma1=(("rotr", 1), ("rotr", 3)),
    small_sigma0=(("rotr", 1), ("shr", 1)),
    small_sigma1=(("rotr", 2), ("shr", 2)),
)

SPECS: dict[str, ShaSpec] = {s.name: s for s in (SHA256, TOY8, TOY4)}

for _spec in SPECS.values():
    _spec.validate()


def get_spec(name: str) -> ShaSpec:
    try:
        return SPECS[name]
    except KeyError:
        raise KeyError(f"unknown spec {name!r}; available: {sorted(SPECS)}") from None
