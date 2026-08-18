# Changelog

All notable changes to this project are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [2.3.0] - 2026-08-18

### Added

**Baseline reconstruction** (`qsha256/interop/baselines/`). Published circuits
are rebuilt from the primitives their papers specify and measured with the same
analyzer used here, instead of being compared against transcribed numbers. Run
it with `qsha256 baseline`.

Amy et al. 2016 reconstructed from their Figures 3-5 and Algorithms 1-2. Their
message-schedule row reproduces exactly: 186 Toffoli, and after expansion and
phase folding 744 T and 372 H, matching their published "Stretch (Opt.)" row to
the gate. Their round does not: their H column implies 754 Toffoli where their
figures account for 626, and the residual is reported rather than tuned away.

**Two internal inconsistencies found in that paper's Table 1.** The unoptimized
T-depth counts the 48 stretch iterations while the optimized T-depth does not,
so the T-par improvement is 2.06x like-for-like rather than 2.44x. And the
unoptimized stretch T-count of 1,329 disagrees with its own H count, which
implies 1,302; their own optimized 744 confirms 1,302. That inflates their
reported SHA-256 T-count by 1,296.

**Modern baselines on the leaderboard.** Kim, Han and Jeong 2018 and Lee, Lee,
Lee and Choi 2022, with a Pareto check. Every qSHA256 configuration is
dominated on (width, Toffoli-depth) by SHA-Z2 at 799 qubits and depth 12,024.
qSHA256 leads on T-count, which that line of work does not report.

### Changed

**The comparison against Amy et al. is restated downward.** The previous claim
of a 20.7% T-count lead compared a hard-coded literal against their published
figure. Both halves are now derived, with their circuit rebuilt and both sides
folded by the same optimizer: the like-for-like lead is **8.4%**.

**The CDKM adder now emits 2(n-1) Toffoli rather than 2n.** The top MAJ/UMA
pair cancels once the carry out is discarded. This is not a resource saving --
the rewriter was already removing all 1,200 of them from the 64-round circuit,
so optimized counts are unchanged -- but unoptimized counts were 2 per adder
too high, and the construction now reaches the published Cuccaro cost without
depending on a later pass.

### Fixed

`scripts/reproduce.py` no longer reproduces claim C9 by comparing a constant
against itself.

## [2.2.0] - 2026-08-17

### Added

**Formal verification** (`qsha256/formal/`) -- correctness becomes a proof
rather than a sample. Circuits are symbolically executed into an XOR-aware
And-Inverter Graph, Tseitin-encoded into CNF, and discharged by SAT. Functional
equivalence against an independently written specification, ancilla cleanliness,
and Gidney AND preconditions are each established over all inputs. Whole
circuits are proved compositionally, with a structural check that the circuit
really is the composition of the proved parts.

**A borrow checker for uncomputation** (`quantum/ancilla_check.py`). Releasing
an ancilla that is not provably zero raises at the release site.

**Reversible pebbling** (`formal/pebbling.py`). 16 registers suffice for the
SHA-256 message schedule and 15 do not, at every move budget tested up to 256
(5.3x the minimum). Required extending the classical game with an in-place move,
without which the model declares the working circuit impossible -- so the move
set is part of the theorem and is stated with it.

**Multiplicative-complexity bounds** (`formal/bounds.py`). Proves MC(Ch) =
MC(Maj) = 1 by exhaustive search -- an unconditional result. With Gidney ANDs
throughout, the full 64-round circuit uses 22,696 ANDs against a composed floor
of 22,696, so it wastes nothing relative to its own decomposition. The composed
figure is a bound for that architecture class, not for SHA-256's multiplicative
complexity; the report states both gaps.

**Superoptimization** (`formal/superopt.py`) by meet-in-the-middle synthesis.
Finding: the shortest Ch is 3 gates using 2 Toffolis, the shortest Maj 3 gates
using 3, while qSHA256 uses 1 Toffoli in each. Minimising gate count is the
wrong objective under fault tolerance.

**SHA-512** -- a new parameter set, and the whole stack applies unchanged.
Verified against hashlib at 80 rounds.

**SHA-3 / Keccak-f[1600]** -- a genuinely different structure, where every
non-linear gate comes from chi. Verified against hashlib including the 0x86
pad10*1 edge case.

**Bitcoin double SHA-256** (`applications/bitcoin.py`) with midstate folding,
constant padding and a threshold predicate. Validated against the genesis block.

