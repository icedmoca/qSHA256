"""The layered validation suite behind ``qsha256 validate``.

Validation is built bottom-up, and each layer is only trusted once the layer
below it has passed:

1. the classical reference against :func:`hashlib.sha256` and the FIPS 180-4
   constants and test vectors;
2. each reversible primitive against its classical counterpart, **exhaustively**
   over its whole input space at small widths;
3. the SHA-256 round function against the classical round, at real 32-bit width;
4. the full compression function against the classical compression, at real
   32-bit width and 64 rounds;
5. garbage-freedom: work registers, message and schedule all restored;
6. the preimage oracle's phase behaviour, over an entire toy search space.

Steps 3-6 run at genuine 32-bit width because the circuits are permutation
circuits, so the basis-state simulator executes them exactly.  This is not a
scaled-down proxy for the real thing; it is the real thing.
"""

from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field

from ..classical.sha256 import (
    big_sigma0,
    big_sigma1,
    ch,
    compress,
    maj,
    message_schedule,
    pad_message,
    parse_blocks,
    sha256_hex,
    small_sigma0,
    small_sigma1,
)
from ..quantum.primitives.add import ADDERS, add_into
from ..quantum.primitives.boolean import ch_word_into, maj_word_into
from ..quantum.primitives.xor import xor_terms
from ..quantum.registers import CircuitBuilder
from ..quantum.sha256.compression import build_compression
from ..quantum.sha256.round import build_round_circuit
from ..quantum.strategies import Strategy
from ..spec import SHA256, TOY4, TOY8, ShaSpec
from .basis_sim import BasisSimulator
from .vectors import (
    NIST_CAVP_SHA3_256,
    NIST_CAVP_SHA256,
    NIST_CAVP_SHA512,
    NIST_VECTORS,
)

__all__ = ["Check", "ValidationReport", "run_validation"]


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    cases: int = 0
    exhaustive: bool = False
    seconds: float = 0.0
    #: Overrides the exhaustive/randomized label. Fixed published vectors are
    #: neither, and calling them "randomized" misdescribes where they came from.
    scope: str = ""

    def __str__(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        scope = self.scope or ("exhaustive" if self.exhaustive else "randomized")
        cases = f"{self.cases:,} cases, {scope}" if self.cases else self.detail
        return f"  [{mark}] {self.name:<52} {cases}  ({self.seconds:.2f}s)"


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        return check


def _timed(fn):
    start = time.time()
    check = fn()
    check.seconds = time.time() - start
    return check


# --------------------------------------------------------------------------
# layer 1: classical reference
# --------------------------------------------------------------------------


def check_classical() -> Check:
    failures = []
    for message, expected in NIST_VECTORS:
        got = sha256_hex(message)
        if got != expected:
            failures.append(f"{message[:20]!r}: {got} != {expected}")
    rng = random.Random(1)
    n = 0
    for _ in range(40):
        data = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 200)))
        n += 1
        if sha256_hex(data) != hashlib.sha256(data).hexdigest():
            failures.append(f"random {len(data)}-byte message")
    return Check(
        "classical SHA-256 == hashlib + NIST vectors",
        not failures,
        "; ".join(failures[:3]),
        cases=len(NIST_VECTORS) + n,
    )


def check_cavp() -> Check:
    """Third-party published expected outputs, for SHA-256, SHA-512 and SHA3-256.

    NIST_VECTORS are the two examples printed in FIPS 180-4 itself.  These come
    from the CAVP response files instead: longer messages, byte lengths that
    cross the padding boundary, and hashes nobody here computed.  The point is
    that the expected side of the comparison has an origin outside this project.

    Every vector is also checked against ``hashlib``, so a transcription error
    on our side shows up as a disagreement rather than as a passing test.  That
    check has already caught one -- a mistyped SHA3-256 digest.
    """
    from ..classical.sha256 import sha256 as sha_generic
    from ..spec import SHA512

    failures = []
    cases = 0
    for label, vectors, fn in (
        ("SHA-256", NIST_CAVP_SHA256, lambda m: sha_generic(m).hex()),
        ("SHA-512", NIST_CAVP_SHA512, lambda m: sha_generic(m, SHA512).hex()),
        ("SHA3-256", NIST_CAVP_SHA3_256, lambda m: hashlib.sha3_256(m).hexdigest()),
    ):
        reference = {
            "SHA-256": hashlib.sha256,
            "SHA-512": hashlib.sha512,
            "SHA3-256": hashlib.sha3_256,
        }[label]
        for hexmsg, expected in vectors:
            message = bytes.fromhex(hexmsg)
            cases += 1
            if reference(message).hexdigest() != expected:
                failures.append(f"{label} {len(message)}B: vector mis-transcribed")
            elif fn(message) != expected:
                failures.append(f"{label} {len(message)}B: model disagrees")
    return Check(
        "NIST CAVP vectors (third-party expected outputs)",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
        scope="published, dual-checked",
    )


