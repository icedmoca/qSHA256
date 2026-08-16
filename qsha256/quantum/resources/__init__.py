"""Logical resource analysis, Clifford+T costing and report rendering."""

from .analyzer import Provenance, ResourceReport, analyze, environment_metadata
from .clifford_t import TOFFOLI_MODELS, ToffoliModel, clifford_t_cost, get_model, rz_t_count
from .depth import DepthMetrics, measure_depth
from .gates import GateCounts, count_ops
from .reports import log2_str, pow2_str, render, to_csv, to_json, to_markdown

from .physical import (
    HARDWARE_MODELS,
    HardwareModel,
    PhysicalEstimate,
    estimate_physical,
    get_hardware_model,
)

from .leaderboard import PUBLISHED, build_leaderboard, render_leaderboard

__all__ = [
    "PUBLISHED",
    "build_leaderboard",
    "render_leaderboard",
    "get_hardware_model",
    "estimate_physical",
    "PhysicalEstimate",
    "HardwareModel",
    "HARDWARE_MODELS",
    "DepthMetrics",
    "GateCounts",
    "Provenance",
    "ResourceReport",
    "TOFFOLI_MODELS",
    "ToffoliModel",
    "analyze",
    "clifford_t_cost",
    "count_ops",
    "environment_metadata",
    "get_model",
    "log2_str",
    "measure_depth",
    "pow2_str",
    "render",
    "rz_t_count",
    "to_csv",
    "to_json",
    "to_markdown",
]
