# Beyond SHA-256: SHA-512 and SHA-3

Everything in this project is written against a parameterised spec rather than
against hard-coded 32-bit constants, which makes new members of the SHA-2 family
nearly free and makes the contrast with SHA-3 sharp.

## SHA-512

A new `ShaSpec` with 64-bit words, 80 rounds and its own rotation amounts. That
is the entire change: the quantum construction, the proof suite, the resource
analysis and the design search all apply unmodified. Constants derive from prime
roots exactly as SHA-256's do, and padding generalises to 128-byte blocks with a
128-bit length field.

Verified by executing the full 80-round circuit against `hashlib.sha512`.

| | Qubits | Toffoli | Gidney ANDs | T-count |
|---|---:|---:|---:|---:|
| SHA-256, CDKM | 1,057 | 46,592 | 0 | 326,144 |
| SHA-512, CDKM | 2,113 | 117,760 | 0 | 824,320 |
| SHA-512, Gidney | 2,175 | 20,480 | 47,880 | 334,880 |

Roughly 2.5x SHA-256's Toffoli count for 2x the word size and 1.25x the rounds,
which is what the arithmetic predicts.

## SHA-3 (Keccak-f[1600])

Worth having precisely because it is built differently, and the difference shows
up directly in the resource profile.

SHA-2 is an ARX design and its cost is dominated by modular **addition**, whose
carry chains are long and serial. Keccak has no arithmetic at all. Four of its
five round steps are linear:

| Step | What it does | Cost here |
|---|---|---|
| `theta` | XOR of column parities plus a rotation | CNOT only |
| `rho` | rotate each lane by a fixed offset | **free** (wiring) |
| `pi` | permute the lanes | **free** (wiring) |
| `chi` | `A[x] ^= (NOT A[x+1]) AND A[x+2]` | one AND per state bit |
| `iota` | XOR a round constant | X gates only |

So **every non-linear gate in the whole of SHA-3 comes from `chi`**: 1,600 per
round. Keccak trades a few expensive carry chains for many cheap independent
ANDs, which is why its Toffoli *depth* is far lower than SHA-2's even though its
Toffoli *count* is higher.

Verified against the classical reference at 1, 2, 4, 8 and 24 rounds, and the
classical reference against `hashlib.sha3_256` — including the `0x86` pad10*1
edge case, where the domain byte lands exactly on a block boundary and a naive
"append 0x80" is wrong.

Full permutation: **10,944 qubits, 153,600 Toffoli, 268,800 CNOT.**

### Two structural obstacles

`chi` is invertible but **not an involution**, and cannot be applied in place:
every output lane depends on lanes the same step is modifying. So the circuit
ping-pongs between two 1600-qubit registers and pays `chi^-1` — two ANDs per bit
against `chi`'s one — purely to clear the source.

`theta`'s ancilla **cannot be uncomputed by any local move**. After the fold,
column `x` has gained `D[x]` in all five of its lanes; five is odd, so the
column's parity changed by exactly `D[x]`. Recovering the original parities
needs `D`, which is what we are trying to erase, and the dependency closes a
5-cycle. Untangling it means inverting `theta` as a linear map on the whole
1600-bit state. This implementation therefore keeps 320 qubits of `theta`
scratch per round.

### The opposite tradeoff to prior work

Amy et al. synthesise `theta` in place as an invertible GF(2) map, which fits
their circuit in 3,200 qubits while costing ~33 million CNOTs.

| | Qubits | Toffoli | CNOT |
|---|---:|---:|---:|
| Amy et al. 2016 | 3,200 | 84,480 | 33,269,760 |
| qSHA256 | 10,944 | 153,600 | 268,800 |

3.4x their qubits and 1.8x their Toffolis, for **124x fewer CNOTs**. Neither
circuit dominates; they sit at opposite ends of the same tradeoff, and the
leaderboard records it that way rather than claiming a win.

## Bitcoin

`SHA256(SHA256(header))` is the most-quoted quantum target there is, and the
public numbers are almost all handwaved from `2^128`. Three structural savings a
naive estimate misses are implemented:

- **the midstate is free** — the 80-byte header pads to two blocks and only the
  second contains the nonce, so the first compression is done classically once
  and folded in as the initial chaining value;
- **the second hash's message is mostly constant** — the first digest plus
  padding, loaded with X gates rather than input qubits;
- **the predicate is a threshold, not an equality** — the AND tree spans the
  difficulty bits, not all 256.

Validated against the real genesis block: the reference reproduces
`000000000019d668...` exactly and reports its 43 leading zero bits.

Oracle at 64 rounds: **2,399 qubits, 90,784 Gidney ANDs, T-count 363,990.**

Nothing here breaks Bitcoin, and the numbers point the other way. Grover on this
oracle is deeply serial, parallelises only as `sqrt`, and the classical network
already computes on the order of `10^20` hashes per second. Computing the real
circuit cost makes that concrete instead of rhetorical.
