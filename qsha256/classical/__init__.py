"""Transparent classical reference model for the SHA-256 family.

This is not a competitor to :func:`hashlib.sha256` -- it is deliberately slower
and written for inspectability.  Its job is to expose every intermediate value
(message schedule words, per-round state, sigma/Ch/Maj outputs) so that the
reversible quantum circuits can be checked against them value by value.
"""

from .sha256 import (
    RoundTrace,
    add_mod,
    ch,
    compress,
    compression_trace,
    digest_from_state,
    maj,
    message_schedule,
    pad_message,
    parse_blocks,
    rotr,
    sha256,
    sha256_hex,
    shr,
    small_sigma0,
    small_sigma1,
    big_sigma0,
    big_sigma1,
    round_step,
)

__all__ = [
    "RoundTrace",
    "add_mod",
    "big_sigma0",
    "big_sigma1",
    "ch",
    "compress",
    "compression_trace",
    "digest_from_state",
    "maj",
    "message_schedule",
    "pad_message",
    "parse_blocks",
    "rotr",
    "round_step",
    "sha256",
    "sha256_hex",
    "shr",
    "small_sigma0",
    "small_sigma1",
]
