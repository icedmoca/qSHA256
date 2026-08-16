"""Validation harness: exact basis-state simulation and classical/quantum comparison."""

from .basis_sim import BasisSimulator, UnsupportedGate, simulate_basis

__all__ = ["BasisSimulator", "UnsupportedGate", "simulate_basis"]
