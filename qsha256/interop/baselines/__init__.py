"""Independent reconstructions of published circuits.

A resource comparison against a number copied from a paper can only be as good
as the copying. These modules rebuild published circuits from the primitives
their papers specify and measure them with the same analyzer qSHA256 uses on
its own work, so a comparison can be made without trusting anybody's reporting
conventions -- including ours.

Reconstructions are labelled by how well they reproduce the published figures,
and a reconstruction that fails to reproduce them says so.
"""

from .amy2016 import (
    TABLE_1,
    build_amy_round,
    build_amy_stretch,
    check_table_consistency,
    compare_architectures,
    reconstruction_report,
)

__all__ = [
    "TABLE_1",
    "build_amy_round",
    "build_amy_stretch",
    "check_table_consistency",
    "compare_architectures",
    "reconstruction_report",
]
