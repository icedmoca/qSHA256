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

| Architecture | Achieved ANDs | Floor | |
|---|---:|---:|---|
| CDKM | 46,592 | 22,696 | 2.05x |
| **Gidney** | **22,696** | **22,696** | attains the composed bound |

**This is a weaker statement than it looks, and the difference matters.**

The composed floor is a lower bound for circuits that (a) compute `Ch`, `Maj`
and the sigma functions as separate bitwise operations, and (b) form every sum
as a chain of *pairwise* modular additions. Within that class, the Gidney
construction wastes nothing: every AND it computes is one the decomposition
demands.

It is **not** a lower bound on the multiplicative complexity of SHA-256's
compression function. Two gaps, both conceded:

- A circuit could share non-linear work *across* component boundaries — reusing
  an AND from inside `Ch` to help a neighbouring carry chain. Nothing rules that
  out, and general non-linear lower bounds are hard.
- The floor charges `n-1` per *pairwise* addition, so a round's five-operand
  `T1` is charged `4(n-1)`. But `MC` of the five-operand sum mod `2^n` is not
  known to be `4(n-1)` — the degree bound gives only `n-1`. A different
  multi-operand construction might beat the composed figure. (This project even
  has a hint in that direction: carry-save reduction has a lower *forward* AND
  count, and lost here only once uncomputation was charged.)

A third, smaller caveat: MC bounds the **AND count**, so a T-count floor follows
only once a decomposition is fixed. And the unit is AND *computations* — each is
paired with an uncomputation that is free in T but is a real measurement.

## Reversible pebbling

The message schedule's space/recomputation tradeoff is exactly the **reversible
pebble game** (Bennett 1989), solvable exactly by SAT following Meuli et al.
(DATE 2019):

- a pebble on a node means the value is held in a register;
- a pebble may be placed only when all predecessors are pebbled;
- a pebble may be **removed** under the same condition, because uncomputing
  means running the computation backwards;
- the pebble count is the register count.

**Result: 16 registers suffice, and 15 do not — within a bounded move budget.**
The impossibility was checked at budgets of 48, 64, 96, 128, 192 and 256 moves,
the last being 5.3x the 48-move minimum, and holds at every one.

That bound is load-bearing and the claim is stated with it. Extra moves buy
recomputation, and recomputation is exactly what trades against registers, so
UNSAT at `S` steps proves only that no strategy exists *within `S` moves*. An
unbounded-step lower bound is not established here.

### The rules are part of the theorem

1. Initially the input nodes are pebbled, nothing else.
2. `place(v)` requires every predecessor of `v` pebbled.
3. `remove(v)` requires the same — uncomputing runs the computation backwards.
4. `move(u -> v)` transforms `u`'s register into `v` in place, requiring `u` to
   be a predecessor of `v` and every other predecessor pebbled.
5. One move per step. For a *space* question this is without loss of generality:
   simultaneous moves cannot lower the peak number of pebbled nodes.
6. Cost is the maximum number of simultaneously-pebbled nodes.
7. The DAG is the schedule's dependency graph at **word** granularity. A circuit
   restructuring the recurrence algebraically, or working at bit granularity, is
   outside the model entirely.

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
