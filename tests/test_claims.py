"""Tests that the claims register stays true.

Documentation drifts away from code. These tests exist so that it cannot: every
number quoted in ``docs/claims.md`` and in the README is re-derived here, and a
change to the circuit that moves a headline figure fails the suite rather than
silently making the docs wrong.

They also pin the *qualifications*, not just the numbers. A claim stripped of
its conditions is a different claim, so the conditions are asserted too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from qsha256 import SHA256
from qsha256.classical.sha256 import pad_message, parse_blocks
from qsha256.classical.sha256 import sha256 as sha_generic
from qsha256.quantum.sha256.compression import build_compression
from qsha256.quantum.strategies import Strategy
from qsha256.validation.basis_sim import BasisSimulator
from qsha256.validation.vectors import (
    NIST_CAVP_SHA3_256,
    NIST_CAVP_SHA256,
    NIST_CAVP_SHA512,
)

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README").read_text()
CLAIMS = (ROOT / "docs" / "claims.md").read_text()

#: The headline count. Changing the circuit legitimately changes this; changing
#: it without updating README, docs/claims.md and CHANGELOG.md does not.
AND_COUNT = 22_696


# --- third-party vectors ---------------------------------------------------


@pytest.mark.parametrize("hexmsg,expected", NIST_CAVP_SHA256)
def test_cavp_sha256_against_reference_model(hexmsg, expected):
    assert sha_generic(bytes.fromhex(hexmsg)).hex() == expected


@pytest.mark.parametrize("hexmsg,expected", NIST_CAVP_SHA512)
def test_cavp_sha512_against_reference_model(hexmsg, expected):
    from qsha256.spec import SHA512

    assert sha_generic(bytes.fromhex(hexmsg), SHA512).hex() == expected


@pytest.mark.parametrize(
    "vectors,digest",
    [
        (NIST_CAVP_SHA256, "sha256"),
        (NIST_CAVP_SHA512, "sha512"),
        (NIST_CAVP_SHA3_256, "sha3_256"),
    ],
)
def test_cavp_vectors_are_transcribed_correctly(vectors, digest):
    """Guards the *expected* side.

    A published vector is only third-party evidence if it was copied down
    correctly. hashlib is the arbiter here, and this check has already caught
    one mis-transcribed SHA3-256 digest.
    """
    import hashlib

    fn = getattr(hashlib, digest)
    for hexmsg, expected in vectors:
        assert fn(bytes.fromhex(hexmsg)).hexdigest() == expected, hexmsg[:16]


# --- C1: the circuit computes SHA-256 --------------------------------------


@pytest.mark.slow
def test_full_circuit_reproduces_cavp_vectors():
    """Run published vectors through the full 1,057-qubit circuit.

    Possible only because the circuit is a permutation: basis states evolve to
    basis states, so no statevector is ever needed.
    """
    comp = build_compression(SHA256, Strategy(), initial_state=SHA256.h0)
    sim = BasisSimulator(comp.circuit)
    checked = 0
    for hexmsg, expected in NIST_CAVP_SHA256:
        message = bytes.fromhex(hexmsg)
        blocks = parse_blocks(pad_message(message), SHA256)
        if len(blocks) != 1:
            continue
        out, _ = sim.run(sim.load(dict(zip(comp.message, blocks[0]))))
        digest = b"".join(sim.read(out, r).to_bytes(4, "big") for r in comp.state)
        assert digest.hex() == expected
        checked += 1
    assert checked >= 3


# --- C4 / C5: the AND count and what it does and does not mean -------------


def test_gidney_compression_and_count():
    comp = build_compression(SHA256, Strategy(adder="gidney"), rounds=64)
    ops = dict(comp.circuit.count_ops())
    assert ops.get("and_g", 0) == AND_COUNT
    assert ops.get("and_g_dg", 0) == AND_COUNT, "every AND must be uncomputed"
    assert ops.get("ccx", 0) == 0, "no bare Toffolis should survive"


@pytest.mark.slow
def test_composed_floor_is_attained_and_labelled_as_conditional():
    from qsha256.formal.bounds import circuit_bound_report

    report = circuit_bound_report(strategy=Strategy(adder="gidney"), rounds=64, timeout=20)
    assert report.achieved_ands == AND_COUNT
    assert report.lower_bound == AND_COUNT
    assert report.attains_bound
    text = str(report)
    assert "architecture" in text.lower(), "the bound must be stated as model-relative"


def test_docs_do_not_overstate_the_composed_bound():
    """The word we spent a revision removing must stay removed."""
    for name, text in (("README", README), ("claims.md", CLAIMS)):
        assert "EXACTLY OPTIMAL" not in text, name
        assert "provably optimal" not in text.lower(), name


def test_readme_states_the_two_conceded_gaps():
    # The README is hard-wrapped at 80 columns, so a phrase spanning a line
    # break is still one phrase. Collapse whitespace before looking for it.
    lowered = re.sub(r"\s+", " ", README.lower())
    assert "sharing non-linear work" in lowered, "cross-component sharing gap"
    assert "five-operand" in lowered or "5-operand" in lowered, "multi-operand MC gap"
    assert "not a lower bound on the multiplicative complexity" in lowered


# --- C6 / C7: pebbling, and its move set ------------------------------------


def test_pebbling_claim_states_its_move_budget_and_rules():
    from qsha256.formal import pebbling

    doc = pebbling.__doc__ or ""
    assert "does not establish" in doc, "the impossibility must state its scope"
    optimality = (ROOT / "docs" / "optimality.md").read_text()
    assert "in-place" in optimality, "the extra move must be documented"
    assert re.search(r"\b256\b", optimality), "the tested move budget must be documented"


# --- C4 numbers appear consistently wherever they are quoted ----------------


def test_headline_number_is_consistent_across_documentation():
    formatted = f"{AND_COUNT:,}"
    for name, text in (("README", README), ("claims.md", CLAIMS)):
        assert formatted in text, f"{name} does not quote {formatted}"