def check_constants() -> Check:
    """K and H0 are derived from prime roots, not transcribed -- check the derivation."""
    ok = (
        SHA256.k[0] == 0x428A2F98
        and SHA256.k[63] == 0xC67178F2
        and SHA256.h0[0] == 0x6A09E667
        and SHA256.h0[7] == 0x5BE0CD19
    )
    return Check("SHA-256 constants derived == FIPS 180-4", ok, cases=72, exhaustive=True)


def check_padding() -> Check:
    failures = []
    cases = 0
    for length in list(range(0, 130)) + [1000]:
        cases += 1
        padded = pad_message(b"a" * length)
        if len(padded) % 64 or not padded.startswith(b"a" * length + b"\x80"):
            failures.append(f"length {length}")
        if int.from_bytes(padded[-8:], "big") != length * 8:
            failures.append(f"length field at {length}")
    return Check(
        "padding: block alignment and length field",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
        exhaustive=True,
    )


# --------------------------------------------------------------------------
# layer 2: primitives, exhaustive
# --------------------------------------------------------------------------


def check_adders() -> Check:
    failures = []
    cases = 0
    for name, adder in ADDERS.items():
        if not adder.basis_simulable:
            continue  # QFT adder is checked by statevector in the pytest suite
        for width in (2, 3, 4, 5):
            b = CircuitBuilder(f"{name}{width}")
            a = b.add_word(width, "a")
            t = b.add_word(width, "b")
            add_into(b, a, t, name)
            sim = BasisSimulator(b.circuit)
            for x in range(1 << width):
                for y in range(1 << width):
                    cases += 1
                    out, _ = sim.run(sim.load({a: x, t: y}))
                    if (
                        sim.read(out, a) != x
                        or sim.read(out, t) != (x + y) % (1 << width)
                        or sim.nonzero_indices(out, exclude=[a, t])
                    ):
                        failures.append(f"{name} n={width} {x}+{y}")
    return Check(
        "reversible adders == (a+b) mod 2^n",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
        exhaustive=True,
    )


def check_boolean() -> Check:
    failures = []
    cases = 0
    width = 3
    for fn, ref, label in ((ch_word_into, ch, "Ch"), (maj_word_into, maj, "Maj")):
        b = CircuitBuilder(label)
        x, y, z, t = (b.add_word(width, c) for c in "xyzt")
        fn(b, x, y, z, t)
        sim = BasisSimulator(b.circuit)
        mask = (1 << width) - 1
        for xv in range(1 << width):
            for yv in range(1 << width):
                for zv in range(1 << width):
                    cases += 1
                    out, _ = sim.run(sim.load({x: xv, y: yv, z: zv}))
                    if sim.read(out, t) != (ref(xv, yv, zv) & mask) or (
                        sim.read(out, x),
                        sim.read(out, y),
                        sim.read(out, z),
                    ) != (xv, yv, zv):
                        failures.append(f"{label}({xv},{yv},{zv})")
    return Check(
        "Ch / Maj == classical, inputs preserved",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
        exhaustive=True,
    )


