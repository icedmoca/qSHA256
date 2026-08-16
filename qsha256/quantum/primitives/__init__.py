"""Reversible building blocks: XOR, Boolean logic, rotation, shift, modular addition."""

from .add import ADDERS, Adder, add_const_into, add_into, get_adder
from .boolean import and_into, and_tree_mcx, ch_into, ch_word_into, maj_into, maj_word_into
from .rotate import rotl, rotr
from .shift import shr
from .xor import copy_word, xor_const, xor_terms, xor_word

__all__ = [
    "ADDERS",
    "Adder",
    "add_const_into",
    "add_into",
    "and_into",
    "and_tree_mcx",
    "ch_into",
    "ch_word_into",
    "copy_word",
    "get_adder",
    "maj_into",
    "maj_word_into",
    "rotl",
    "rotr",
    "shr",
    "xor_const",
    "xor_terms",
    "xor_word",
]
