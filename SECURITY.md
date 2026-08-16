# Security Policy

## What qSHA256 is

A **research and educational project** that constructs reversible quantum
circuits for SHA-256 and measures their logical resource requirements.

## What qSHA256 is not

It is **not a cryptographic library**. It provides no confidentiality, integrity
or authenticity guarantees and must not be used to protect anything.

Earlier versions (1.x) exposed AES-GCM, HMAC, HKDF and Ed25519 wrappers. **Those
were removed in 2.0.0.** If you were using them, migrate to
[`cryptography`](https://cryptography.io/) and `hashlib` directly - both are
maintained by people who specialise in exactly that, which this project does not.

The classical SHA-256 implementation in `qsha256/classical/` exists solely as a
transparent reference for validating circuits. It is written for inspectability,
not for speed or side-channel resistance: it is not constant-time, makes no
attempt to resist timing or cache attacks, and is far slower than `hashlib`.
**Use `hashlib.sha256` for anything real.**

## Does this break SHA-256?

No.

qSHA256 measures the cost of a **known generic attack** (Grover search) against
SHA-256. It finds no cryptanalytic weakness, no shortcut, and no reduction in
SHA-256's security margin. Its results, if anything, point the other way: a
Grover query costs about twice a full SHA-256 evaluation, the resulting circuit
is astronomically deep, and Grover parallelises poorly - all of which make the
plain `2^128` figure an *underestimate* of the real difficulty.

Nothing in this repository has ever run on quantum hardware.

## Reporting an issue

Since this is not a security product, most issues are ordinary bugs; please open
a GitHub issue.

Two categories are treated with particular seriousness, because they go to the
project's purpose:

1. **A correctness bug in a circuit** - a construction that does not compute what
   it claims, or leaves a work register uncleaned.
2. **A misleading or overstated claim** - a number presented without its
   provenance, a logical resource described as physical, an extrapolation
   presented as a measurement, or a comparison between quantities that are not
   comparable.

Both are bugs here. Please report them, with the command that reproduces the
result.

For anything you would rather not discuss in public, email
blazehavenservers@gmail.com.

## Supported versions

| Version | Supported |
|---|---|
| 2.x | yes |
| 1.x | no - different project, removed functionality; migrate to `cryptography` |
