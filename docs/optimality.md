# Optimality: how close to the floor?

Most of this project measures what the circuits cost. This part measures what
they *could* cost, which is the more useful question.

## Multiplicative complexity

Multiplicative complexity (MC) is the minimum number of AND gates needed to
compute a Boolean function over `{AND, XOR, NOT}`. It bounds reversible
circuits directly:

- a reversible circuit's non-linear cost is its Toffoli count, and a Toffoli is
  an AND;
- XOR, NOT and the free wire permutations contribute nothing non-linear;
- so **any** reversible implementation of a function with multiplicative
  complexity `c` needs at least `c` Toffolis.

Bounds come from two sources, kept distinct: exhaustive search over small
circuit shapes (which *proves* a lower bound), and published results for
families too large to search.

## Results

| Component (32-bit) | Achieved | Floor | |
|---|---:|---:|---|
| Ch | 32 | 32 | **optimal** |
| Maj | 32 | 32 | **optimal** |
| sigma functions | 0 | 0 | **optimal** (affine) |
| Gidney adder | 31 | 31 | **optimal** |
| CDKM adder | 64 | 31 | 2.06x |
| VBE adder | 124 | 31 | 4.00x |

`MC(Ch) = MC(Maj) = 1` is **proved here by exhaustive search**, not assumed. The
adder floor is `MC(add mod 2^n) = n - 1` (Boyar–Peralta).

For the whole 64-round compression:

| Architecture | Achieved ANDs | Floor | Overhead |
|---|---:|---:|---:|
| CDKM | 46,592 | 22,696 | 2.05x |
| **Gidney** | **22,696** | **22,696** | **1.00x** |

The Gidney configuration attains the floor **exactly**. It is not merely a good
implementation; no reversible circuit built from these components can use fewer
non-linear gates.

Two caveats the report states rather than glosses: the composed floor bounds
circuits built from these components *separately*, since a cleverer circuit
might share non-linear work between them; and MC bounds the **AND count**, so a
T-count floor follows only once a decomposition is fixed.

## Reversible pebbling

The message schedule's space/recomputation tradeoff is exactly the **reversible
pebble game** (Bennett 1989), solvable exactly by SAT following Meuli et al.
(DATE 2019):

- a pebble on a node means the value is held in a register;
- a pebble may be placed only when all predecessors are pebbled;
- a pebble may be **removed** under the same condition, because uncomputing
  means running the computation backwards;
- the pebble count is the register count.

**Result: 16 registers is provably optimal for SHA-256.** 15 is proved
impossible. The rolling schedule is not just good, it is optimal. Likewise 4 for
toy4 and toy8.

### The move the textbook game is missing

Classical reversible pebbling said toy4 needs 5 registers while our circuit
demonstrably uses 4. The classical game has no move for an **in-place
transformation**: it charges a pebble for the new value while the old one is
still held.

SHA-256's recurrence has `W[t-16]` as an *addend* of `W[t]`, so accumulating the
other three terms into that register turns it into `W[t]` without the two ever
coexisting. Adding that move makes the model agree with the implementation.

The disagreement beforehand is the useful part: a formalisation is only as
truthful as its move set, and a model that declares your working circuit
impossible is telling you about the model.

Results are typed rather than blurred: a **strategy** is a witness that a bound
suffices, an **impossibility** is a proof that it does not, and a timeout is
**unknown** and never counted as either.

## Superoptimization

For blocks small enough to exhaust, optimality can be settled outright by
meet-in-the-middle synthesis: enumerate every circuit of length up to `L/2` from
the identity, do the same backwards from the target, and look for a permutation
both reach. Depth-first search is hopeless — 28 gates on 4 qubits makes length 8
about `4x10^11` nodes — while meeting in the middle needs two searches of length
4.

The result is more interesting than confirmation:

| Primitive | Shortest circuit | | qSHA256 | |
|---|---:|---:|---:|---:|
| | gates | Toffoli | gates | Toffoli |
| Ch | 3 | 2 | 4 | **1** |
| Maj | 3 | 3 | 6 | **1** |

**The shortest circuit is not the cheapest circuit.** Minimising gate count is
the wrong objective under fault tolerance, where Cliffords are nearly free and
only the Toffolis are paid for. That an exhaustive circuit search and an
algebraic multiplicative-complexity argument independently agree on 1 Toffoli
being achievable is the reassuring part.

## Running it

```bash
qsha256 bounds --adder gidney      # achieved against the floor
qsha256 pebble --steps 48          # optimal register count
qsha256 pebble --classical-game    # the textbook game, for contrast
```
