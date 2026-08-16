"""Cutting the T-count: Gidney temporary ANDs and phase-polynomial folding.

    python examples/08_t_count_optimization.py

T gates dominate fault-tolerant cost, so this is where the real optimization
happens. Two independent techniques, and they compose.
"""

from qsha256 import SHA256
from qsha256.quantum.optimization.phase_fold import phase_fold
from qsha256.quantum.optimization.rewrite import apply_rewrites
from qsha256.quantum.primitives.add import add_into
from qsha256.quantum.primitives.temporary_and import (
    gidney_and_circuit,
    gidney_uncompute_circuit,
)
from qsha256.quantum.registers import CircuitBuilder
from qsha256.quantum.resources import analyze
from qsha256.quantum.resources.clifford_t import clifford_t_cost
from qsha256.quantum.sha256.compression import build_compression
from qsha256.quantum.strategies import Strategy

print("1. Gidney's temporary AND")
print("=" * 70)
print("""
A Toffoli costs 7 T because it must work for any target state. But almost every
Toffoli in a reversible circuit writes into a CLEAN ancilla and later uncomputes
it -- and that pair is far cheaper:
""")
compute = gidney_and_circuit()
uncompute = gidney_uncompute_circuit()
c_ops, u_ops = compute.count_ops(), uncompute.count_ops()
print(
    f"  compute   |x>|y>|0> -> |x>|y>|x AND y>   {c_ops.get('t', 0) + c_ops.get('tdg', 0)} T gates"
)
print(
    f"  uncompute (measure + Clifford correction) "
    f"{u_ops.get('t', 0) + u_ops.get('tdg', 0)} T gates, "
    f"{u_ops.get('measure', 0)} measurement"
)
print("\n  So a compute/uncompute pair costs 4 T instead of 2 x 7 = 14.\n")
print("  The compute circuit:")
print(compute.draw(output="text", fold=100))
print("\n  The uncompute -- note it is NOT a unitary circuit:")
print(uncompute.draw(output="text", fold=100))

print("\n\n2. What that does to a 32-bit adder")
print("=" * 70)
print(f"\n{'adder':<10}{'ancillas':>10}{'Toffoli':>10}{'and_g':>8}{'T-count':>10}{'measure':>9}")
print("-" * 70)
for name in ("cdkm", "vbe", "gidney"):
    b = CircuitBuilder(name)
    a, t = b.add_word(32, "a"), b.add_word(32, "b")
    add_into(b, a, t, name)
    ops = dict(b.circuit.count_ops())
    cost = clifford_t_cost(ops)
    print(
        f"{name:<10}{b.ancilla_qubits:>10}{ops.get('ccx', 0):>10}"
        f"{ops.get('and_g', 0):>8}{cost['t_count']:>10}{cost['measurements']:>9}"
    )
print("\n  448 T -> 124 T, a 3.6x reduction on the operation SHA-256 does most.")

print("\n\n3. Phase-polynomial folding (the core of T-par)")
print("=" * 70)
print("""
Between Hadamards, a circuit factors as (linear CNOT map) . (diagonal phase).
The diagonal part depends only on LINEAR FUNCTIONS of the inputs, so two T gates
acting on the same function merge -- even with CNOTs between them:
""")
from qiskit import QuantumCircuit  # noqa: E402

demo = QuantumCircuit(2)
demo.t(0)
demo.cx(0, 1)
demo.cx(0, 1)
demo.t(0)
folded = phase_fold(demo, already_clifford_t=True)
print(f"  T ; CX ; CX ; T   ->  {dict(folded.circuit.count_ops())}")
print(f"  {folded.summary()}")
print("\n  Two T gates became one Clifford S. On a compute/uncompute Toffoli pair:")
pair = QuantumCircuit(3)
pair.ccx(0, 1, 2)
pair.ccx(0, 1, 2)
print(f"  {phase_fold(pair).summary()}")

print("\n\n4. Both, on the real 64-round SHA-256 circuit")
print("=" * 70)
print(
    f"\n{'design':<26}{'Toffoli':>9}{'and_g':>8}{'T-count':>10}{'vs base':>9}"
    f"{'non-Cliff depth':>17}"
)
print("-" * 79)
baseline = None
for label, strategy, fold in [
    ("cdkm (baseline)", Strategy(), False),
    ("cdkm + folding", Strategy(), True),
    ("gidney", Strategy(adder="gidney"), False),
    ("gidney + folding", Strategy(adder="gidney"), True),
]:
    comp = build_compression(SHA256, strategy, rounds=64)
    if fold:
        circuit = apply_rewrites(comp.builder, phase_folding=True).circuit
        report = analyze(circuit, spec=SHA256, strategy=strategy, rounds=64, transpile_t=False)
    else:
        report = analyze(comp, spec=SHA256, strategy=strategy, rounds=64, transpile_t=False)
    ct = report.clifford_t
    baseline = baseline or ct["t_count"]
    delta = f"{100 * (ct['t_count'] / baseline - 1):+.1f}%"
    print(
        f"{label:<26}{report.toffoli_count:>9,}{ct.get('and_compute_gates', 0):>8,}"
        f"{ct['t_count']:>10,}{delta:>9}{report.depth['non_clifford_depth']:>17,}"
    )

print("""
Read the last column with the T-count. Folding buys T-count with depth: merging
phases onto one point serialises them. The Gidney adder improves both at once --
but only on hardware that can measure mid-circuit and act on the result, which
the other designs do not require.

Amy et al. (2016) report 228,992 T after T-par. The unitary comparison here is
'cdkm + folding' at 181,568 T -- 20.7% lower, at 44% of their qubit count.
""")
