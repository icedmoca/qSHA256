"""An independent reconstruction of the Amy et al. 2016 SHA-256 circuit.

Why this exists
---------------

Until now the comparison against prior work was asymmetric in a way that
mattered:

    qSHA256  ->  built here, measured here
    Amy 2016 ->  a number copied out of a table

A copied number cannot be interrogated. If their conventions differ from ours --
what counts as a gate, whether the message schedule is charged, which Toffoli
decomposition is assumed -- the copied figure carries that difference silently
into every ratio computed from it. So this module rebuilds their circuit from
the primitives their paper actually specifies, runs it through *the same*
analyzer qSHA256 uses on its own circuits, and then compares.

What the paper specifies, and where
-----------------------------------

Everything below is read off the published paper, with the location noted:

- **Algorithm 1** (p.8) -- the round, in the same form as FIPS 180-4.
- **Algorithm 2** (p.8) -- the message schedule, which they call *stretch*:
  ``w[i] <- W[i-16] + s0 + W[i-7] + s1``, so **three** modular additions.
- **Figure 3** (p.8) -- the round schematic. Counting its ``Add`` boxes gives
  **seven** adders per round: one each for ``Sigma1``, ``Ch``, ``K[i]``,
  ``W[i]``, ``D + t1``, ``Maj`` and ``Sigma0``.
- **Figure 4** (p.9) -- their ``Maj``: ``ccx(a,b,t); cx(a,b); ccx(b,c,t)``, so
  **2 Toffoli per bit**, leaving ``b`` as ``a^b`` for the inverse to restore.
- **Figure 5** (p.9) -- their ``Ch``: ``cx(c,t); cx(b,c); ccx(a,c,t)``, so
  **1 Toffoli per bit**, using the same ``a(b^c)^c`` rewrite qSHA256 uses.
- **Section 5.1** (p.9) -- adders are Cuccaro et al. [23], in-place, one ancilla,
  ``(a,b,0) -> (a,a+b,0)``; the ``Sigma`` blocks are "constructed using CNOT
  gates exclusively".
- **Section 5.2** (p.10) -- Toffolis expanded with the T-depth-3 Toffoli of
  their ref. [24], which costs **7 T and 2 H** each. This is what lets a
  Toffoli count be recovered from their reported ``H`` column.

The calibration that makes this checkable
-----------------------------------------

Their *stretch* row is fully determined by the paper: three additions, sigmas
free. Its reported ``H = 372`` gives ``372 / 2 = 186`` Toffoli, hence
``186 / 3 = 62`` Toffoli per 32-bit modular addition -- exactly ``2(n-1)``,
the Cuccaro cost.

That single number is the most useful thing recovered here, because qSHA256's
own CDKM adder was emitting ``2n = 64``. The top ``MAJ``/``UMA`` pair cancels
when the carry out is discarded, and qSHA256 was not eliding it. Reproducing
this baseline is what surfaced that; see :func:`qsha256.quantum.primitives.add`.

Honest outcome
--------------

The reconstruction does **not** reproduce their round figure. Their ``H``
column implies 754 Toffoli per round; the architecture their own figures
describe accounts for 626. The 128-Toffoli residual is reported by
:func:`reconstruction_report` and is *not* tuned away, so the comparison
against their published total remains a comparison against a transcribed
number. What this module does buy is a second, fully reproducible comparison
that never consults their table at all: their architecture and ours, measured
by one analyzer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...spec import SHA256, ShaSpec

__all__ = [
    "TABLE_1",
    "TOFFOLI_H_COST",
    "TOFFOLI_T_COST",
    "ArchitectureComparison",
    "ConsistencyFinding",
    "ReconstructionResult",
    "build_amy_round",
    "build_amy_stretch",
    "check_table_consistency",
    "compare_architectures",
    "reconstruction_report",
]

#: Their Toffoli decomposition: the T-depth-3 Toffoli of Amy, Maslov, Mosca and
#: Roetteler (their ref. [24]). Section 5.2 states this explicitly.
TOFFOLI_T_COST = 7
TOFFOLI_H_COST = 2

#: Table 1 of ePrint 2016/992 (p.10), transcribed verbatim, column for column.
#: The caption states 64 round iterations, 48 stretch iterations, 2402 logical
#: qubits, and that no X gates are used.
TABLE_1: dict[str, dict[str, int]] = {
    "Round": {"T": 5278, "P": 0, "Z": 0, "H": 1508, "CNOT": 6800, "T_depth": 2262, "depth": 8262},
    "Round (Opt.)": {
        "T": 3020,
        "P": 931,
        "Z": 96,
        "H": 1192,
        "CNOT": 63501,
        "T_depth": 1100,
        "depth": 12980,
    },
    "Stretch": {"T": 1329, "P": 0, "Z": 0, "H": 372, "CNOT": 2064, "T_depth": 558, "depth": 2331},
    "Stretch (Opt.)": {
        "T": 744,
        "P": 279,
        "Z": 0,
        "H": 372,
        "CNOT": 3021,
        "T_depth": 372,
        "depth": 2907,
    },
    "SHA-256": {
        "T": 401584,
        "P": 0,
        "Z": 0,
        "H": 114368,
        "CNOT": 534272,
        "T_depth": 171552,
        "depth": 528768,
    },
    "SHA-256 (Opt.)": {
        "T": 228992,
        "P": 72976,
        "Z": 6144,
        "H": 94144,
        "CNOT": 4209072,
        "T_depth": 70400,
        "depth": 830720,
    },
}

ROUND_ITERATIONS = 64
STRETCH_ITERATIONS = 48


# --------------------------------------------------------------------------
# Part 1: does the published table agree with itself?
# --------------------------------------------------------------------------


@dataclass
class ConsistencyFinding:
    """One arithmetic check against the published table."""

    check: str
    consistent: bool
    expected: int | None = None
    reported: int | None = None
    note: str = ""

    def __str__(self) -> str:
        mark = "OK  " if self.consistent else "MISMATCH"
        body = f"  [{mark}] {self.check}"
        if not self.consistent and self.expected is not None:
            body += f"\n           derived {self.expected:,}, table says {self.reported:,}"
        if self.note:
            body += f"\n           {self.note}"
        return body


def check_table_consistency() -> list[ConsistencyFinding]:
    """Check Table 1 against itself, using only the paper's own statements.

    Three things are checkable without leaving the paper:

    1. The caption says the full circuit is 64 rounds and 48 stretch
       iterations, so every total should be ``64 R + 48 S`` -- except depth,
       which the caption explicitly says excludes the stretch because it runs
       in parallel.
    2. Under their stated Toffoli decomposition, ``T`` should be ``3.5 x H``,
       since it is 7 T and 2 H per Toffoli and their circuit is
       Toffoli-CNOT-NOT before expansion.
    3. The optimized rows should compose the same way the unoptimized ones do.

    Two of these fail, and the failures are reported rather than smoothed over.
    """
    findings: list[ConsistencyFinding] = []

    for tag, (rnd, stretch, total) in {
        "unoptimized": ("Round", "Stretch", "SHA-256"),
        "optimized": ("Round (Opt.)", "Stretch (Opt.)", "SHA-256 (Opt.)"),
    }.items():
        for metric in ("T", "P", "Z", "H", "CNOT", "T_depth", "depth"):
            both = (
                ROUND_ITERATIONS * TABLE_1[rnd][metric]
                + STRETCH_ITERATIONS * TABLE_1[stretch][metric]
            )
            rounds_only = ROUND_ITERATIONS * TABLE_1[rnd][metric]
            reported = TABLE_1[total][metric]
            if reported == both:
                convention = "64R + 48S"
            elif reported == rounds_only:
                convention = "64R only (stretch treated as parallel)"
            else:
                findings.append(
                    ConsistencyFinding(
                        f"{tag} {metric}: total composes from the per-iteration rows",
                        False,
                        both,
                        reported,
                        f"neither 64R+48S ({both:,}) nor 64R ({rounds_only:,})",
                    )
                )
                continue
            findings.append(
                ConsistencyFinding(
                    f"{tag} {metric}: total = {convention}", True, note=f"{reported:,}"
                )
            )

    # The convention should not change between the two rows of the same table.
    unopt_td = TABLE_1["SHA-256"]["T_depth"] == (
        ROUND_ITERATIONS * TABLE_1["Round"]["T_depth"]
        + STRETCH_ITERATIONS * TABLE_1["Stretch"]["T_depth"]
    )
    opt_td = (
        TABLE_1["SHA-256 (Opt.)"]["T_depth"]
        == ROUND_ITERATIONS * TABLE_1["Round (Opt.)"]["T_depth"]
    )
    findings.append(
        ConsistencyFinding(
            "T-depth uses the same convention in both rows",
            not (unopt_td and opt_td),
            note=(
                "The unoptimized T-depth (171,552) ADDS 48 stretch iterations; "
                "the optimized T-depth (70,400) does NOT, counting rounds only. "
                "Like for like, T-par improves T-depth 144,768 -> 70,400 (2.06x), "
                "not 171,552 -> 70,400 (2.44x)."
            )
            if (unopt_td and opt_td)
            else "",
        )
    )

    # T should be exactly 3.5 x H before optimization: 7 T and 2 H per Toffoli.
    for row in ("Round", "Stretch"):
        h, t = TABLE_1[row]["H"], TABLE_1[row]["T"]
        implied_toffoli = h // TOFFOLI_H_COST
        expected_t = implied_toffoli * TOFFOLI_T_COST
        findings.append(
            ConsistencyFinding(
                f"unoptimized {row}: T = 3.5 x H (their T-depth-3 Toffoli)",
                expected_t == t,
                expected_t,
                t,
                ""
                if expected_t == t
                else (
                    f"H implies {implied_toffoli} Toffoli, hence {expected_t:,} T. "
                    f"The {t - expected_t:+} T per iteration propagates "
                    f"{STRETCH_ITERATIONS * (t - expected_t):+,} into the reported "
                    f"SHA-256 total of {TABLE_1['SHA-256']['T']:,}."
                ),
            )
        )
    return findings


def implied_toffoli_count(row: str) -> int:
    """Toffoli count recovered from a row's H count under their decomposition.

    H is the more reliable of the two columns for this: it is unaffected by the
    +27 T anomaly in the stretch row, and it agrees exactly with the T column on
    the round row.
    """
    return TABLE_1[row]["H"] // TOFFOLI_H_COST


# --------------------------------------------------------------------------
# Part 2: rebuild their circuit from their published primitives
# --------------------------------------------------------------------------


def _amy_maj_into(b, x, y, z, target) -> None:
    """Their Figure 4 majority, bitwise: 2 Toffoli and 1 CNOT per bit.

    Leaves ``y`` holding ``x ^ y``; the paper notes the inverse circuit restores
    it. qSHA256's own ``Maj`` uses the algebraic rewrite
    ``x ^ ((x^y) & (x^z))`` for **1** Toffoli per bit, so this is deliberately
    the more expensive construction -- it is theirs, not ours.
    """
    for i in range(len(x)):
        b.ccx(x[i], y[i], target[i])
        b.cx(x[i], y[i])
        b.ccx(y[i], z[i], target[i])


def _amy_maj_undo(b, x, y, z, target) -> None:
    for i in reversed(range(len(x))):
        b.ccx(y[i], z[i], target[i])
        b.cx(x[i], y[i])
        b.ccx(x[i], y[i], target[i])


def _amy_ch_into(b, x, y, z, target) -> None:
    """Their Figure 5 choice function, bitwise: 1 Toffoli and 2 CNOT per bit.

    ``t = z ^ x(y^z) = Ch(x,y,z)``. Leaves ``z`` holding ``y ^ z``.
    """
    for i in range(len(x)):
        b.cx(z[i], target[i])
        b.cx(y[i], z[i])
        b.ccx(x[i], z[i], target[i])


def _amy_ch_undo(b, x, y, z, target) -> None:
    for i in reversed(range(len(x))):
        b.ccx(x[i], z[i], target[i])
        b.cx(y[i], z[i])
        b.cx(z[i], target[i])


def build_amy_stretch(spec: ShaSpec = SHA256):
    """One iteration of their *stretch*, per Algorithm 2.

    ``w[i] <- W[i-16] + s0(W[i-15]) + W[i-7] + s1(W[i-2])``: three in-place
    modular additions into the ``W[i-16]`` register, with the sigmas costing
    only CNOTs. Returns the builder and its registers.
    """
    from ...quantum.primitives.add import add_into
    from ...quantum.registers import CircuitBuilder
    from ...quantum.sha256.functions import small_sigma0_into, small_sigma1_into

    n = spec.word_bits
    b = CircuitBuilder("amy2016 stretch")
    w16 = b.add_word(n, "W[i-16]")
    w15 = b.add_word(n, "W[i-15]")
    w7 = b.add_word(n, "W[i-7]")
    w2 = b.add_word(n, "W[i-2]")

    for source, sigma in ((w15, small_sigma0_into), (w2, small_sigma1_into)):
        scratch = b.ancillas.acquire(n)
        sigma(b, source, scratch, spec)
        add_into(b, scratch, w16, "cdkm")
        sigma(b, source, scratch, spec)
        b.ancillas.release(scratch)
    add_into(b, w7, w16, "cdkm")
    return b, {"w16": w16, "w15": w15, "w7": w7, "w2": w2}


def build_amy_round(spec: ShaSpec = SHA256, t: int = 0):
    """One round, following Figure 3 and Algorithm 1 exactly.

    Seven adders, in the order the figure draws them::

        h += Sigma1(e)        h += Ch(e,f,g)      h += K[t]      h += W[t]
        d += h   (-> new e)   h += Maj(a,b,c)     h += Sigma0(a) (-> new a)

    The state rotation ``a..h -> b..h,a`` at the end is wire relabelling and is
    free, exactly as in qSHA256's own rounds.
    """
    from ...quantum.primitives.add import add_into
    from ...quantum.registers import CircuitBuilder
    from ...quantum.sha256.functions import big_sigma0_into, big_sigma1_into

    n = spec.word_bits
    b = CircuitBuilder(f"amy2016 round[{t}]")
    names = "abcdefgh"
    regs = {name: b.add_word(n, name) for name in names}
    a, b_, c, d, e, f, g, h = (regs[name] for name in names)
    w = b.add_word(n, "W")
    k = b.add_word(n, "K")

    def accumulate(compute, undo, *args):
        scratch = b.ancillas.acquire(n)
        compute(b, *args, scratch)
        add_into(b, scratch, h, "cdkm")
        undo(b, *args, scratch)
        b.ancillas.release(scratch)

    # t1 = h + Sigma1(e) + Ch(e,f,g) + K[t] + W[t], accumulated in place into h.
    accumulate(
        lambda bb, src, tgt: big_sigma1_into(bb, src, tgt, spec),
        lambda bb, src, tgt: big_sigma1_into(bb, src, tgt, spec),
        e,
    )
    accumulate(_amy_ch_into, _amy_ch_undo, e, f, g)
    add_into(b, k, h, "cdkm")
    add_into(b, w, h, "cdkm")
    # d += t1 gives the new e.
    add_into(b, h, d, "cdkm")
    # t2 = Sigma0(a) + Maj(a,b,c), accumulated into h, which becomes the new a.
    accumulate(_amy_maj_into, _amy_maj_undo, a, b_, c)
    accumulate(
        lambda bb, src, tgt: big_sigma0_into(bb, src, tgt, spec),
        lambda bb, src, tgt: big_sigma0_into(bb, src, tgt, spec),
        a,
    )
    return b, regs, w, k


# --------------------------------------------------------------------------
# Part 3: measure the reconstruction with the qSHA256 analyzer
# --------------------------------------------------------------------------


@dataclass
class ReconstructionResult:
    """What our rebuild of one of their components costs, versus their table."""

    component: str
    reconstructed_toffoli: int
    reconstructed_cnot: int
    published_toffoli: int
    published_t: int
    #: Toffoli count times 7, the pre-T-par T-count their decomposition implies.
    reconstructed_t: int = 0
    accounted: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.reconstructed_t = self.reconstructed_toffoli * TOFFOLI_T_COST

    @property
    def residual(self) -> int:
        return self.published_toffoli - self.reconstructed_toffoli

    @property
    def reproduced(self) -> bool:
        return self.residual == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "reconstructed_toffoli": self.reconstructed_toffoli,
            "published_toffoli": self.published_toffoli,
            "residual": self.residual,
            "reproduced": self.reproduced,
            "reconstructed_t": self.reconstructed_t,
            "published_t": self.published_t,
            "accounted": self.accounted,
        }


def _measure(builder) -> tuple[int, int]:
    ops = dict(builder.circuit.count_ops())
    return ops.get("ccx", 0), ops.get("cx", 0)


def reconstruction_report(spec: ShaSpec = SHA256) -> list[ReconstructionResult]:
    """Rebuild both of their components and compare against their own table."""
    n = spec.word_bits
    adder = 2 * (n - 1)

    stretch_builder, _ = build_amy_stretch(spec)
    s_toffoli, s_cnot = _measure(stretch_builder)
    stretch = ReconstructionResult(
        component="stretch (Algorithm 2)",
        reconstructed_toffoli=s_toffoli,
        reconstructed_cnot=s_cnot,
        published_toffoli=implied_toffoli_count("Stretch"),
        published_t=TABLE_1["Stretch"]["T"],
        accounted=[f"3 modular additions x {adder} Toffoli = {3 * adder}"],
    )

    round_builder, _, _, _ = build_amy_round(spec)
    r_toffoli, r_cnot = _measure(round_builder)
    rnd = ReconstructionResult(
        component="round (Figure 3)",
        reconstructed_toffoli=r_toffoli,
        reconstructed_cnot=r_cnot,
        published_toffoli=implied_toffoli_count("Round"),
        published_t=TABLE_1["Round"]["T"],
        accounted=[
            f"7 modular additions x {adder} Toffoli = {7 * adder}",
            f"Ch computed and uncomputed, 1 Toffoli/bit x 2 x {n} = {2 * n}",
            f"Maj computed and uncomputed, 2 Toffoli/bit x 2 x {n} = {4 * n}",
            "Sigma0, Sigma1: CNOT only, 0 Toffoli",
        ],
    )
    return [stretch, rnd]


def reproduce_optimized_stretch(spec: ShaSpec = SHA256) -> dict[str, Any]:
    """Rebuild their stretch, expand it, optimize it, and check against Table 1.

    This is the strongest link in the chain, because it closes end to end with
    nothing transcribed in the middle:

        Algorithm 2  ->  rebuild  ->  186 Toffoli  ->  expand (7 T each)
                     ->  1,302 T  ->  qSHA256 phase folding  ->  744 T

    and their Table 1 reports ``Stretch (Opt.) T = 744``, ``H = 372``. Both
    match exactly. So an independent implementation, an independent expansion
    and an independent phase-polynomial optimizer land on their published
    optimized figure to the gate.

    It also resolves the ``+27`` anomaly: their own optimized value of 744 is
    consistent with an unoptimized 1,302, not with the 1,329 printed one row
    above it. The 1,329 is very likely a typo, and it inflates their reported
    SHA-256 T-count of 401,584 by 48 x 27 = 1,296.
    """
    from ...quantum.optimization.phase_fold import phase_fold, to_clifford_t

    builder, _ = build_amy_stretch(spec)
    folded = phase_fold(to_clifford_t(builder.circuit), already_clifford_t=True)
    ops = dict(folded.circuit.count_ops())
    published_t = TABLE_1["Stretch (Opt.)"]["T"]
    published_h = TABLE_1["Stretch (Opt.)"]["H"]
    return {
        "toffoli": _measure(builder)[0],
        "t_before_folding": folded.t_before,
        "t_after_folding": folded.t_after,
        "published_t": published_t,
        "published_h": published_h,
        "our_h": ops.get("h", 0),
        "t_matches": folded.t_after == published_t,
        "h_matches": ops.get("h", 0) == published_h,
        "reduction_pct": round(-100 * folded.reduction, 1),
        "their_reduction_pct": round(100 * (published_t / TABLE_1["Stretch"]["T"] - 1), 1),
    }


# --------------------------------------------------------------------------
# Part 4: the comparison that never consults their table
# --------------------------------------------------------------------------


@dataclass
class ArchitectureComparison:
    """Their architecture and ours, both measured by the qSHA256 analyzer."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"rows": self.rows, "notes": self.notes}

    def __str__(self) -> str:
        out = [
            "Amy et al. 2016 architecture vs qSHA256, one analyzer, one convention",
            "=" * 76,
            "",
            f"{'architecture':<34}{'Toffoli/AND':>13}{'T':>12}{'vs Amy':>10}",
            "-" * 76,
        ]
        for row in self.rows:
            out.append(
                f"{row['architecture']:<34}{row['nonlinear']:>13,}{row['t_count']:>12,}"
                f"{row['ratio']:>10}"
            )
        out += ["", "Reading this table", "-" * 18]
        out += [f"  * {note}" for note in self.notes]
        return "\n".join(out)


