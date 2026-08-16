"""Reversible message schedule.

    W[t] = sigma1(W[t-2]) + W[t-7] + sigma0(W[t-15]) + W[t-16]   (mod 2^32)

Classically this is a cheap loop.  Reversibly it is one of the two places where
architecture choices swing the cost by hundreds of qubits, because the naive
reading -- "materialise all 64 words" -- means 64 * 32 = 2048 data qubits that
exist only to be uncomputed later.

Two strategies are implemented, and the benchmark measures rather than assumes
which is better:

``store_all``
    Every ``W[t]`` gets its own register.  Because the target starts in ``|0>``,
    the *first* term of the recurrence is a free CNOT copy instead of a modular
    addition -- so this strategy needs only **three** adders per word and one
    temporary for ``sigma0``.  Costs ``(rounds - 16) * 32`` extra data qubits.

``rolling``
    Only 16 registers exist.  The register holding ``W[t-16]`` is transformed
    **in place** into ``W[t]``: since ``W[t-16]`` is an addend of the recurrence,
    accumulating the other three terms into it is exactly the right answer, and
    the transformation is reversible.  Also three adders per word, but now both
    sigma terms need a temporary, costing an extra sigma compute/uncompute pair
    per word.

The interesting result is that both strategies use the *same number of adders*.
They differ in CNOTs (``rolling`` pays two extra sigma folds per word) and in
qubits (``store_all`` pays 1536 extra qubits for SHA-256).  That is a genuine,
measured qubit-versus-gate tradeoff rather than a folk assumption.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ...classical.sha256 import schedule_offsets
from ...spec import SHA256, ShaSpec
from ..strategies import DEFAULT, Strategy
from ..primitives.add import add_into
from ..primitives.xor import xor_terms
from ..registers import CircuitBuilder, Word
from .functions import small_sigma0_into, small_sigma1_into

__all__ = ["MessageSchedule", "StoreAllSchedule", "RollingSchedule", "build_schedule"]


class MessageSchedule(ABC):
    """Provides the register holding ``W[t]`` at the moment round ``t`` needs it."""

    def __init__(
        self,
        b: CircuitBuilder,
        spec: ShaSpec = SHA256,
        strategy: Strategy = DEFAULT,
        message: list[Word] | None = None,
    ):
        self.b = b
        self.spec = spec
        self.strategy = strategy
        self.o16, self.o15, self.o7, self.o2 = schedule_offsets(spec)
        self.message: list[Word] = message if message is not None else self._alloc_message()

    def _alloc_message(self) -> list[Word]:
        return self.b.add_words(self.spec.block_words, self.spec.word_bits, "W")

    @abstractmethod
    def word(self, t: int) -> Word:
        """Register holding ``W[t]``, expanding the schedule on demand."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    def _sigma_add(self, target: Word, source: Word, which: str) -> None:
        """``target += sigma_k(source)`` via a borrowed temporary.

        The temporary is computed, consumed by the adder, then uncomputed by
        replaying the same self-inverse CNOT fold -- so it returns to ``|0>``
        and goes back to the ancilla pool for the next word.
        """
        terms = self.spec.small_sigma0 if which == "0" else self.spec.small_sigma1
        with self.b.ancillas.borrow(self.spec.word_bits, f"sig{which}") as tmp:
            xor_terms(self.b, source, terms, tmp)
            add_into(self.b, tmp, target, self.strategy.adder)
            xor_terms(self.b, source, terms, tmp)  # uncompute: XOR is self-inverse


class StoreAllSchedule(MessageSchedule):
    """One dedicated register per schedule word."""

    name = "store_all"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._words: list[Word] = list(self.message)
        self._built = len(self._words)

    def word(self, t: int) -> Word:
        while self._built <= t:
            self._expand(self._built)
            self._built += 1
        return self._words[t]

    def _expand(self, t: int) -> None:
        b, spec = self.b, self.spec
        w = self._words
        with b.section(f"schedule[{t}]"):
            target = b.add_word(spec.word_bits, f"W{t}")
            w.append(target)
            # target is |0>, so the first term is a copy, not an addition.
            xor_terms(b, w[t - self.o2], spec.small_sigma1, target)
            add_into(b, w[t - self.o7], target, self.strategy.adder)
            self._sigma_add(target, w[t - self.o15], "0")
            add_into(b, w[t - self.o16], target, self.strategy.adder)


class RollingSchedule(MessageSchedule):
    """A 16-word window; ``W[t-16]``'s register becomes ``W[t]`` in place."""

    name = "rolling"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        m = self.spec.block_words
        if max(self.o15, self.o7, self.o2) >= m:
            raise ValueError(
                f"rolling schedule needs block_words > {max(self.o15, self.o7, self.o2)}"
            )
        self._window = list(self.message)
        self._ready = m - 1  # highest t currently materialised

    def word(self, t: int) -> Word:
        while self._ready < t:
            self._advance(self._ready + 1)
            self._ready += 1
        if t < self._ready - self.spec.block_words + 1:
            raise ValueError(f"W[{t}] has already been overwritten by the rolling window")
        return self._window[t % self.spec.block_words]

    def _advance(self, t: int) -> None:
        b, spec = self.b, self.spec
        m = spec.block_words
        win = self._window
        with b.section(f"schedule[{t}]"):
            # This register currently holds W[t-16]; accumulate the other three
            # terms into it and it holds W[t].
            target = win[t % m]
            self._sigma_add(target, win[(t - self.o2) % m], "1")
            add_into(b, win[(t - self.o7) % m], target, self.strategy.adder)
            self._sigma_add(target, win[(t - self.o15) % m], "0")


def build_schedule(
    b: CircuitBuilder,
    spec: ShaSpec = SHA256,
    strategy: Strategy = DEFAULT,
    message: list[Word] | None = None,
) -> MessageSchedule:
    """Instantiate the schedule strategy named by ``strategy.schedule``."""
    cls = {"store_all": StoreAllSchedule, "rolling": RollingSchedule}[strategy.schedule]
    return cls(b, spec, strategy, message)
