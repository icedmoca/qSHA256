"""One SHA-256 round, and the trick that makes it allocate nothing.

python examples/04_one_round.py
"""

import random

from qsha256.classical.sha256 import round_step
from qsha256.quantum.sha256.round import build_round_circuit
from qsha256.quantum.strategies import Strategy
from qsha256.spec import SHA256, TOY4
from qsha256.validation.basis_sim import BasisSimulator

print("The classical round")
print("=" * 70)
print("""
  T1 = h + Sigma1(e) + Ch(e,f,g) + K[t] + W[t]
  T2 = Sigma0(a) + Maj(a,b,c)
  (a,b,c,d,e,f,g,h) <- (T1+T2, a, b, c, d+T1, e, f, g)

Six of the eight assignments are pure RENAMING. In a circuit, renaming costs
nothing -- it is a permutation of references to registers. Only 'a' and 'e'
are computed, and both accumulate into a register that is about to die:

  register h:  +Sigma1(e) +Ch +K +W = T1,  then +Sigma0(a) +Maj = T1+T2 -> new a
  register d:  +T1                                                     -> new e

So the round allocates NO permanent qubits. Its only work space is a borrowed
temporary, uncomputed before the round ends.
""")

print("A toy4 round (4-bit words), verified against the classical round")
print("=" * 70)
b, st_in, w, st_out = build_round_circuit(TOY4, Strategy(), t=0)
sim = BasisSimulator(b.circuit)
values = [1, 2, 3, 4, 5, 6, 7, 8]
out, _ = sim.run(sim.load(dict(zip(st_in, values)) | {w: 9}))
expected, trace = round_step(tuple(values), 9, TOY4.k[0], TOY4)
got = tuple(sim.read(out, r) for r in st_out)
print(f"  in : {values}  W=9  K={TOY4.k[0]}")
print(f"  out: {list(got)}")
print(f"  classical: {list(expected)}    match: {got == expected}")
print(f"  T1={trace.t1} T2={trace.t2}")
print(
    f"\n  circuit: {b.circuit.num_qubits} qubits "
    f"({b.data_qubits} data + {b.ancilla_qubits} ancilla), "
    f"{sum(b.circuit.count_ops().values())} gates"
)
print("  the same 9 registers come out; nothing was allocated for the result")

print("\n\nA real 32-bit round, three layouts")
print("=" * 70)
rng = random.Random(0)
print(f"{'layout':<10}{'qubits':>9}{'Toffoli':>10}{'CNOT':>9}{'depth':>9}  correct")
print("-" * 70)
for layout in ("serial", "wide", "csa"):
    b, st_in, w, st_out = build_round_circuit(SHA256, Strategy(round_layout=layout), t=0)
    sim = BasisSimulator(b.circuit)
    ok = True
    for _ in range(5):
        values = [rng.getrandbits(32) for _ in range(8)]
        wv = rng.getrandbits(32)
        out, _ = sim.run(sim.load(dict(zip(st_in, values)) | {w: wv}))
        exp, _ = round_step(tuple(values), wv, SHA256.k[0], SHA256)
        ok &= tuple(sim.read(out, r) for r in st_out) == exp
    ops = b.circuit.count_ops()
    print(
        f"{layout:<10}{b.circuit.num_qubits:>9}{ops.get('ccx', 0):>10}"
        f"{ops.get('cx', 0):>9}{b.circuit.depth():>9}  {ok}"
    )

print("\n576 Toffolis per round = 7 modular additions x 64, plus Ch and Maj")
print("computed and uncomputed (32 each). Addition dominates.")
