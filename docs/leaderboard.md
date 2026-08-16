# Comparison against published circuits

A resource estimate with nothing to compare against is hard to trust. Published
figures live in `qsha256/quantum/resources/leaderboard.py` with their citations
attached.

## Rules for entries

1. **Nothing is invented.** Every number is copied from a specific table in a
   specific paper, and the `source` field says which. A quantity a paper does not
   report is `None` — never estimated, never inferred from a related figure.
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

**Where qSHA256 loses, and why.** T-par is a phase-polynomial optimizer that
merges T gates across gate boundaries; in their adders every Toffoli shares two
controls with another, which it exploits heavily. qSHA256 does no cross-gate T
optimization, so its T-count stays at exactly `7 x` its Toffoli count. Closing
this gap is the most valuable open item in the project.

Note also that T-par *lowered* T-count and T-depth while *raising* CNOT count and
total depth — a reminder that "optimized" is always relative to a chosen
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
