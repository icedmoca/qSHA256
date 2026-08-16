"""Logical resource analysis, Clifford+T costing and report rendering."""

from .analyzer import Provenance, ResourceReport, analyze, environment_metadata
from .clifford_t import TOFFOLI_MODELS, ToffoliModel, clifford_t_cost, get_model, rz_t_count
from .depth import DepthMetrics, measure_depth
from .gates import GateCounts, count_ops
from .reports import log2_str, pow2_str, render, to_csv, to_json, to_markdown

__all__ = [
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
