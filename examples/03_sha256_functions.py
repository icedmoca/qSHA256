"""Ch, Maj and the sigma functions as reversible circuits.

    python examples/03_sha256_functions.py

Shows the two facts that shape the whole cost model: sigma functions are free of
Toffolis, and Ch/Maj need exactly one Toffoli per bit.
"""

import itertools

from qsha256.classical.sha256 import big_sigma0, ch, maj, small_sigma0
from qsha256.quantum.primitives.boolean import ch_word_into, maj_word_into
from qsha256.quantum.registers import CircuitBuilder
from qsha256.quantum.sha256.functions import big_sigma0_into, small_sigma0_into
from qsha256.spec import SHA256
from qsha256.validation.basis_sim import BasisSimulator

print("Ch and Maj -- one Toffoli per bit, zero ancillas")
print("=" * 60)
print("  Ch(x,y,z)  = z XOR (x AND (y XOR z))")
print("  Maj(x,y,z) = x XOR ((x XOR y) AND (x XOR z))")
print("\nBoth rewrites put ALL the non-linearity in a single AND. A naive")
print("transcription of the textbook formulas needs 2 and 3 Toffolis per bit.\n")

for fn, ref, name in ((ch_word_into, ch, "Ch"), (maj_word_into, maj, "Maj")):
    b = CircuitBuilder(name)
    words = [b.add_word(3, c) for c in "xyzt"]
    fn(b, *words)
    sim = BasisSimulator(b.circuit)
    ok = all(
        sim.read(sim.run(sim.load(dict(zip(words[:3], v))))[0], words[3]) == (ref(*v) & 0b111)
        for v in itertools.product(range(8), repeat=3)
    )
    ops = b.circuit.count_ops()
    print(
        f"  {name:4s} 3-bit: {ops['ccx']} Toffoli, {ops['cx']} CNOT, "
        f"{b.ancilla_qubits} ancilla -- all 512 inputs correct: {ok}"
    )

print("\nA 1-bit Ch circuit:")
b = CircuitBuilder("ch1")
x, y, z, t = (b.add_word(1, c) for c in "xyzt")
ch_word_into(b, x, y, z, t)
print(b.circuit.draw(output="text", fold=100))

print("\n\nSigma functions -- ZERO Toffolis, zero ancillas")
print("=" * 60)
print("  Sigma0(x) = ROTR^2(x) XOR ROTR^13(x) XOR ROTR^22(x)")
print("  sigma0(x) = ROTR^7(x) XOR ROTR^18(x) XOR SHR^3(x)")
print("\nRotations and shifts are wire relabellings, so a sigma function is")
print("nothing but a CNOT network.\n")

for fn, ref, name, expect in (
    (big_sigma0_into, big_sigma0, "Sigma0", "3 rotations x 32 = 96"),
    (small_sigma0_into, small_sigma0, "sigma0", "32 + 32 + (32-3) = 93"),
):
    b = CircuitBuilder(name)
    src, dst = b.add_word(32, "x"), b.add_word(32, "t")
    fn(b, src, dst, SHA256)
    sim = BasisSimulator(b.circuit)
    value = 0xDEADBEEF
    out, _ = sim.run(sim.load({src: value}))
    ops = b.circuit.count_ops()
    print(f"  {name:7s} {ops.get('ccx', 0)} Toffoli, {ops['cx']} CNOT  ({expect})")
    print(
        f"          {name}(0x{value:08x}) = 0x{sim.read(out, dst):08x}  "
        f"matches classical: {sim.read(out, dst) == ref(value, SHA256)}"
    )

print("\nSHR^3 contributes 29 CNOTs, not 32: three source positions are the")
print("constant zero and emit nothing. A shift is CHEAPER than a rotation here.")
