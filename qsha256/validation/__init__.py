"""Validation harness: exact basis-state simulation and classical/quantum comparison."""

from .basis_sim import BasisSimulator, UnsupportedGate, simulate_basis
from .suite import Check, ValidationReport, run_validation
from .vectors import NIST_VECTORS, PADDING_BOUNDARY_LENGTHS

__all__ = [
    "NIST_VECTORS",
    "PADDING_BOUNDARY_LENGTHS",
    "BasisSimulator",
    "Check",
    "UnsupportedGate",
    "ValidationReport",
    "run_validation",
    "simulate_basis",
]
