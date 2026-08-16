"""Grover oracle construction: digest comparison, preimage oracle, toy demonstrations."""

from .grover import (
    GroverCostEstimate,
    build_toy_grover,
    diffusion,
    grover_cost_estimate,
    grover_iterations,
)
from .predicate import equality_ancilla_count, equality_phase_flip
from .preimage import PreimageOracle, build_preimage_oracle
from .toy import TOY_TINY, ToySpec, build_toy_hash, toy_compress

__all__ = [
    "GroverCostEstimate",
    "PreimageOracle",
    "TOY_TINY",
    "ToySpec",
    "build_preimage_oracle",
    "build_toy_grover",
    "build_toy_hash",
    "diffusion",
    "equality_ancilla_count",
    "equality_phase_flip",
    "grover_cost_estimate",
    "grover_iterations",
    "toy_compress",
]
