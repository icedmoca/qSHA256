# Grover, SHA-256, and what "2^128" leaves out

## The claim

Grover's algorithm finds a marked item among `N` in `O(sqrt(N))` queries, and
Zalka showed that is optimal. For a 256-bit preimage that gives roughly `2^128`
oracle queries instead of `2^256` classical evaluations, which is usually
summarised as "SHA-256 offers 128-bit security against quantum attack".

The query count is correct. The summary is misleading, for reasons this project
is built to measure rather than assert.

## 1. A query is not a hash evaluation

A Grover oracle must apply `|x> -> -|x>` on marked states and leave every work
register untouched. That requires:

```text
garbage-free forward SHA-256
  + comparison against the target digest
  + phase flip
  + full inverse SHA-256
```

The uncomputation is not optional. Without it the digest register stays entangled
with the candidate, the branches of the superposition remain distinguishable, and
the interference Grover depends on never happens.

**Measured** (64 rounds, 32-bit, default architecture):

| Circuit | Qubits | Toffoli | T-count |
|---|---:|---:|---:|
| forward compression | 1,057 | 46,592 | 326,144 |
| preimage oracle | 1,535 | 94,202 | 659,414 |

**2.02x**, measured, not assumed. Multiply the naive `2^128` by the real cost of
a query and the picture changes considerably.

## 2. Grover barely parallelises

Classical brute force parallelises perfectly: `m` machines give an `m`-fold
speedup. Grover does not. Splitting the search across `m` machines gives only
`sqrt(m)`. Wall-clock time cannot be bought down the way it can classically, and
`2^128` sequential iterations is not something any amount of hardware fixes.

## 3. Depth limits bite

A `2^128`-iteration Grover circuit is inherently serial. Its depth is
astronomically beyond any plausible coherence time or runtime budget - which is
exactly why NIST's post-quantum criteria evaluate attacks under an explicit
`MAXDEPTH` cap rather than by query count alone. Under any realistic cap the full
iteration count cannot be run, and the effective security is higher than the
query count suggests.

## 4. Error correction is not included

Every T gate in the counts above is a *logical* T, each requiring a distilled
magic state. See `docs/fault-tolerance.md`.

## What qSHA256 actually demonstrates

| Claim | Status |
|---|---|
| reversible SHA-256 circuit constructed at full scale | yes |
| that circuit executed on basis states and matched `hashlib` | yes |
| preimage oracle constructed at full scale, resources measured | yes |
| oracle phase behaviour verified over an entire search space | yes, on a 4-bit toy |
| Grover amplitude amplification executed | yes, on a ~15-qubit toy hash |
| Grover run against real SHA-256 | **no, and it never will be here** |
| any circuit executed on quantum hardware | **no** |

The toy demonstrations are labelled toys everywhere they appear. The full-scale
numbers are labelled with their provenance. The gap between them is the honest
subject of this repository.

## References

- L. K. Grover, STOC '96, arXiv:quant-ph/9605043.
- C. Zalka, "Grover's quantum searching algorithm is optimal", PRA 60, 2746
  (1999), arXiv:quant-ph/9711070.
- M. Amy, O. Di Matteo, V. Gheorghiu, M. Mosca, A. Parent, J. Schanck,
  "Estimating the cost of generic quantum pre-image attacks on SHA-2 and SHA-3",
  SAC 2016, arXiv:1603.09383.
- NIST, "Submission Requirements and Evaluation Criteria for the Post-Quantum
  Cryptography Standardization Process" (2016) - the `MAXDEPTH` discussion.
