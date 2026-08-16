# Limitations

What this project has and has not demonstrated. Read this before quoting any
number from it.

## What has been demonstrated

- A reversible quantum circuit for the **full 64-round, 32-bit SHA-256**
  compression function is **constructed**, and its logical resources measured.
- That circuit has been **executed** on computational basis states and reproduces
  `hashlib.sha256` exactly, including multi-block chaining. This is possible
  because it is a permutation circuit, so exact simulation costs `O(gates)`
  rather than `O(2^qubits)`.
- A **garbage-free** variant returns work registers, message schedule and message
  block to their initial states.
- A **preimage oracle** is constructed at full scale and its resources measured.
- The oracle's phase behaviour is verified **exhaustively** over an entire toy
  search space.
- **Grover amplitude amplification executes** on a ~15-qubit reduced toy hash,
  raising a planted solution's probability from 0.0625 to 0.9613.
- Every design in the search space is verified against the classical reference.

## What has NOT been demonstrated

- **Nothing has run on quantum hardware.** Not one gate, not once.
- **Full-scale SHA-256 has never been simulated in superposition.** Only on basis
  states. A statevector for 1,057 qubits does not fit in the universe.
- **Grover has never been run against real SHA-256**, and will not be here.
- **SHA-256 has not been broken, weakened, or attacked.** This project measures
  the cost of a known generic attack; it finds no cryptanalytic weakness.
- **No new preimage has been found.** The toy demonstrations search a 16-element
  space with a solution planted in advance.

## Known weaknesses in the implementation

**No phase-polynomial optimization.** The T-count is exactly `7 x` the Toffoli
count. T-par-style cross-gate merging reaches substantially lower (Amy et al.
report 228,992 against our 326,144). This is the largest known gap.

**No measurement-based AND uncomputation.** Gidney's temporary-AND computes with
4 T gates and uncomputes with none. The circuits are full of compute/uncompute
AND pairs, so this is likely a large win — unimplemented because it needs
AND-pair structure tracking and a non-unitary gate model.

**Rotation cost is a modelling choice.** Rotations and shifts are counted as free
because they are wire relabellings. This is legitimate in a logical model with
free qubit-to-wire mapping, and is stated as an assumption in every report. On
limited-connectivity hardware the relabelling is absorbed into routing overhead,
which is **not** modelled.

**Depth assumes all-to-all connectivity.** No routing, no SWAP insertion. Real
hardware depth will be larger, possibly by a large factor.

**T-depth is sometimes an upper bound.** Where a circuit was not transpiled, the
reported T-depth is a fully-serialised upper bound. Labelled as such.

**The QFT adder's T-count is an estimate.** Arbitrary-angle rotations have no
exact Clifford+T form; their cost is a Ross-Selinger synthesis estimate at a
chosen precision, and changes if you change the precision.

**Fault-tolerant estimates are model outputs, not predictions.** Change the error
rate and the answer changes. Factory parameters vary by more than an order of
magnitude across published designs and are inputs here.

**Multi-block hashing is chained classically.** The circuit compresses one block;
a multi-block quantum hash would need the chaining value to stay quantum
throughout, multiplying the cost. Only the one-block case is measured.

**The toy specs are not SHA-256.** `toy4`, `toy8` and `toy-tiny` share the
architecture, not the security properties. They exist for exhaustive testing.

**Only Qiskit 2.x is tested.** The dependency pin says `qiskit>=2.0,<3` because
that is what has actually been run, not because 1.x is known to fail.

**Python 3.10-3.13 are tested in CI**; development happened on 3.14.

## Things deliberately not claimed

This project will not tell you that quantum computers break SHA-256, that
SHA-256 has 128-bit quantum security, or that N physical qubits suffice for
anything. Those claims require the distinctions this repository exists to
maintain.
