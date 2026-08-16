"""Grover, actually executed -- on a toy hash, and labelled as such.

    python examples/07_grover_toy.py

Full SHA-256 Grover cannot be simulated: the oracle is ~1,535 qubits. So the
repository separates two things it never conflates -- real SHA-256 circuits,
constructed and measured; and this toy, small enough to actually run.
"""

from qsha256.classical.sha256 import compress
from qsha256.quantum.oracle.preimage import build_preimage_oracle
from qsha256.quantum.strategies import Strategy
from qsha256.spec import TOY4
from qsha256.validation.basis_sim import BasisSimulator
from qsha256.validation.grover_demo import run_grover_demo

print("Part 1: the oracle marks EXACTLY the preimages")
print("=" * 66)
print("Checked over an entire 256-candidate space, at 143 qubits -- far beyond")
print("statevector reach, but easy on basis states.\n")

spec = TOY4
iv = tuple(spec.h0)
reference = [3, 1, 4, 1]
target_state = compress(iv, reference, spec)
target = sum(v << (i * spec.word_bits) for i, v in enumerate(target_state))

oracle = build_preimage_oracle(
    spec, Strategy(uncompute_working=True), target_digest=target, initial_state=iv
)
sim = BasisSimulator(oracle.circuit)
flipped, clean = [], True
for m0 in range(16):
    for m1 in range(16):
        block = [m0, m1, 4, 1]
        out, phase = sim.run(sim.load(dict(zip(oracle.message, block))))
        if phase == -1:
            flipped.append((m0, m1))
        clean &= not sim.nonzero_indices(out, exclude=oracle.message)

truth = [
    (m0, m1)
    for m0 in range(16)
    for m1 in range(16)
    if compress(iv, [m0, m1, 4, 1], spec) == target_state
]
print(
    f"  oracle: {oracle.circuit.num_qubits} qubits, "
    f"{sum(oracle.circuit.count_ops().values()):,} gates"
)
print(f"  phase-flipped:      {flipped}")
print(f"  true preimages:     {truth}")
print(f"  exactly correct:    {flipped == truth}")
print(f"  no leftover garbage: {clean}   <- required, or Grover cannot interfere")

print("\n\nPart 2: amplitude amplification, executed")
print("=" * 66)
run_grover_demo()