def compare_architectures(spec: ShaSpec = SHA256, rounds: int = 64) -> ArchitectureComparison:
    """Build their full circuit and ours, and measure both the same way.

    This is the comparison that does not depend on trusting their table, since
    no number in it is transcribed: their architecture is rebuilt from their
    figures and counted by the same code that counts ours.

    T-counts here all use *their* decomposition (7 T per Toffoli) so the column
    means one thing throughout. The Gidney row is the exception and is labelled:
    its uncomputation is free in T but assumes measurement and feedforward, a
    capability their 2016 circuit does not have.
    """
    from ...quantum.sha256.compression import build_compression
    from ...quantum.strategies import Strategy

    stretch_builder, _ = build_amy_stretch(spec)
    round_builder, _, _, _ = build_amy_round(spec)
    per_round = _measure(round_builder)[0]
    per_stretch = _measure(stretch_builder)[0]
    theirs = rounds * per_round + (rounds - spec.block_words) * per_stretch

    rows = [
        {
            "architecture": "Amy et al. 2016 (rebuilt here)",
            "nonlinear": theirs,
            "t_count": theirs * TOFFOLI_T_COST,
            "ratio": "1.00x",
        }
    ]
    for label, strategy, per_and in (
        ("qSHA256 CDKM (unitary)", Strategy(adder="cdkm"), TOFFOLI_T_COST),
        ("qSHA256 Gidney (feedforward)", Strategy(adder="gidney"), 4),
    ):
        ops = dict(build_compression(spec, strategy, rounds=rounds).circuit.count_ops())
        nonlinear = ops.get("ccx", 0) + ops.get("and_g", 0)
        rows.append(
            {
                "architecture": label,
                "nonlinear": nonlinear,
                "t_count": nonlinear * per_and,
                "ratio": f"{nonlinear / theirs:.2f}x",
            }
        )

    return ArchitectureComparison(
        rows=rows,
        notes=[
            "Their circuit is rebuilt from Figures 3, 4 and 5 and Algorithms 1 "
            "and 2; no figure from their Table 1 is used, so this comparison "
            "stands or falls on the reconstruction, not on their reporting.",
            "The reconstruction does NOT reproduce their reported per-round "
            "Toffoli count -- see reconstruction_report(). Their H column "
            "implies 754 per round and their figures account for 626. Because "
            "the gap is in their favour being *larger*, rebuilding cannot be "
            "used to widen qSHA256's margin, and it is not.",
            "T-count uses 7 T per Toffoli everywhere except the Gidney row, "
            "which uses 4 T per AND with a free measurement-based "
            "uncomputation. That row assumes hardware their circuit does not.",
            "Their Maj costs 2 Toffoli per bit (their Figure 4). qSHA256 uses "
            "the algebraic rewrite for 1. That single difference is worth "
            f"{2 * spec.word_bits * rounds:,} Toffoli over {rounds} rounds and "
            "is the largest architectural gap between the two designs.",
        ],
    )
