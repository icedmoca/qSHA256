# Reversible computing, and why SHA-256 needs it

## The constraint

Quantum evolution is unitary, and unitary maps are invertible. A quantum circuit
therefore cannot do anything irreversible: it cannot overwrite a value, discard a
bit, or map two different inputs to the same output.

Almost every line of classical SHA-256 does exactly those things.

```text
h = g            overwrites h
x & y            two bits in, one bit out
x >> 3           three bits discarded
a + b mod 2^32   the carry out is thrown away
```

None of these can be translated directly. Each needs a *reversible embedding*: a
bijective function that contains the one you want.

## The three standard embeddings

**In-place update.** If an operation can be written as `y ^= f(x)` or
`y += f(x)`, it is already reversible — apply it twice, or subtract, and you are
back where you started. XOR and modular addition are both of this form, which is
why they are the cheap parts of SHA-256 on a quantum computer.

**Compute into a fresh register.** An irreversible `f` becomes reversible as
`(x, 0) -> (x, f(x))`. The input is kept, so nothing is lost. This is what the
Toffoli gate does for AND:

```text
(x, y, t) -> (x, y, t XOR (x AND y))
```

The price is a qubit to hold the answer, and Toffoli is the only non-Clifford
gate in the whole SHA-256 construction — so it drives the entire fault-tolerant
cost.

**Read through a relabelling.** Some operations are permutations of *bit
positions*, not of values. `ROTR^7` moves no data; it renames which wire carries
which bit. If the compiler controls that mapping, the operation is free. qSHA256
implements rotation and shift this way (`quantum/primitives/rotate.py`,
`shift.py`), which is why they never appear in the gate counts.

## Garbage, and why it is fatal

A reversible circuit that computes `f(x)` typically leaves intermediate values
behind:

```text
|x> |0> |0>   ->   |x> |f(x)> |junk>
```

For a one-shot computation `junk` is merely wasteful. Inside a Grover oracle it
is fatal. Grover works by interference between branches of a superposition, and
two branches only interfere if they are *identical in every register*. Leftover
junk that differs between branches keeps them distinguishable, the interference
never happens, and the algorithm degrades to random guessing.

The fix is **uncomputation**, due to Bennett:

```text
|x> |0> |0>
    --- compute --->    |x> |junk> |f(x)>
    --- copy out --->   |x> |junk> |f(x)>  (f(x) CNOTed to a clean register)
    --- uncompute --->  |x> |0>    |f(x)>
```

Run the computation, copy the answer out, then run the computation backwards.
The junk unwinds and the answer survives, because it lives in a register the
reverse pass never touches.

qSHA256 does exactly this in `quantum/sha256/compression.py`. Because the builder
only emits self-inverse permutation gates, the inverse circuit is the recorded
instruction span replayed backwards — no separate inverse implementation, and no
chance of the two drifting apart.

**Uncomputation is not free.** It roughly doubles the round cost, and the
measured numbers say so:

| Circuit | Qubits | Toffoli | T-count |
|---|---:|---:|---:|
| forward only | 1,057 | 46,592 | 326,144 |
| garbage-free | 1,313 | 92,672 | 648,704 |

## Where this bites in SHA-256

| Classical operation | Reversible form | Cost per 32-bit word |
|---|---|---|
| `x XOR y` | in-place, CNOT | 32 CNOT, 0 Toffoli |
| `ROTR^n(x)` | wire relabelling | free |
| `SHR^n(x)` | wire relabelling with constant-zero fill | free |
| `Ch(x,y,z)` | `z XOR (x AND (y XOR z))` | 32 Toffoli, 96 CNOT |
| `Maj(x,y,z)` | `x XOR ((x XOR y) AND (x XOR z))` | 32 Toffoli, 160 CNOT |
| `(a+b) mod 2^32` | CDKM ripple-carry | 64 Toffoli, 128 CNOT, 1 ancilla |

The Ch and Maj forms are algebraic rewrites chosen so a single AND does all the
non-linear work. A naive transcription of the textbook formulas would need 2 and
3 Toffolis per bit; these need 1. Both identities are verified exhaustively in
`tests/test_primitives.py`.

## The `SHR` subtlety

A logical right shift genuinely destroys information: four different inputs give
the same `SHR^2` output. So how can it be free?

Because qSHA256 never shifts a register — it only ever *reads a shifted view* of
one. `SHR` appears in SHA-256 only inside `sigma0` and `sigma1`, whose results
are XORed into a separate target. The source register is untouched, so the
"discarded" low bits are still sitting there. The view simply declines to read
them.

A shift is in fact *cheaper* than a rotation when XORed into a target: `SHR^10`
on a 32-bit word contributes 22 CNOTs rather than 32, because ten source
positions are the constant zero and emit nothing.

See `qsha256/quantum/primitives/shift.py`, whose
`in_place_shift_is_reversible()` returns `False` and explains why.
