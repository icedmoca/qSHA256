# Claims register

Every non-trivial claim this project makes, stated precisely, with what
establishes it, what it assumes, how to reproduce it, and what would falsify it.

The purpose is to make the assumptions attackable. A claim whose conditions are
not written down cannot be reviewed, and an unreviewable claim is not a result.
Each entry ends with the strongest objections we know of and an honest answer —
sometimes a rebuttal, sometimes a concession.

**Reproduce everything:** `python scripts/reproduce.py`

---

## Summary of status

| # | Claim | Status |
|---|---|---|
| C1 | The 64-round 32-bit circuit computes SHA-256 | **Verified by execution** + SAT-proved compositionally |
| C2 | `MC(Ch) = MC(Maj) = 1` | **Proved** (exhaustive, unconditional) |
| C3 | The Gidney adder uses `n-1` ANDs, the published minimum | **Proved** (unconditional) |
| C4 | The full circuit uses 22,696 AND computations | **Measured**, confirmed by 3 counters |
| C5 | 22,696 attains the composed floor | **Conditional** — architecture-class bound only |
| C6 | 16 registers suffice for the message schedule | **Proved** (witness) |
| C7 | 15 registers do not | **Conditional** — within a bounded move budget |
| C8 | T-count 90,784 at 1,119 logical qubits | **Analytical**, from a stated decomposition |
| C9 | Below Amy et al.'s 228,992 T | **Conditional** — different machine model |
| C10 | Grover oracle costs 2.02x a forward hash | **Measured** |

---

## C1 — The circuit computes SHA-256

**Statement.** The reversible circuit built by `build_compression(SHA256,
rounds=64)` maps `(H, M)` to SHA-256's compression of block `M` into chaining
value `H`, for every input.

**How established.** Two independent ways.

*By execution.* Every compute circuit here uses only X, CNOT, Toffoli and Gidney
AND gates, so it is a permutation circuit and can be executed exactly on
computational basis states in time linear in the gate count. The 1,057-qubit
circuit is run and its digest compared against `hashlib.sha256`, including
multi-block chaining and the NIST CAVP vectors. This is exact, not sampled — but
it covers only the inputs tried.

*By proof.* Symbolic execution into an And-Inverter Graph, Tseitin-encoded to
CNF, discharged by SAT. UNSAT means the circuit and the specification agree on
**all** inputs. Proved at full 32-bit width: all four adders, `Ch`, `Maj`, the
four sigma functions, the round in every layout, both schedule strategies,
copy-in and chaining.

**Assumptions.**

- The specification is correct. Mitigated by writing it twice, in code that
  shares nothing: `classical/sha256.py` on Python integers,
  `formal/spec_aig.py` on Boolean formulas. Both are checked against `hashlib`
  and against published test vectors.
- The whole-circuit proof is **compositional**, not one monolithic query. Each
  component is proved universally quantified over its own inputs, so chaining
  them is sound by induction; `prove_structure` then checks the circuit really
  is that composition, with no gate outside a proved component.

**Reproduce.** `qsha256 validate` and `qsha256 prove --scope standard`

**Falsified by.** Any input where the circuit's digest differs from `hashlib`;
any SAT query returning SAT on an equivalence obligation.

**Hostile review.**

> *A compositional proof is not a proof of the whole circuit.*

Partly conceded. The induction is valid because each component proof is
universally quantified, and `prove_structure` closes the "are these really the
components" gap by checking the instruction spans partition the circuit exactly.
What remains unproved by machine is the induction step itself, which is ordinary
mathematics stated in `docs/formal-verification.md`. A single monolithic miter
would be stronger; it does not scale, and we say why (the circuit's adder and
the specification's adder encode carries differently, so the solver reconciles
two representations afresh at every one of ~450 additions).

> *Basis-state execution misses phase behaviour.*

Correct, and irrelevant for these circuits: a permutation circuit's action on
basis states determines it up to global phase. Where phase matters — the Grover
oracle — the basis simulator tracks the sign explicitly, and superposition
behaviour is checked with a statevector on toy instances.

---

## C2 — `MC(Ch) = MC(Maj) = 1`

**Statement.** The Boolean functions `Ch(x,y,z) = (x AND y) XOR (NOT x AND z)`
and `Maj(x,y,z)` each have multiplicative complexity exactly 1: neither is
affine, and each is expressible as an affine function XOR one product of affine
functions.

**How established.** Exhaustive search. There are `2^(n+1) = 16` affine
functions of 3 variables; the search checks affineness (rules out `MC = 0`) and
then every `a XOR (b AND c)` with `a, b, c` affine. Finding a match after ruling
out `MC = 0` proves `MC = 1` — the witness gives the upper bound and the
exhausted search the lower bound.

