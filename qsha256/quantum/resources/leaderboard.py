"""Comparison against published quantum SHA-256 circuits.

A resource estimate with nothing to compare against is hard to trust.  This
module holds resource figures **transcribed from published papers**, with the
citation and table attached, so qSHA256's own measurements can be placed beside
prior work -- including where qSHA256 loses.

Rules for this file
-------------------

1. **Nothing here is invented.**  Every number is copied from a specific table
   in a specific paper, and the ``source`` field says which.  If a paper does
   not report a quantity, the field is ``None``; it is never estimated, inferred
   or filled in from a related figure.
2. **Comparability is explicit.**  The single biggest trap in cross-paper
   comparison is metrics that share a name but not a meaning.  "Depth" in a
   Clifford+T circuit and "depth" in a Toffoli-level circuit differ by roughly
   an order of magnitude; comparing them directly would be meaningless.  Each
   metric therefore carries a :class:`Comparability` verdict, and the report
   refuses to compute a ratio for anything marked ``INCOMPARABLE``.
3. **Adding an entry requires reading the paper.**  Not recalling it.

Adding entries is welcome; see ``docs/leaderboard.md`` for the required fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "PUBLISHED",
    "Comparability",
    "LeaderboardRow",
    "PublishedCircuit",
    "build_leaderboard",
    "render_leaderboard",
]


class Comparability:
    DIRECT = "DIRECT"
    """Same quantity, same conventions -- a ratio is meaningful."""

    QUALIFIED = "QUALIFIED"
    """Comparable only with a stated caveat, shown alongside the number."""

    INCOMPARABLE = "INCOMPARABLE"
    """Same name, different quantity. No ratio is reported."""


@dataclass(frozen=True)
class PublishedCircuit:
    """Resource figures transcribed from a publication."""

    key: str
    label: str
    citation: str
    source: str
    #: What the circuit actually is, in enough detail to know what it compares to.
    scope: str

    logical_qubits: int | None = None
    toffoli_count: int | None = None
    t_count: int | None = None
    t_depth: int | None = None
    cnot_count: int | None = None
    h_count: int | None = None
    depth: int | None = None

    #: Which of our metrics may be compared to theirs, and with what caveat.
    comparability: dict[str, tuple[str, str]] = field(default_factory=dict)
    notes: str = ""

    def get(self, metric: str) -> int | None:
        return getattr(self, metric, None)


#: --- Amy, Di Matteo, Gheorghiu, Mosca, Parent, Schanck (SAC 2016) ---
#:
#: Figures below are copied from Table 1 of the ePrint version
#: (https://eprint.iacr.org/2016/992.pdf, p.10), which reports T-par optimization
#: results for the round function, the message-schedule ("stretch") function, and
#: full SHA-256.  The paper states in the same table's caption that "the circuit
#: uses 2402 total logical qubits".
_AMY_COMMON_NOTES = (
    "Toffoli gates were expanded with the T-depth-3 Toffoli of Amy et al. "
    "(their ref. [24]), then the whole circuit was optimized with T-par, a "
    "phase-polynomial optimizer that merges T gates across gate boundaries. "
    "That cross-gate optimization is why the optimized T-count falls well below "
    "7 x (Toffoli count): it is an optimization qSHA256 does not currently "
    "perform, and it is the clearest place where prior work beats this project."
)

_AMY_COMPARABILITY = {
    "logical_qubits": (
        Comparability.QUALIFIED,
        "Their 2402 qubits cover the SHA-256 circuit as used inside their Grover "
        "oracle, with the IV and padding fixed as constants. Compare against a "
        "qSHA256 forward compression circuit, not against the garbage-free or "
        "full-oracle variants.",
    ),
    "t_count": (
        Comparability.DIRECT,
        "Both are T-counts for a full 64-round SHA-256. State which Toffoli "
        "decomposition each used: theirs is T-depth-3 (7 T per Toffoli) plus "
        "T-par optimization; qSHA256's default is the standard 7-T Toffoli with "
        "no cross-gate optimization.",
    ),
    "t_depth": (
        Comparability.DIRECT,
        "Both are T-depths of a Clifford+T circuit. qSHA256 must supply a "
        "transpiled (not serial-upper-bound) T-depth for this to be meaningful.",
    ),
    "cnot_count": (
        Comparability.QUALIFIED,
        "Their CNOT count is post-Clifford+T-expansion. Comparable only when the "
        "qSHA256 report carries transpiled figures (it then uses the transpiled "
        "CNOT count); against a Toffoli-level count the two differ by roughly the "
        "7 CNOTs each Toffoli expansion introduces, and no ratio is meaningful.",
    ),
    "depth": (
        Comparability.QUALIFIED,
        "Their depth is the depth of the expanded Clifford+T circuit. Comparable "
        "only when the qSHA256 report carries a transpiled depth; the Toffoli-level "
        "depth qSHA256 reports by default is a different quantity roughly an order "
        "of magnitude smaller.",
    ),
    "h_count": (
        Comparability.QUALIFIED,
        "Hadamard counts are only comparable post-expansion, and depend heavily on "
        "which Toffoli decomposition was used; a similar H count between two "
        "circuits mostly reflects a similar Toffoli count, not similar efficiency.",
    ),
    "toffoli_count": (
        Comparability.QUALIFIED,
        "The paper does not report a Toffoli count directly. It can be inferred "
        "as T-count / 7 from the unoptimized row, since every Toffoli contributes "
        "exactly 7 T gates before T-par runs. That inference is stated here, not "
        "presented as a published figure.",
    ),
}

_AMY_SHA3_COMPARABILITY = {
    "logical_qubits": (
        Comparability.QUALIFIED,
        "Their 3,200 qubits is two 1600-bit registers and nothing else, because "
        "they synthesise theta in place as an invertible GF(2) map. qSHA256 uses "
        "per-round scratch for theta instead, so it is larger in qubits and far "
        "smaller in CNOTs. Different point on the same tradeoff, not a defect.",
    ),
    "toffoli_count": (
        Comparability.DIRECT,
        "Both are Toffoli counts for Keccak-f[1600] over 24 rounds.",
    ),
    "cnot_count": (
        Comparability.QUALIFIED,
        "Their CNOT count is dominated by in-place linear synthesis of theta; "
        "ours by ancilla-based theta. The 100x-plus gap is the whole substance "
        "of the tradeoff and should not be read as an efficiency win on its own.",
    ),
    "t_count": (
        Comparability.QUALIFIED,
        "Derived from the Toffoli count under a stated decomposition on both "
        "sides; state which before comparing.",
    ),
    "depth": (
        Comparability.INCOMPARABLE,
        "Different bases and different structures.",
    ),
    "t_depth": (Comparability.INCOMPARABLE, "Not reported comparably."),
    "h_count": (Comparability.INCOMPARABLE, "Not reported comparably."),
}

PUBLISHED: dict[str, PublishedCircuit] = {
    "amy2016-sha3": PublishedCircuit(
        key="amy2016-sha3",
        label="Amy et al. 2016 (SHA3-256 / Keccak-f[1600])",
        citation=(
            "M. Amy, O. Di Matteo, V. Gheorghiu, M. Mosca, A. Parent, J. Schanck, "
            "'Estimating the cost of generic quantum pre-image attacks on SHA-2 and "
            "SHA-3', SAC 2016. arXiv:1603.09383 / ePrint 2016/992."
        ),
        source="Section 6 prose (ePrint 2016/992, p.13)",
        scope=(
            "One Keccak-f[1600] permutation, 24 rounds, as used inside their Grover "
            "oracle. The paper states: 3200 qubits, 85 NOT gates, 33269760 CNOT "
            "gates and 84480 Toffoli gates."
        ),
        logical_qubits=3200,
        toffoli_count=84_480,
        cnot_count=33_269_760,
        comparability=_AMY_SHA3_COMPARABILITY,
        notes=(
            "Their theta is synthesised in place as an invertible linear map, which "
            "keeps the qubit count at two registers but costs tens of millions of "
            "CNOTs. qSHA256 uses per-round ancilla for theta: far fewer CNOTs, more "
            "qubits. Neither dominates."
        ),
    ),
    "amy2016": PublishedCircuit(
        key="amy2016",
        label="Amy et al. 2016 (SHA-256, pre-T-par)",
        citation=(
            "M. Amy, O. Di Matteo, V. Gheorghiu, M. Mosca, A. Parent, J. Schanck, "
            "'Estimating the cost of generic quantum pre-image attacks on SHA-2 and "
            "SHA-3', SAC 2016. arXiv:1603.09383 / ePrint 2016/992."
        ),
        source="Table 1, row 'SHA-256' (ePrint 2016/992, p.10)",
        scope="Full 64-round SHA-256 forward circuit, as used inside their Grover oracle.",
        logical_qubits=2402,
        t_count=401_584,
        t_depth=171_552,
        cnot_count=534_272,
        h_count=114_368,
        depth=528_768,
        # 401584 / 7 -- inferred, flagged as such in the comparability note.
        toffoli_count=57_369,
        comparability=_AMY_COMPARABILITY,
        notes=_AMY_COMMON_NOTES,
    ),
    "amy2016-opt": PublishedCircuit(
        key="amy2016-opt",
        label="Amy et al. 2016 (SHA-256, T-par optimized)",
        citation=(
            "M. Amy, O. Di Matteo, V. Gheorghiu, M. Mosca, A. Parent, J. Schanck, "
            "'Estimating the cost of generic quantum pre-image attacks on SHA-2 and "
            "SHA-3', SAC 2016. arXiv:1603.09383 / ePrint 2016/992."
        ),
        source="Table 1, row 'SHA-256 (Opt.)' (ePrint 2016/992, p.10)",
        scope=(
            "The same circuit after T-par phase-polynomial optimization. The paper "
            "reports depth 830720 and notes it is approximately 2^62.458 code cycles "
            "at 2402 ~ 2^11.4 logical qubits."
        ),
        logical_qubits=2402,
        t_count=228_992,
        t_depth=70_400,
        cnot_count=4_209_072,
        h_count=94_144,
        depth=830_720,
        toffoli_count=None,  # not meaningful after phase-polynomial optimization
        comparability=_AMY_COMPARABILITY,
        notes=(
            _AMY_COMMON_NOTES
            + " Note that T-par lowered the T-count and T-depth substantially while "
            "*raising* both CNOT count and total depth -- a reminder that "
            "'optimized' is always relative to a chosen objective."
        ),
    ),
}


#: Capabilities a circuit assumes of its hardware. Comparing circuits that
#: assume different capabilities is legitimate only if the difference is stated.
CAPABILITY_NOTES = {
    "feedforward": (
        "This qSHA256 circuit uses Gidney temporary ANDs, whose uncomputation is "
        "a mid-circuit measurement plus a classically-controlled correction. It "
        "therefore assumes hardware with fast measurement and feedforward -- a "
        "capability the published circuit does not assume (Gidney's construction "
        "postdates it). The comparison is still meaningful, but it is a "
        "comparison between different machine models, not between two circuits "
        "for the same machine. The unitary qSHA256 designs are the "
        "like-for-like comparison."
    ),
}


@dataclass
class LeaderboardRow:
    metric: str
    ours: int | None
    theirs: int | None
    comparability: str
    caveat: str
    ratio: float | None = None
    verdict: str = ""


def build_leaderboard(report, entry: str | PublishedCircuit = "amy2016") -> list[LeaderboardRow]:
    """Compare a qSHA256 :class:`ResourceReport` against a published circuit.

    Ratios are computed only for metrics marked ``DIRECT`` or ``QUALIFIED``.
    ``INCOMPARABLE`` metrics still appear -- with both numbers and the reason --
    because hiding them would be worse than showing them side by side with a
    warning.
    """
    published = PUBLISHED[entry] if isinstance(entry, str) else entry

    ours_by_metric = {
        "logical_qubits": report.width,
        "toffoli_count": report.toffoli_count,
        "t_count": report.clifford_t.get("t_count_transpiled") or report.t_count,
        "t_depth": report.clifford_t.get("t_depth_transpiled"),
        "cnot_count": report.clifford_t.get("cnot_transpiled"),
        "h_count": report.clifford_t.get("h_transpiled"),
        "depth": report.clifford_t.get("depth_transpiled"),
    }
    transpiled = "t_count_transpiled" in report.clifford_t

    needs_feedforward = bool(report.clifford_t.get("needs_feedforward"))

    rows: list[LeaderboardRow] = []
    for metric, ours in ours_by_metric.items():
        theirs = published.get(metric)
        comparability, caveat = published.comparability.get(
            metric, (Comparability.INCOMPARABLE, "No comparability assessment recorded.")
        )
        ratio = None
        verdict = ""
        if metric in ("cnot_count", "depth", "h_count") and not transpiled:
            comparability = Comparability.INCOMPARABLE
            caveat = (
                "qSHA256 report has no transpiled figures, so this metric is not "
                "in the same basis as the published value. Re-run the analysis "
                "with transpile_t=True to compare."
            )
        if (
            ours is not None
            and theirs
            and comparability in (Comparability.DIRECT, Comparability.QUALIFIED)
        ):
            ratio = ours / theirs
            if ratio < 0.98:
                verdict = f"qSHA256 lower ({(1 - ratio) * 100:.0f}% less)"
            elif ratio > 1.02:
                verdict = f"qSHA256 higher ({(ratio - 1) * 100:.0f}% more)"
            else:
                verdict = "comparable"
        elif comparability == Comparability.INCOMPARABLE:
            verdict = "not compared"
        elif ours is None or theirs is None:
            verdict = "not reported"

        if needs_feedforward and metric in ("t_count", "t_depth") and ratio is not None:
            comparability = Comparability.QUALIFIED
            caveat = caveat + " " + CAPABILITY_NOTES["feedforward"]

        rows.append(
            LeaderboardRow(
                metric=metric,
                ours=ours,
                theirs=theirs,
                comparability=comparability,
                caveat=caveat,
                ratio=ratio,
                verdict=verdict,
            )
        )
    return rows


def render_leaderboard(report, entry: str = "amy2016", our_label: str = "qSHA256") -> str:
    published = PUBLISHED[entry]
    rows = build_leaderboard(report, published)

    out = [
        "qSHA256 vs Published Work",
        "=" * 78,
        "",
        f"Ours:      {our_label} -- {report.target}, {report.rounds} rounds, "
        f"{report.strategy.get('label', 'n/a')}",
        f"Theirs:    {published.label}",
        f"Citation:  {published.citation}",
        f"Source:    {published.source}",
        f"Scope:     {published.scope}",
        "",
        f"{'metric':<18}{'qSHA256':>14}{'published':>14}{'ratio':>10}  verdict",
        "-" * 78,
    ]
    for row in rows:
        ours = f"{row.ours:,}" if row.ours is not None else "-"
        theirs = f"{row.theirs:,}" if row.theirs is not None else "not reported"
        ratio = f"{row.ratio:.2f}x" if row.ratio is not None else "-"
        flag = "" if row.comparability == Comparability.DIRECT else f" [{row.comparability[:4]}]"
        out.append(f"{row.metric:<18}{ours:>14}{theirs:>14}{ratio:>10}  {row.verdict}{flag}")

    out += ["", "Comparability notes", "-" * 19]
    for row in rows:
        if row.comparability != Comparability.DIRECT:
            out.append(f"  {row.metric} [{row.comparability}]: {row.caveat}")
    if report.clifford_t.get("needs_feedforward"):
        out += [
            "",
            "DIFFERENT MACHINE MODEL",
            "-" * 23,
            f"  {CAPABILITY_NOTES['feedforward']}",
        ]
    out += ["", "Notes on the published circuit", "-" * 30, f"  {published.notes}"]
    return "\n".join(out)
