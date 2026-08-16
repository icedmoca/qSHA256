"""The classical reference model, and why it exposes its intermediates.

    python examples/01_classical_reference.py

Every quantum circuit in this project is validated against this model, so the
model itself is validated against hashlib and the published test vectors first.
"""

import hashlib

from qsha256.classical.sha256 import compression_trace, message_schedule, pad_message, sha256_hex
from qsha256.spec import SHA256

message = b"abc"

print("1. It agrees with hashlib")
print(f"   qsha256:  {sha256_hex(message)}")
print(f"   hashlib:  {hashlib.sha256(message).hexdigest()}")
print(f"   match:    {sha256_hex(message) == hashlib.sha256(message).hexdigest()}")

print("\n2. Padding is explicit (message length is public, so this is classical)")
padded = pad_message(message)
print(f"   {len(message)} bytes -> {len(padded)} bytes, {len(padded) // 64} block(s)")
print(
    f"   0x80 marker at byte {padded.index(0x80)}, "
    f"length field = {int.from_bytes(padded[-8:], 'big')} bits"
)

print("\n3. Constants are DERIVED, not transcribed")
print("   K[t] = first 32 fractional bits of the cube root of the t-th prime")
for t in (0, 1, 63):
    print(f"     K[{t:2d}] = 0x{SHA256.k[t]:08x}")

print("\n4. Every intermediate is available -- this is what validates circuits")
block = [int.from_bytes(padded[i : i + 4], "big") for i in range(0, 64, 4)]
w = message_schedule(block, SHA256)
print(f"   W[ 0] = 0x{w[0]:08x}   (from the message)")
print(f"   W[16] = 0x{w[16]:08x}   (first expanded word)")
print(f"   W[63] = 0x{w[63]:08x}")

trace = compression_trace(tuple(SHA256.h0), block, SHA256)
r = trace.rounds[0]
print("\n   Round 0, every value the circuit must reproduce:")
print(f"     Sigma1(e) = 0x{r.big_sigma1_e:08x}")
print(f"     Ch(e,f,g) = 0x{r.ch_efg:08x}")
print(f"     T1        = 0x{r.t1:08x}")
print(f"     Sigma0(a) = 0x{r.big_sigma0_a:08x}")
print(f"     Maj(a,b,c)= 0x{r.maj_abc:08x}")
print(f"     T2        = 0x{r.t2:08x}")
print(f"\n   digest: {''.join(f'{x:08x}' for x in trace.state_out)}")