**Assumptions.** None beyond the definition of MC over `{AND, XOR, NOT}` with
unbounded fan-out. This is a statement about the functions and holds against any
circuit anyone writes.

**Reproduce.** `qsha256 bounds`, or `multiplicative_complexity(...)` directly.
A deliberately harder control (`x AND y AND z`) is confirmed to need 2.

**Falsified by.** Exhibiting an affine expression for either function.

**Hostile review.**

> *MC bounds an irreversible circuit. Does it bound a reversible one?*

It does, in the needed direction. A reversible circuit's Toffolis each compute
one AND, so erasing the reversibility structure yields an `{AND, XOR, NOT}`
circuit with the same AND count. Hence `#Toffoli >= MC`. Ancillas do not help:
they add wires, not non-linearity.

---

## C3 — The Gidney adder attains the adder floor

**Statement.** `MC(add mod 2^n) = n - 1` (Boyar–Peralta). The Gidney adder uses
exactly `n - 1` AND computations, so it attains the floor. CDKM uses `2n`
(2.06x at 32 bits) and VBE `4(n-1)` (4.00x).

**How established.** The floor is published; the achieved count is measured off
the constructed circuit and confirmed by three independent counters (C4).

**Reproduce.** `qsha256 bounds --adder gidney`

**Hostile review.**

> *You are citing the floor, not proving it.*

Conceded and labelled: `bound_source` records "published" versus "exhaustive
search here" for every component, and the report distinguishes them.

---

## C4 — 22,696 AND computations

**Statement.** The 64-round 32-bit forward compression built with
`Strategy(adder="gidney")` contains 22,696 `and_g` gates and **zero** Toffoli
gates.

**How established.** Measured off the constructed circuit, and independently
confirmed by Google's Qualtran (via Cirq) and by a counter that re-derives
everything from exported OpenQASM 3 *text*, sharing no data structure with the
analyzer. All three agree.

**Assumptions.** The unit is AND **computations**. Each is paired with an
`and_g_dg` uncomputation — also 22,696 — which costs no T gates but is a real
mid-circuit measurement. Anyone comparing against a Toffoli count that includes
uncomputation must double this.

**Reproduce.** `qsha256 crosscheck --adder gidney`

---

## C5 — 22,696 attains the composed floor

**This is the claim to attack, and the one most easily overstated.**

**Statement, stated carefully.** For circuits that (a) compute `Ch`, `Maj` and
the sigma functions as separate bitwise operations and (b) form every sum as a
chain of *pairwise* modular additions, the AND count is at least 22,696; the
Gidney construction achieves exactly that.

**What it is NOT.** It is **not** a lower bound on the multiplicative complexity
of SHA-256's compression function. It does not show that no reversible
implementation of SHA-256 can use fewer than 22,696 non-linear operations.

**Assumptions, itemised.**

1. Components are computed separately, with no non-linear work shared across
   their boundaries.
2. Multi-operand sums are formed as chained pairwise additions, each charged
   `n - 1`.
3. The unit is AND computations, not AND gates.

**Reproduce.** `qsha256 bounds --adder gidney`

**Hostile review.**

> *Assumption 1 is doing a lot of work. A circuit could reuse an AND computed
> inside `Ch` to help a neighbouring carry chain.*

**Conceded.** Nothing here rules that out. General non-linear lower bounds are
notoriously hard, and the composed figure is a bound for an architecture class,
not for the function. This is why the phrasing throughout is "attains the
composed bound", never "optimal".

> *Assumption 2 is the sharper problem. A round forms `T1 = h + Sigma1(e) + Ch +
> K + W` — five operands — as four chained additions, charged `4(n-1)`. Is the
> multiplicative complexity of the five-operand sum mod `2^n` really `4(n-1)`?*

**Conceded, and this is the strongest objection.** It is not known to be. The
degree bound yields only `n - 1` for the multi-operand sum. A fundamentally
different multi-operand construction could plausibly beat the composed figure.
The project even has a data point in that direction: carry-save addition reduces
`k` operands with `k-2` constant-depth layers, and while it lost here once
uncomputation was charged, its *forward* AND count is lower than chained
pairwise addition.

> *So what survives?*

The component-level results (C2, C3) survive unconditionally — they are
statements about functions. What C5 adds is that the construction wastes nothing
*relative to its own decomposition*: every AND it computes is one the
decomposition demands. That is a meaningful engineering statement and a weak
mathematical one, and the report now says so in those terms.

---

## C6 / C7 — The message schedule's register count

**C6 statement.** 16 registers suffice to compute all 48 expanded schedule words,
within 48 moves. Established by a **witness**: SAT produced an explicit move
sequence.

**C7 statement.** No strategy exists with 15 registers **within a move budget of
48, 64, 96, 128, 192 or 256** — the last being 5.3x the 48-move minimum.
Established by UNSAT at each budget.

