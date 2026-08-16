"""Shared fixtures and helpers for the qSHA256 test suite."""

from __future__ import annotations

import random

import pytest

from qsha256.quantum.registers import CircuitBuilder
from qsha256.validation.basis_sim import BasisSimulator


@pytest.fixture
def rng() -> random.Random:
    """A seeded RNG, so a failure is always reproducible."""
    return random.Random(20240816)


@pytest.fixture
def builder() -> CircuitBuilder:
    return CircuitBuilder("test")


def run_circuit(builder: CircuitBuilder, assignments: dict) -> tuple[BasisSimulator, list[int]]:
    """Execute a builder's circuit on a basis state given as ``{Word: value}``."""
    sim = BasisSimulator(builder.circuit)
    out, _ = sim.run(sim.load(assignments))
    return sim, out


def assert_ancillas_clean(builder: CircuitBuilder, sim: BasisSimulator, out: list[int]) -> None:
    """The ancilla pool recycles qubits, so every borrower must return them to |0>."""
    dirty = [q for q in builder.ancillas.all if out[sim.index_of(q)]]
    assert not dirty, f"{len(dirty)} recycled work qubit(s) were not returned to |0>"