**Lattice-surgery layout** (`resources/layout.py`) following Litinski, replacing
the 2d^2 shortcut with concrete floor plans and spacetime volume.

**Cross-validation** (`interop/`) against Qualtran, Qiskit and a QASM-text
counter. All three agree on qubits, Toffoli and Clifford counts.

**Resource regression checking** (`scripts/check_regression.py`) with a
committed baseline, wired into CI.

**CITATION.cff** so results are citable.

### Changed

- Gidney temporary ANDs now cover Ch and Maj, so the 64-round circuit contains
  zero Toffoli gates and its T-count falls from 131,744 to 90,784.
- T-par matroid partitioning reports achievable T-depth: 93,184 against a
  186,368 serial bound.
- XOR-awareness in the AIG is now opt-in, because it helps cancellation and
  hurts equivalence miters -- measured, and documented at the flag.
- CI grew jobs for proofs, regression and cross-validation.

### Fixed

- CaDiCaL cannot be interrupted, so SAT timeouts against it were silently
  ignored; interruptible solvers are now selected when a timeout is requested.
- The QASM-text counter was counting gate-definition bodies as invocations.
- Qualtran's Clifford total was being compared against our CNOT count, which is
  a definitional mismatch rather than a disagreement.

## [2.1.0] - 2026-08-16

### Added

**Gidney measurement-based temporary ANDs** (`quantum/primitives/temporary_and.py`)

- `and_g` / `and_g_dg` gates: an AND computed into a clean ancilla in 4 T gates,
  and uncomputed in **zero** T gates via an X-basis measurement and a Clifford
  correction. A compute/uncompute pair costs 4 T instead of 14.
- A fourth adder, `gidney`: `n-1` ancillas, no Toffolis, `4(n-1)` T gates.
  A 32-bit addition drops from **448 T to 124 T**.
- The basis simulator gained a `strict` mode that verifies both preconditions -
  that every `and_g` target is `|0>` and every `and_g_dg` target holds exactly
  `x AND y` - since violating either is a silent correctness trap.
- `append_reversed` maps `and_g` to `and_g_dg` under reverse replay; the two are
  inverses, not self-inverse.

**Phase-polynomial folding** (`quantum/optimization/phase_fold.py`)

- T-par-style optimization: merges phase gates acting on the same GF(2) linear
  function, so `T . T` becomes a Clifford `S` and `T . Tdg` cancels. Verified to
  preserve the unitary **exactly, including global phase**, on hundreds of random
  circuits.
- A deterministic hand-written Toffoli expansion replaces the transpiler, so the
  decomposition is version-independent and agrees with the `standard` cost model
  by construction.
- Composes with Gidney ANDs, which are kept opaque rather than expanded.

**Measured results at 64 rounds, 32-bit**

| Design | T-count | vs baseline |
|---|---:|---:|
| cdkm (baseline) | 326,144 | - |
| cdkm + phase folding | 181,568 | -44.3% |
| gidney temporary ANDs | 131,744 | -59.6% |
| gidney + phase folding | 107,168 | -67.1% |

Against Amy et al.'s T-par-optimized 228,992 T, the phase-folded *unitary*
circuit is **20.7% lower at 44% of the qubits**. The Gidney designs go further
but assume measurement and feedforward, which the published circuit does not; the
leaderboard annotates every such comparison.

### Changed

- New objective `non_clifford_depth`, counting Toffolis, T gates and AND-computes
  together. Toffoli depth alone scored a phase-folded circuit at zero and read as
  a free win; this metric stays meaningful across all three representations. The
  Pareto search and the physical estimator both use it now.
- The design space grew from 72 to 96 architectures (224 searched points with
  optimization variants). `qsha256 search` reports the enlarged front.
- New `--adder gidney`, `--phase-fold` and preset `min-t`.
- Validation suite: 17 checks, 6,129 cases (was 15 / 4,721).
- Test suite: 360 tests (was 330).

## [2.0.0] - 2026-08-16

**qSHA256 has been refocused from a conventional cryptography wrapper into a
reversible quantum circuit construction and resource-analysis framework for
SHA-256.** This is a complete rewrite with no API in common with 1.x.

### Why

Version 1.x was a thin wrapper over `pyca/cryptography` and `hashlib` offering
AES-GCM, HMAC, HKDF and Ed25519, with experimental quantum code quarantined in an
untested `demos/` directory. That package duplicated well-maintained libraries
while its name promised something else entirely. The quantum material was the
only part that was not already better served elsewhere, so the project now *is*
that material.

