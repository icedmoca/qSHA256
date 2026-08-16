"""Reversible modular addition: three published designs, measured and compared.

    python examples/02_reversible_adder.py

Addition is the cost centre of quantum SHA-256 -- seven per round -- so the
adder choice propagates into every headline number.
"""

import itertools

from qsha256.quantum.primitives.add import ADDERS, add_into
from qsha256.quantum.registers import CircuitBuilder
from qsha256.validation.basis_sim import BasisSimulator

print("A 4-bit CDKM adder, executed on every input pair")
print("=" * 52)
b = CircuitBuilder("cdkm4")
a, t = b.add_word(4, "a"), b.add_word(4, "b")
add_into(b, a, t, "cdkm")
sim = BasisSimulator(b.circuit)
print(b.circuit.draw(output="text", fold=110))

ok = True
for x, y in itertools.product(range(16), repeat=2):
    out, _ = sim.run(sim.load({a: x, t: y}))
    if sim.read(out, t) != (x + y) % 16 or sim.read(out, a) != x:
        ok = False
print(f"\nAll 256 input pairs correct: {ok}")
print("Note the addend register is unchanged, and the single carry ancilla")
print("returns to |0> -- that is what makes this usable inside a larger circuit.\n")

print("32-bit comparison")
print("=" * 78)
print(f"{'adder':<8}{'ancillas':>10}{'Toffoli':>10}{'CNOT':>10}{'depth':>10}  notes")
print("-" * 78)
for name, adder in ADDERS.items():
    b = CircuitBuilder(name)
    a, t = b.add_word(32, "a"), b.add_word(32, "b")
    add_into(b, a, t, name)
    ops = b.circuit.count_ops()
    note = "" if adder.native_clifford_t else "NOT natively Clifford+T"
    print(
        f"{name:<8}{b.ancilla_qubits:>10}{ops.get('ccx', 0):>10}"
        f"{ops.get('cx', 0):>10}{b.circuit.depth():>10}  {note}"
    )

print("\nThe QFT adder appears free of Toffolis. It is not free: its")
print("arbitrary-angle rotations must be synthesised from Clifford+T, which")
print("costs far more T gates than the Toffolis it replaced. See example 05.")
for name, adder in ADDERS.items():
    print(f"\n  {name}: {adder.reference}")