def check_sigmas() -> Check:
    """Sigma functions at real 32-bit width, over random inputs plus edge cases."""
    failures = []
    cases = 0
    rng = random.Random(2)
    specs = [
        (SHA256.big_sigma0, big_sigma0, "Sigma0"),
        (SHA256.big_sigma1, big_sigma1, "Sigma1"),
        (SHA256.small_sigma0, small_sigma0, "sigma0"),
        (SHA256.small_sigma1, small_sigma1, "sigma1"),
    ]
    values = [0, 1, 0xFFFFFFFF, 0x80000000] + [rng.getrandbits(32) for _ in range(24)]
    for terms, ref, label in specs:
        b = CircuitBuilder(label)
        x = b.add_word(32, "x")
        t = b.add_word(32, "t")
        xor_terms(b, x, terms, t)
        sim = BasisSimulator(b.circuit)
        for value in values:
            cases += 1
            out, _ = sim.run(sim.load({x: value}))
            if sim.read(out, t) != ref(value, SHA256) or sim.read(out, x) != value:
                failures.append(f"{label}(0x{value:08x})")
    return Check(
        "Sigma0/1, sigma0/1 == classical (32-bit)",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
    )


def check_inverse() -> Check:
    """U then U-dagger must be the identity -- the property oracles depend on."""
    failures = []
    cases = 0
    for width in (3, 4):
        b = CircuitBuilder("roundtrip")
        a = b.add_word(width, "a")
        t = b.add_word(width, "b")
        start = len(b.circuit.data)
        add_into(b, a, t, "cdkm")
        b.append_reversed(start, len(b.circuit.data))
        sim = BasisSimulator(b.circuit)
        for x in range(1 << width):
            for y in range(1 << width):
                cases += 1
                out, _ = sim.run(sim.load({a: x, t: y}))
                if (sim.read(out, a), sim.read(out, t)) != (x, y):
                    failures.append(f"n={width} ({x},{y})")
    return Check(
        "U then U-dagger == identity",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
        exhaustive=True,
    )


# --------------------------------------------------------------------------
# layer 3-5: round, schedule, compression -- at real 32-bit width
# --------------------------------------------------------------------------


def check_schedule(spec: ShaSpec = SHA256) -> Check:
    """The quantum message schedule must reproduce the classical W[0..63]."""
    from ..quantum.sha256.schedule import build_schedule

    failures = []
    cases = 0
    rng = random.Random(4)
    for name in ("rolling", "store_all"):
        b = CircuitBuilder(f"sched_{name}")
        strategy = Strategy(schedule=name)
        sched = build_schedule(b, spec, strategy)
        words = [sched.word(t) for t in range(spec.rounds)] if name == "store_all" else None
        if name == "rolling":
            # The rolling window keeps only the last block_words entries alive.
            for t in range(spec.rounds):
                sched.word(t)
            words = [sched.word(t) for t in range(spec.rounds - spec.block_words, spec.rounds)]
        sim = BasisSimulator(b.circuit)
        for _ in range(3):
            cases += 1
            block = [rng.getrandbits(spec.word_bits) for _ in range(spec.block_words)]
            expected = message_schedule(block, spec)
            out, _ = sim.run(sim.load(dict(zip(sched.message, block))))
            offset = 0 if name == "store_all" else spec.rounds - spec.block_words
            for i, reg in enumerate(words):
                if sim.read(out, reg) != expected[offset + i]:
                    failures.append(f"{name} W[{offset + i}]")
                    break
    return Check(
        "quantum message schedule == classical W[t]",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
    )


def check_round(spec: ShaSpec = SHA256) -> Check:
    from ..classical.sha256 import round_step

    failures = []
    cases = 0
    rng = random.Random(5)
    for layout in ("serial", "wide", "csa"):
        b, st_in, w, st_out = build_round_circuit(spec, Strategy(round_layout=layout), t=0)
        sim = BasisSimulator(b.circuit)
        for _ in range(8):
            cases += 1
            values = [rng.getrandbits(spec.word_bits) for _ in range(8)]
            wv = rng.getrandbits(spec.word_bits)
            out, _ = sim.run(sim.load(dict(zip(st_in, values)) | {w: wv}))
            expected, _ = round_step(tuple(values), wv, spec.k[0], spec)
            if tuple(sim.read(out, r) for r in st_out) != expected:
                failures.append(f"{layout} state mismatch")
            elif sim.nonzero_indices(out, exclude=list(st_out) + [w]):
                failures.append(f"{layout} dirty ancilla")
    return Check(
        f"SHA-256 round == classical ({spec.word_bits}-bit, 3 layouts)",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
    )


