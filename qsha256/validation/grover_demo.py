"""The runnable Grover demonstration behind ``qsha256 grover-demo``.

This is the one place in the repository where a genuinely quantum effect --
superposition, phase kickback, amplitude amplification -- is simulated rather
than counted.  It runs on a reduced toy hash, never on SHA-256, because a
SHA-256 oracle is ~2600 qubits and statevector simulation of that is impossible.
"""

from __future__ import annotations

from ..quantum.oracle.grover import build_toy_grover
from ..quantum.oracle.toy import TOY_TINY

__all__ = ["run_grover_demo"]


def run_grover_demo(iterations: int | None = None, compare_bits: int = 4) -> bool:
    from qiskit.quantum_info import Statevector

    spec = TOY_TINY
    builder, message, iterations, target, solutions = build_toy_grover(
        iterations=iterations, compare_bits=compare_bits
    )
    circuit = builder.circuit
    search_bits = spec.message_words * spec.word_bits

    print("qSHA256 toy Grover demonstration")
    print("=" * 66)
    print(
        "\nThis is a REDUCED TOY HASH, not SHA-256. It has the same reversible\n"
        "architecture (modular addition, Ch, Maj, sigma functions) at a size a\n"
        "statevector simulator can actually execute.\n"
    )
    print(f"  toy spec:          {spec.name} ({spec.word_bits}-bit words, {spec.rounds} rounds)")
    print(f"  search space:      2^{search_bits} = {2 ** search_bits} candidates")
    print(f"  target digest:     0x{target:x} (low {compare_bits} bits)")
    print(f"  planted solutions: {solutions}  (found by classical brute force)")
    print(f"  Grover iterations: {iterations}")
    print(f"  circuit:           {circuit.num_qubits} qubits, "
          f"{sum(circuit.count_ops().values()):,} gates, depth {circuit.depth():,}")

    print("\nSimulating (statevector)...")
    state = Statevector.from_instruction(circuit)
    indices = [circuit.find_bit(q).index for word in message for q in word.qubits]
    probabilities = state.probabilities_dict(qargs=indices)

    def decode(bitstring: str) -> tuple[int, ...]:
        value = sum(int(bitstring[::-1][i]) << i for i in range(len(bitstring)))
        return tuple(
            (value >> (i * spec.word_bits)) & spec.mask for i in range(spec.message_words)
        )

    print(f"\n  {'candidate':<16}{'probability':>14}   preimage?")
    print("  " + "-" * 46)
    for bits, probability in sorted(probabilities.items(), key=lambda kv: -kv[1])[:6]:
        candidate = decode(bits)
        print(f"  {str(candidate):<16}{probability:>14.4f}   "
              f"{'YES' if candidate in solutions else 'no'}")

    found = sum(p for bits, p in probabilities.items() if decode(bits) in solutions)
    uniform = len(solutions) / 2**search_bits
    print(
        f"\n  probability on a true preimage: {found:.4f}"
        f"\n  before amplification:           {uniform:.4f}"
        f"\n  amplification factor:           {found / uniform:.1f}x"
    )
    success = found > 0.5
    print("\n" + ("PASS: amplitude amplification concentrated the amplitude on a preimage."
                  if success else "FAIL: amplification did not succeed."))
    return success
