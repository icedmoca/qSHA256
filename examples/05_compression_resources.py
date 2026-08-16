"""Full 64-round SHA-256: build it, run it against hashlib, measure it.

python examples/05_compression_resources.py
"""

import hashlib
import time

from qsha256.classical.sha256 import pad_message, parse_blocks
from qsha256.quantum.resources import analyze
from qsha256.quantum.sha256.compression import build_compression
from qsha256.quantum.strategies import Strategy
from qsha256.spec import SHA256
from qsha256.validation.basis_sim import BasisSimulator

print("Building the full 64-round, 32-bit SHA-256 compression circuit...")
start = time.time()
comp = build_compression(SHA256, Strategy(), initial_state=SHA256.h0)
print(
    f"  built in {time.time() - start:.1f}s: {comp.circuit.num_qubits:,} qubits, "
    f"{len(comp.circuit.data):,} gates"
)

print("\nExecuting it on a computational basis state...")
print("  (possible because it is a permutation circuit -- no statevector needed;")
print("   a statevector for 1,057 qubits would not fit in the observable universe)")
blocks = parse_blocks(pad_message(b"abc"), SHA256)
start = time.time()
sim = BasisSimulator(comp.circuit)
out, _ = sim.run(sim.load(dict(zip(comp.message, blocks[0]))))
digest = b"".join(sim.read(out, r).to_bytes(4, "big") for r in comp.state)
print(f"  executed in {time.time() - start:.1f}s")
print(f"\n  circuit output: {digest.hex()}")
print(f"  hashlib:        {hashlib.sha256(b'abc').hexdigest()}")
print(f"  MATCH:          {digest == hashlib.sha256(b'abc').digest()}")

print("\n" + "=" * 70)
report = analyze(
    comp,
    spec=SHA256,
    strategy=comp.strategy,
    rounds=64,
    target="SHA-256 compression",
    simulated=True,
    reproduce="qsha256 analyze --rounds 64",
)
print(report)