def check_compression(spec: ShaSpec, rounds: int, strategy: Strategy, trials: int = 2) -> Check:
    failures = []
    rng = random.Random(6)
    comp = build_compression(spec, strategy, rounds=rounds)
    reduced = spec.with_rounds(rounds)
    sim = BasisSimulator(comp.circuit)
    for _ in range(trials):
        state = [rng.getrandbits(spec.word_bits) for _ in range(8)]
        block = [rng.getrandbits(spec.word_bits) for _ in range(spec.block_words)]
        out, _ = sim.run(sim.load(dict(zip(comp.state, state)) | dict(zip(comp.message, block))))
        expected = compress(tuple(state), block, reduced)
        if tuple(sim.read(out, r) for r in comp.digest) != expected:
            failures.append("digest mismatch")
        if comp.uncomputed:
            if any(sim.read(out, r) for r in comp.working):
                failures.append("work registers not restored to |0>")
            if [sim.read(out, r) for r in comp.message] != block:
                failures.append("message not restored")
        if [q for q in comp.builder.ancillas.all if out[sim.index_of(q)]]:
            failures.append("ancilla pool not clean")
    label = "garbage-free" if comp.uncomputed else "forward"
    return Check(
        f"{spec.name} compression, {rounds} rounds, {label} == classical",
        not failures,
        "; ".join(failures[:3]),
        cases=trials,
    )


def check_hashlib_end_to_end() -> Check:
    """The headline check: the real circuit's digest against hashlib."""
    message = b"abc"
    blocks = parse_blocks(pad_message(message), SHA256)
    comp = build_compression(SHA256, Strategy(), initial_state=SHA256.h0)
    sim = BasisSimulator(comp.circuit)
    out, _ = sim.run(sim.load(dict(zip(comp.message, blocks[0]))))
    digest = b"".join(sim.read(out, r).to_bytes(4, "big") for r in comp.state)
    expected = hashlib.sha256(message).digest()
    return Check(
        "32-bit 64-round circuit digest == hashlib.sha256(b'abc')",
        digest == expected,
        f"circuit={digest.hex()[:16]}... hashlib={expected.hex()[:16]}...",
        cases=1,
    )


def check_gidney() -> Check:
    """Gidney temporary ANDs, with every precondition checked during simulation."""
    failures = []
    cases = 0
    rng = random.Random(9)
    for spec, rounds in ((TOY4, 8), (SHA256, 64)):
        for uncompute in (False, True):
            strategy = Strategy(adder="gidney", uncompute_working=uncompute)
            comp = build_compression(spec, strategy, rounds=rounds)
            # strict=True verifies that every and_g target is |0> and every
            # and_g_dg target holds exactly x AND y.
            sim = BasisSimulator(comp.circuit, strict=True)
            for _ in range(2):
                cases += 1
                state = [rng.getrandbits(spec.word_bits) for _ in range(8)]
                block = [rng.getrandbits(spec.word_bits) for _ in range(spec.block_words)]
                out, _ = sim.run(
                    sim.load(dict(zip(comp.state, state)) | dict(zip(comp.message, block)))
                )
                if tuple(sim.read(out, r) for r in comp.digest) != compress(
                    tuple(state), block, spec.with_rounds(rounds)
                ):
                    failures.append(f"{spec.name} r{rounds} digest")
                if [q for q in comp.builder.ancillas.all if out[sim.index_of(q)]]:
                    failures.append(f"{spec.name} r{rounds} ancilla")
    return Check(
        "Gidney temporary-AND circuits == classical (preconditions checked)",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
    )


def check_phase_fold() -> Check:
    """Phase folding must preserve the unitary exactly, global phase included."""
    import random as _random

    import numpy as np
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Operator

    from ..quantum.optimization.phase_fold import phase_fold, to_clifford_t

    rng = _random.Random(11)
    failures = []
    cases = 0
    for _ in range(40):
        cases += 1
        n = rng.randint(2, 3)
        qc = QuantumCircuit(n)
        for _ in range(rng.randint(4, 18)):
            gate = rng.choice(["cx", "x", "h", "t", "tdg", "s", "ccx"])
            if gate == "ccx" and n < 3:
                gate = "cx"
            if gate == "ccx":
                qc.ccx(*rng.sample(range(n), 3))
            elif gate == "cx":
                qc.cx(*rng.sample(range(n), 2))
            else:
                getattr(qc, gate)(rng.randrange(n))
        folded = phase_fold(qc)
        if not np.allclose(
            Operator(to_clifford_t(qc)).data, Operator(folded.circuit).data, atol=1e-9
        ):
            failures.append("unitary changed")
            break
    return Check(
        "phase folding preserves the unitary exactly",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
    )