### Removed

- The entire conventional cryptography surface: `secure_sha256`, `secure_hmac`,
  `secure_hmac_verify`, `generate_key`, `hkdf_extract_expand`, `aes_gcm_encrypt`,
  `aes_gcm_decrypt`, `ed25519_generate_keypair`, `ed25519_sign`,
  `ed25519_verify`, `SecurityError`. **Use `hashlib` and `cryptography`
  directly.**
- The `demos/` package and the `qsha256-demo` entry point.
- The `cryptography` dependency.
- `setup.py`-based packaging, replaced by `pyproject.toml`.

### Added

**Reversible circuit core**

- Parameterised SHA-256 family (`spec.py`) with `K` and `H0` *derived* from prime
  roots using exact integer arithmetic, verified against FIPS 180-4, plus `toy4`
  and `toy8` reduced models sharing every code path with the real spec.
- Transparent classical reference model exposing padding, message schedule,
  per-round state and all intermediates.
- `Word` register views making rotation and shift zero-gate wire relabellings; a
  recycling ancilla pool with high-water tracking; a builder that attributes gate
  cost to named components.
- Ancilla-free `Ch` and `Maj` at one Toffoli per bit, via algebraic rewrites.
- Three published reversible adders - CDKM, VBE, Draper QFT - and two constant
  addition strategies.
- Carry-save multi-operand addition.
- Two message-schedule strategies (rolling in-place window, store-all) and three
  round layouts (serial, wide, carry-save).
- Full compression function with optional garbage-free uncomputation.

**Validation**

- Exact basis-state simulator, which allows the **real 32-bit, 64-round circuit**
  to be executed and checked against `hashlib` - not a scaled-down proxy.
- Layered validation suite (`qsha256 validate`): 15 checks, 4,721 cases.
- 330 pytest tests, several asserting the project's honesty properties directly.

**Resource analysis**

- Measured gate, depth, qubit and per-component metrics with disjoint attribution.
- Three documented Clifford+T Toffoli decompositions (standard, Selinger, Jones);
  reports always state which was used. The analytical T-count is checked against
  a real transpilation.
- Ross-Selinger rotation synthesis costing for non-Clifford+T-native circuits.
- Surface-code fault-tolerant estimator with every parameter an explicit input.
- Provenance labelling throughout: `MEASURED`, `TRANSPILED`, `ANALYTICAL`,
  `EXTRAPOLATED`, `ASSUMPTION-DEPENDENT`.

**Optimization and search**

- Gate-level rewriting: commutation-aware involution cancellation and constant
  folding from `|0>`.
- Equivalence checking with reported assurance level.
- Exhaustive Pareto search over the design space with per-design verification and
  quantified trade-off statements.
- Hardware-aware ranking by spacetime volume.
- Leaderboard against published circuits, with per-metric comparability verdicts.

**Grover**

- Digest comparison and full SHA-256 preimage oracle, measured at 2.02x a forward
  evaluation.
- A reduced toy hash on which amplitude amplification actually executes.
- Grover cost extrapolation that separates measured, analytical and extrapolated
  quantities and states the caveats the `2^128` figure omits.

**Project**

- `qsha256` CLI with nine subcommands.
- Nine documentation pages including an explicit limitations page.
- Seven runnable examples.
- Reproducible benchmark generation; no number in the README is typed by hand.
- Modernised CI: lint, matrix tests, separate full-scale and benchmark workflows.

### Fixed

- **License inconsistency.** The README claimed MIT while `LICENSE` was Apache
  2.0. The `LICENSE` file is authoritative and unchanged; all documentation and
  package metadata now agree on **Apache 2.0**.
- Repository metadata pointing at the wrong GitHub organisation.
- CI that reported success while running zero tests (`tests/` contained only an
  `__init__.py`).

### Security

qSHA256 is a research and educational project. It is **not** a cryptographic
library, provides no security guarantees, and must not be used to protect
anything. See [SECURITY.md](SECURITY.md).

---

## [1.0.0] - 2025

Conventional cryptography wrapper providing SHA-256, HMAC, HKDF, AES-GCM and
Ed25519 over `hashlib` and `pyca/cryptography`, with experimental quantum demos.
Superseded entirely by 2.0.0.
