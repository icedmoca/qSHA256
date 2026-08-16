# Changelog

All notable changes to this project are documented here. This project follows
[Semantic Versioning](https://semver.org/).

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
- Three published reversible adders — CDKM, VBE, Draper QFT — and two constant
  addition strategies.
- Carry-save multi-operand addition.
- Two message-schedule strategies (rolling in-place window, store-all) and three
  round layouts (serial, wide, carry-save).
- Full compression function with optional garbage-free uncomputation.

**Validation**

- Exact basis-state simulator, which allows the **real 32-bit, 64-round circuit**
  to be executed and checked against `hashlib` — not a scaled-down proxy.
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