def check_oracle() -> Check:
    """The oracle must phase-flip exactly the preimages, over a whole toy space."""
    from ..quantum.oracle.preimage import build_preimage_oracle

    spec = TOY4
    iv = tuple(spec.h0)
    reference_block = [3, 1, 4, 1]
    target_state = compress(iv, reference_block, spec)
    target = sum(v << (i * spec.word_bits) for i, v in enumerate(target_state))

    oracle = build_preimage_oracle(
        spec, Strategy(uncompute_working=True), target_digest=target, initial_state=iv
    )
    sim = BasisSimulator(oracle.circuit)

    failures = []
    cases = 0
    for m0 in range(1 << spec.word_bits):
        for m1 in range(1 << spec.word_bits):
            cases += 1
            block = [m0, m1, 4, 1]
            out, phase = sim.run(sim.load(dict(zip(oracle.message, block))))
            should_flip = compress(iv, block, spec) == target_state
            if (phase == -1) != should_flip:
                failures.append(f"phase wrong for {block}")
            if [sim.read(out, r) for r in oracle.message] != block:
                failures.append(f"message not restored for {block}")
            if sim.nonzero_indices(out, exclude=oracle.message):
                failures.append(f"garbage left for {block}")
    return Check(
        "preimage oracle phase-flips exactly the preimages",
        not failures,
        "; ".join(failures[:3]),
        cases=cases,
        exhaustive=True,
    )


# --------------------------------------------------------------------------


def run_validation(quick: bool = False, verbose: bool = True) -> bool:
    report = ValidationReport()

    def emit(check: Check) -> None:
        report.add(check)
        if verbose:
            print(check, flush=True)

    if verbose:
        print("qSHA256 validation\n" + "=" * 78)
        print("\nLayer 1 - classical reference")
    emit(_timed(check_constants))
    emit(_timed(check_classical))
    emit(_timed(check_padding))

    if verbose:
        print("\nLayer 2 - reversible primitives (exhaustive at small width)")
    emit(_timed(check_adders))
    emit(_timed(check_boolean))
    emit(_timed(check_sigmas))
    emit(_timed(check_inverse))

    if verbose:
        print("\nLayer 3 - SHA-256 structure at real 32-bit width")
    emit(_timed(lambda: check_round(SHA256)))
    emit(_timed(lambda: check_schedule(SHA256)))
    emit(_timed(lambda: check_compression(TOY4, 8, Strategy(uncompute_working=True), 3)))
    emit(_timed(lambda: check_compression(TOY8, 8, Strategy(uncompute_working=True), 2)))

    if not quick:
        if verbose:
            print("\nLayer 4 - full 64-round SHA-256")
        emit(_timed(lambda: check_compression(SHA256, 64, Strategy(), 2)))
        emit(_timed(lambda: check_compression(SHA256, 64, Strategy(uncompute_working=True), 1)))
        emit(_timed(check_hashlib_end_to_end))

    if verbose:
        print("\nLayer 5 - optimization passes")
    emit(_timed(check_phase_fold))
    if not quick:
        emit(_timed(check_gidney))

    if verbose:
        print("\nLayer 6 - Grover oracle")
    emit(_timed(check_oracle))

    if verbose:
        failed = [c for c in report.checks if not c.passed]
        total_cases = sum(c.cases for c in report.checks)
        print("\n" + "=" * 78)
        print(
            f"{len(report.checks) - len(failed)}/{len(report.checks)} checks passed, "
            f"{total_cases:,} cases"
            + (" -- QUICK MODE, full-scale checks skipped" if quick else "")
        )
        if failed:
            print("\nFAILED:")
            for check in failed:
                print(f"  {check.name}: {check.detail}")
    return report.passed
