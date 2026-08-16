"""Logical resource analysis, Clifford+T costing and report rendering."""

from .analyzer import Provenance, ResourceReport, analyze, environment_metadata
from .clifford_t import TOFFOLI_MODELS, ToffoliModel, clifford_t_cost, get_model, rz_t_count
from .depth import DepthMetrics, measure_depth
from .gates import GateCounts, count_ops
from .leaderboard import PUBLISHED, build_leaderboard, render_leaderboard
from .physical import (
    HARDWARE_MODELS,
    HardwareModel,
    PhysicalEstimate,
    estimate_physical,
    get_hardware_model,
)
from .reports import log2_str, pow2_str, render, to_csv, to_json, to_markdown

__all__ = [
    "HARDWARE_MODELS",
    "PUBLISHED",
    "TOFFOLI_MODELS",
    "DepthMetrics",
    "GateCounts",
    "HardwareModel",
    "PhysicalEstimate",
    "Provenance",
    "ResourceReport",
    "ToffoliModel",
    "analyze",
    "build_leaderboard",
    "clifford_t_cost",
    "count_ops",
    "environment_metadata",
    "estimate_physical",
    "get_hardware_model",
    "get_model",
    "log2_str",
    "measure_depth",
    "pow2_str",
    "render",
    "render_leaderboard",
    "rz_t_count",
    "to_csv",
    "to_json",
    "to_markdown",
]
