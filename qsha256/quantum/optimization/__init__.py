"""Design-space search, gate-level rewriting, equivalence verification, hardware models."""

from .rewrite import RewriteResult, apply_rewrites
from .search import DesignPoint, SearchResult, compare_designs, pareto_front, search_designs
from .strategies import (
    DEFAULT,
    MIN_DEPTH,
    MIN_QUBITS,
    PRESETS,
    STRATEGY_AXES,
    Strategy,
    enumerate_strategies,
    get_preset,
)
from .verify import EquivalenceResult, check_equivalence, verify_against_classical

__all__ = [
    "DEFAULT",
    "DesignPoint",
    "EquivalenceResult",
    "MIN_DEPTH",
    "MIN_QUBITS",
    "PRESETS",
    "RewriteResult",
    "STRATEGY_AXES",
    "SearchResult",
    "Strategy",
    "apply_rewrites",
    "check_equivalence",
    "compare_designs",
    "enumerate_strategies",
    "get_preset",
    "pareto_front",
    "search_designs",
    "verify_against_classical",
]
