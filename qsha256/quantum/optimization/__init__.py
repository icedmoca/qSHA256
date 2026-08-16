"""Design-space search, gate-level rewriting, equivalence verification, hardware models."""

from ..strategies import (
    DEFAULT,
    MIN_DEPTH,
    MIN_QUBITS,
    PRESETS,
    STRATEGY_AXES,
    Strategy,
    enumerate_strategies,
    get_preset,
)
from .hardware import HardwareRanking, ScoredDesign, rank_for_hardware
from .phase_fold import PhaseFoldResult, phase_fold, to_clifford_t
from .rewrite import RewriteResult, apply_rewrites
from .search import DesignPoint, SearchResult, compare_designs, pareto_front, search_designs
from .verify import EquivalenceResult, check_equivalence, verify_against_classical

__all__ = [
    "DEFAULT",
    "MIN_DEPTH",
    "MIN_QUBITS",
    "PRESETS",
    "STRATEGY_AXES",
    "DesignPoint",
    "EquivalenceResult",
    "HardwareRanking",
    "PhaseFoldResult",
    "RewriteResult",
    "ScoredDesign",
    "SearchResult",
    "Strategy",
    "apply_rewrites",
    "check_equivalence",
    "compare_designs",
    "enumerate_strategies",
    "get_preset",
    "pareto_front",
    "phase_fold",
    "rank_for_hardware",
    "search_designs",
    "to_clifford_t",
    "verify_against_classical",
]
