# Comparison against published circuits

A resource estimate with nothing to compare against is hard to trust. Published
figures live in `qsha256/quantum/resources/leaderboard.py` with their citations
attached.

## Rules for entries

1. **Nothing is invented.** Every number is copied from a specific table in a
   specific paper, and the `source` field says which. A quantity a paper does not
   report is `None` - never estimated, never inferred from a related figure.
2. **Comparability is explicit.** The biggest trap in cross-paper comparison is
   metrics that share a name but not a meaning. Each metric carries a verdict of
   `DIRECT`, `QUALIFIED` or `INCOMPARABLE`, and no ratio is computed for the
   last.
3. **Adding an entry requires reading the paper.** Not recalling it.

## Current results

Against **Amy, Di Matteo, Gheorghiu, Mosca, Parent & Schanck (SAC 2016)**,
arXiv:1603.09383 / ePrint 2016/992, Table 1 (p.10). Their circuit is a full
64-round SHA-256 forward circuit as used inside their Grover oracle; ours is the
default forward compression, transpiled to Clifford+T so the bases match.

### vs the pre-T-par circuit

| Metric | qSHA256 | Published | Ratio | |
|---|---:|---:|---:|---|
| logical qubits | 1,057 | 2,402 | 0.44x | **56% fewer** |
| T-count | 326,144 | 401,584 | 0.81x | **19% fewer** |
| T-depth | 154,112 | 171,552 | 0.90x | **10% lower** |
| CNOT | 431,136 | 534,272 | 0.81x | 19% fewer |
| depth | 463,606 | 528,768 | 0.88x | 12% lower |

The qubit saving comes from the in-place rolling message schedule and the
accumulate-into-`h` round layout, which together avoid materialising 48 schedule
words and any permanent per-round scratch. The Toffoli saving comes from the
ancilla-free one-Toffoli-per-bit `Ch` and `Maj` and the CDKM adder.

### vs the T-par-optimized circuit

| Metric | qSHA256 | Published | Ratio | |
|---|---:|---:|---:|---|
| logical qubits | 1,057 | 2,402 | 0.44x | 56% fewer |
| T-count | 326,144 | 228,992 | 1.42x | **42% more** |
| T-depth | 154,112 | 70,400 | 2.19x | **119% more** |
| depth | 463,606 | 830,720 | 0.56x | 44% lower |
| CNOT | 431,136 | 4,209,072 | 0.10x | 90% fewer |

**This gap has since been closed.** The table above compares the circuit *as
built*. With phase-polynomial folding applied, qSHA256 reaches **181,568 T** -
20.7% *below* the published T-par figure, at 44% of the qubits - and with Gidney
temporary ANDs as well, **107,168 T**.

| qSHA256 design | Qubits | T-count | vs published | Same machine model? |
|---|---:|---:|---:|---|
| default (as built) | 1,057 | 326,144 | +42.4% | yes - unitary Clifford+T |
| **+ phase folding** | **1,057** | **181,568** | **-20.7%** | **yes - unitary Clifford+T** |
| + Gidney ANDs | 1,087 | 131,744 | -42.5% | no - needs measurement + feedforward |
| + Gidney and folding | 1,087 | 107,168 | -53.2% | no - needs measurement + feedforward |

**Read the last column.** The Gidney rows assume mid-circuit measurement with
classical feedforward, a capability the 2016 circuit did not assume - Gidney's
construction postdates it by two years. Comparing them to a unitary circuit is a
comparison between *machine models*, and the leaderboard annotates every such row
rather than quietly claiming a win. The like-for-like result is the phase-folded
row.

Note also that qSHA256's folding is only part of T-par: it merges phases but does
not re-synthesise the CNOT network. That it still comes out ahead is largely
because the underlying circuit has fewer Toffolis to begin with.

Note also that T-par *lowered* T-count and T-depth while *raising* CNOT count and
total depth - a reminder that "optimized" is always relative to a chosen
objective.

## Caveats

- Qubit counts are `QUALIFIED`: their 2,402 covers the circuit as used inside
  their oracle with IV and padding fixed as constants. Compare against a qSHA256
  *forward* circuit, not the garbage-free or full-oracle variants.
- Depth and CNOT comparisons require the qSHA256 report to carry transpiled
  figures. Against a Toffoli-level count the two are different quantities and the
  leaderboard marks them `INCOMPARABLE` and computes no ratio.
- Their Toffoli count is inferred as `T-count / 7` from the unoptimized row; that
  inference is flagged, not presented as a published figure.

## Contributing an entry

Add a `PublishedCircuit` with `citation`, `source` (table and page), `scope`, and
a `comparability` verdict plus caveat for every metric. Leave unreported
quantities as `None`.

Regenerate with `python scripts/generate_benchmarks.py`.


## Reconstruction, not transcription

Every figure on this page that came out of a paper is marked with the table it
came from. That is necessary but not sufficient: a transcribed number cannot be
interrogated, and if the source's conventions differ from ours the difference
propagates silently into every ratio computed from it.

So `qsha256/interop/baselines/` rebuilds published circuits from the primitives
their papers specify and measures them with the same analyzer used on qSHA256's
own work. Run it with `qsha256 baseline`.

### Amy et al. 2016

Reconstructed from Figures 3, 4 and 5 and Algorithms 1 and 2. Results:

- **Their stretch reproduces exactly.** 186 Toffoli, matching what their own H
  column implies; expanded and folded with qSHA256's optimizer it reaches 744 T
  and 372 H, which is their published "Stretch (Opt.)" row to the gate.
- **Their round does not.** Their H column implies 754 Toffoli; their figures
  account for 626. The rebuild is verified correct against the classical round
  function, and the 128-Toffoli residual is unexplained.
- **Their Table 1 has two internal inconsistencies.** The unoptimized T-depth
  counts the 48 stretch iterations and the optimized T-depth does not; and the
  unoptimized stretch T (1,329) disagrees with its own H count (which implies
  1,302), inflating their reported total by 1,296 T.

Because their reported cost is *higher* than their described architecture,
quoting their published total would credit qSHA256 with a gap belonging to
their reporting. The like-for-like margin is therefore stated as 8.4%, not the
21.6% the published figure would allow.

### What reproducing a baseline bought us

qSHA256's CDKM adder was emitting `2n = 64` Toffoli for a 32-bit modular
addition. Calibrating against their stretch row pinned the Cuccaro cost at
`2(n-1) = 62`: the top MAJ/UMA pair cancels once the carry out is discarded.

Honesty requires the next sentence. This is **not** a resource saving: the
peephole rewriter was already finding all 1,200 of those pairs, so the
optimized 64-round figure is unchanged at 45,392 Toffoli. What changed is that
the construction now reaches the published Cuccaro cost on its own rather than
depending on a later pass, and the *unoptimized* counts -- which is what most
papers report, including the one being reproduced -- are no longer 2 per adder
too high.

## Where qSHA256 loses

The depth-and-width line of work is ahead. Every qSHA256 configuration is
Pareto-dominated on (logical qubits, non-Clifford depth) by Lee et al. 2022's
SHA-Z2 at 799 qubits and Toffoli-depth 12,024.

qSHA256 leads on T-count and AND-count. That line of work does not report
T-counts at all, so neither side can be compared on the other's strong axis and
no overall ranking exists. See claim C12.
