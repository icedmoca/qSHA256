"""qSHA256 -- reversible quantum SHA-2 and SHA-3 construction, verification and
resource analysis."""

__version__ = "2.2.0"

from .spec import SHA256, SHA512, SPECS, TOY4, TOY8, ShaSpec, get_spec

__all__ = [
    "SHA256",
    "SHA512",
    "SPECS",
    "TOY4",
    "TOY8",
    "ShaSpec",
    "__version__",
    "get_spec",
]
