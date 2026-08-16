"""Search the design space and rank the survivors for a specific machine.

    python examples/06_compare_designs.py

Runs on toy4 so it finishes in under a minute. For real SHA-256:
    qsha256 search --rounds 64
"""

from qsha256.quantum.optimization.hardware import rank_for_hardware
from qsha256.quantum.optimization.search import search_designs
from qsha256.spec import TOY4

print("Searching the design space (toy4, 8 rounds)...")
print("Every design is verified against the classical model before it is scored.\n")
result = search_designs(TOY4, rounds=8, verify_trials=2)
print(result)

verified = sum(p.verified for p in result.points)
unsupported = sum("UNSUPPORTED" in p.verification for p in result.points)
print(f"\n{verified} designs verified, {unsupported} unsupported (QFT adder leaves")
print(
    "the computational basis, so the basis simulator cannot execute it), "
    f"{len(result.points) - verified - unsupported} failed."
)

print("\n\nThe Pareto front says which designs are defensible, not which to use.")
print("That depends on what the machine is short of:\n")
for model in ("superconducting", "optimistic"):
    ranking = rank_for_hardware(result.front, model)
    print(ranking)
    print()

print("Note the ordering can change with the machine. Ranking circuits by")
print("logical gate count alone can pick the wrong one.")
