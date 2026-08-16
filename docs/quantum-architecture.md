# The quantum architecture

How qSHA256 lays SHA-256 out on qubits, and why.

## Register map (default architecture, 64 rounds)

```text
H0..H7      8 x 32 = 256 qubits   chaining state
M0..M15    16 x 32 = 512 qubits   message block / rolling schedule window
wv0..wv7    8 x 32 = 256 qubits   working variables a..h
ancilla pool         33 qubits    recycled: one 32-bit temporary + 1 adder carry
                    ----------
                          1,057 qubits total
```

Add 256 more for the digest register when uncomputation is enabled (1,313), and
255 more for the oracle's comparison tree (1,535).

## The central trick: the round is almost in-place

The classical round renames six of its eight state words. In a circuit, renaming
is free — it is a permutation of Python references to registers, emitting no
gates at all. So the round only has to *compute* two words, and both are formed
by accumulating into a register that is about to become dead:

```text
register h   +Sigma1(e)  +Ch  +K  +W   =  T1
             +Sigma0(a)  +Maj          =  T1 + T2   ->  renamed to a
register d   +T1                       =  new e
```

The old `h` is consumed by `T1` and never referenced again, so it can serve as
the accumulator. The result: **a SHA-256 round allocates no permanent qubits.**
Its only work space is a borrowed 32-qubit temporary, uncomputed and returned to
the pool before the round ends.

What remains is seven modular additions per round, and that is where essentially
the whole cost lives.

## Data flow

```text
message block M ---> message schedule ---> W[t]
                                             |
chaining state H --copy--> a..h --- 64 rounds ---> a'..h'
                                             |
                                    H_out = H + a'..h'
                                             |
                   (garbage-free mode: run the whole forward span backwards,
                    restoring a..h, the schedule and M, leaving only H_out)
```

## Design axes

Every axis below produces a *correct* circuit; they differ only in cost. This is
what makes exhaustive search possible.

| Axis | Options | Trades |
|---|---|---|
| `adder` | `cdkm`, `vbe`, `qft` | Toffoli count vs ancillas vs gate basis |
| `const_add` | `load`, `vbe_const` | a temporary register vs specialised gates |
| `schedule` | `rolling`, `store_all` | 1,536 qubits vs two sigma folds per word |
| `round_layout` | `serial`, `wide`, `csa` | width vs depth |
| `uncompute_working` | `false`, `true` | garbage vs roughly 2x the cost |

### Adders

`cdkm` (Cuccaro et al. 2004) is the default: 2n Toffoli, 4n CNOT, **one** ancilla,
with the carry rippling through the addend register rather than through scratch
space. `vbe` (Vedral et al. 1996) keeps carries in a dedicated register — twice
the Toffolis and n times the ancillas, included so the benchmark can show that.
`qft` (Draper 2000) is ancilla-free and Toffoli-free, and is a trap: its
arbitrary-angle rotations have no exact Clifford+T form, and synthesising them
costs far more T gates than the Toffolis they replaced. The measured figures are
in `benchmarks/results/latest.md`.

### Message schedule

`store_all` gives every `W[t]` its own register. Because the target starts in
`|0>`, the first term of the recurrence is a free CNOT copy rather than an
addition — so it uses only three adders per word. `rolling` keeps 16 registers
and transforms `W[t-16]` **in place** into `W[t]`, which works because `W[t-16]`
is itself an addend of the recurrence.

Both use the same number of adders. They differ in CNOTs (rolling pays two extra
sigma folds per word) and in qubits (store_all pays 1,536 more). That is a
measured qubit-versus-gate trade, not a folk assumption.

### Round layout

`serial` recycles one temporary. `wide` computes the four independent
sub-expressions into separate temporaries so their gate layers can overlap:
measured at **25% lower depth for 128 more qubits, at identical T-count** — the
best trade in the space.

`csa` replaces the chain of ripple adders with a carry-save tree plus one carry
propagation. On paper it should win: three constant-depth CSA layers (31 Toffoli
each) plus one CDKM adder is 157 Toffoli against four CDKM adders at 256. It
loses anyway, because every CSA intermediate must be uncomputed, which doubles
the tree. **Measured: 58,496 Toffoli against 46,592 for `serial`.** A negative
result, reported because it is one.

## What the oracle adds

```text
|candidate> |0>
   --- forward SHA-256 --->     |candidate> |digest>
   --- compare, phase flip --->  phase = -1 iff digest == target
   --- inverse SHA-256 --->     |candidate> |0>
```

The oracle supplies its own inverse, so the compression inside it is built
*without* an internal uncomputation — doing both would run the rounds four times
where two suffice. Measured oracle cost is **2.02x** the forward circuit.

The digest comparison is a marker qubit plus a balanced AND tree over 256 bits:
255 ancillas, ~1,018 Toffolis. Small next to the hash, which is the useful thing
to know — the comparison is not what makes a preimage oracle expensive.
