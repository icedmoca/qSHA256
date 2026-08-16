# Changelog

All notable changes to this project are documented here. This project follows
[Semantic Versioning](https://semver.org/).

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
