# Resource metrics: what each number means

Resource estimates mislead mostly through ambiguity, so every quantity qSHA256
reports is defined here, along with what it depends on.

## Provenance labels

Every figure carries one:

| Label | Meaning |
|---|---|
| `MEASURED` | counted directly off a constructed circuit object |
| `TRANSPILED` | measured off a circuit a compiler actually rewrote into Clifford+T |
| `ANALYTICAL` | derived from measured counts through a documented model |
| `EXTRAPOLATED` | projected beyond what was built, with the scaling rule shown |
| `ASSUMPTION-DEPENDENT` | requires a hardware/error-correction model to mean anything |

"SHA-256 needs N T gates" is a different claim under each of these.

## Qubit counts

- **Circuit width** — total qubits in the circuit object. `MEASURED`.
- **Data qubits** — named registers holding meaningful values.
- **Ancilla qubits** — total ever handed out by the recycling pool.
- **Max live qubits** — the high-water mark of simultaneously-held qubits, which
  is what a machine actually has to provide.

All of these are **logical** qubits. They are not physical qubits, and the ratio
is not a constant — see `docs/fault-tolerance.md`.

## Gate counts

`MEASURED`. Rotations and shifts contribute **zero**, because they are wire
relabellings (`docs/reversible-computing.md`).

- **Toffoli (CCX)** — the only non-Clifford gate qSHA256 emits, and the one that
  drives fault-tolerant cost.
- **CNOT / X** — Clifford, comparatively cheap under error correction.

## Depth

The most compiler-dependent number here, and the most often quoted without
qualification.

- **Depth** — longest gate chain, **assuming all-to-all connectivity**. On
  limited-connectivity hardware, routing inserts SWAPs and depth grows.
- **Two-qubit depth** — counting only two-or-more-qubit gates.
- **Toffoli depth** — longest chain of dependent Toffolis. Usually the most
  meaningful for fault tolerance, since Clifford layers are cheap relative to
  magic-state consumption.

Toffoli-level depth and Clifford+T depth are **different quantities**, differing
by roughly an order of magnitude. The leaderboard refuses to compute a ratio
between them.

## T-count and T-depth

There is no such thing as "the T-count of a Toffoli". There are several, and they
trade against each other:

| Model | T-count | T-depth | Ancilla | Measurement | Reference |
|---|---:|---:|---:|---|---|
| `standard` | 7 | 4 | 0 | no | Nielsen & Chuang Fig. 4.9 |
| `selinger` | 7 | 1 | 4 | no | Selinger, PRA 87, 042302 (2013) |
| `jones` | 4 | 1 | 1 | **yes** | Jones, PRA 87, 022328 (2013) |

`standard` is the default: ancilla-free, measurement-free, and reproduced exactly
by Qiskit's own Toffoli translation — which the test suite checks, so the
analytical model stays anchored to a real compiler rather than drifting.

`jones` is cheapest in T-count but is not a unitary circuit: it needs mid-circuit
measurement and classical feedforward, and cannot be inverted by gate reversal.

**Arbitrary-angle rotations** (the QFT adder) have no exact Clifford+T form at
all. Their cost is a Ross-Selinger synthesis *estimate* at a chosen precision
`epsilon`, and any report containing one says so. This is why the QFT adder,
despite having the fewest Toffolis in the whole design space, has by far the
worst T-count.

**T-depth**: reported as `TRANSPILED` where the circuit was actually compiled,
and otherwise as a *serial upper bound* that charges every Toffoli as if none ran
concurrently. The two are labelled differently because they are not the same
claim.

## Grover totals

`EXTRAPOLATED`: a measured per-oracle cost multiplied by the analytic query count
`(pi/4) sqrt(N/M)`. Reported in log2 because the results run to hundreds of bits.
Nothing is simulated and nothing is executed.