**The model is part of the theorem.** Rules in force:

1. Initially the 16 input nodes are pebbled, nothing else.
2. `place(v)` requires every predecessor of `v` pebbled.
3. `remove(v)` requires the same — uncomputing means running backwards.
4. `move(u -> v)` transforms `u`'s register into `v` in place, requiring `u` to
   be a predecessor of `v` and every other predecessor pebbled.
5. One move per step. For a *space* question this is WLOG: simultaneous moves
   cannot lower the peak number of pebbled nodes.
6. Cost is the maximum number of simultaneously-pebbled nodes.
7. The DAG is the schedule's dependency graph at **word** granularity.

**Reproduce.** `qsha256 pebble --steps 96`

**Hostile review.**

> *An impossibility within 256 moves is not an impossibility.*

**Conceded.** Extra moves buy recomputation, which is exactly what trades
against registers, so the step budget is load-bearing. What is established is
that 15 registers is impossible within budgets up to 5.3x the minimum, checked
at six budgets, with no sign of the answer changing. An unbounded-step lower
bound is not established, and we do not claim one.

> *Rule 4 is not part of the classical reversible pebble game. Did you add a
> move to make your circuit look optimal?*

The move was added because the classical game declared the **existing, working,
executed** circuit impossible — it said toy4 needs 5 registers while the circuit
demonstrably uses 4. SHA-256's recurrence has `W[t-16]` as an *addend* of `W[t]`,
so accumulating the other three terms into that register turns it into `W[t]`
without the two ever coexisting; the classical game has no move for that and
charges a pebble for both. Fixing the model rather than the circuit was the
correct response, and the effect is visible: `--classical-game` reproduces the
old, wrong answer.

> *Word granularity is an assumption.*

Yes. A circuit that restructured the recurrence algebraically, or worked at bit
granularity, is outside the model entirely.

---

## C8 — T-count 90,784 at 1,119 logical qubits

**Statement.** Under the Gidney decomposition — 4 T per AND computation, 0 per
uncomputation — the 64-round forward circuit has T-count 90,784 and width 1,119
logical qubits.

**Assumptions.** The decomposition is stated and is the whole content of the
number: the same circuit under the standard 7-T Toffoli decomposition would cost
more. Logical qubits, not physical. Uncomputation is free in T but consumes
22,696 mid-circuit measurements and requires classical feedforward.

**Reproduce.** `qsha256 analyze --rounds 64 --adder gidney`

**Hostile review.**

> *"T-count" without a decomposition is meaningless.*

Agreed, which is why every report names its model, and why three documented
Toffoli decompositions are offered rather than one hard-coded.

---

## C9 — Below Amy et al.'s published figure

**Statement.** Amy et al. (SAC 2016) report 228,992 T at 2,402 logical qubits
for SHA-256 after T-par optimization. This project reaches 90,784 T at 1,119
logical qubits — 60.4% lower — **under a different machine model**, and 181,568
T (20.7% lower) under a comparable one.

**Assumptions, and the one that matters.** The 90,784 figure uses
measurement-based uncomputation, which requires mid-circuit measurement and
classical feedforward. Amy et al.'s circuit is unitary and assumes no such
capability — Gidney's construction postdates their paper by two years.
Comparing the two is a comparison between *machine models*.

The like-for-like comparison is the unitary phase-folded circuit at 181,568 T,
which is 20.7% below their figure at 44% of the qubits.

**Reproduce.** `qsha256 leaderboard`

**Hostile review.**

> *You are comparing your best case against their published case.*

Which is why the leaderboard prints both rows, annotates the machine-model
difference on every affected metric, and refuses to compute ratios for metrics
that are not comparable (a Toffoli-level depth is never divided by a Clifford+T
depth).

> *Their figures were transcribed by you.*

From Table 1 of ePrint 2016/992, page 10, cited to the row. Quantities the paper
does not report are `None`, never inferred. The one inferred value — their
Toffoli count as T-count/7 — is flagged as an inference.

---

## C10 — A Grover query costs 2.02x a forward hash

**Statement.** The full preimage oracle (garbage-free forward hash, 256-bit
digest comparison, phase flip, inverse hash) has 94,202 Toffolis against the
forward circuit's 46,592: a ratio of 2.02.

**How established.** Both circuits constructed and counted. The oracle's phase
behaviour is verified **exhaustively** over an entire toy search space: it
flips exactly the true preimages and leaves no garbage.

**Reproduce.** `qsha256 oracle`

---

## Standing limitations

Nothing here has run on quantum hardware. Full-scale SHA-256 has never been
simulated in superposition. Grover has never been run against real SHA-256. See
`docs/limitations.md`.
