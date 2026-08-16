"""Public SHA-256 test vectors.

The first three are the classic FIPS 180-2 / NIST examples; the remainder
exercise the padding boundaries, which is where hand-written SHA-256
implementations most often go wrong (a message of exactly 55 or 56 bytes is the
classic off-by-one).  All were checked against :func:`hashlib.sha256`.
"""

from __future__ import annotations

__all__ = ["NIST_VECTORS", "PADDING_BOUNDARY_LENGTHS"]

#: ``(message, expected_hex_digest)``
NIST_VECTORS: list[tuple[bytes, str]] = [
    (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    (
        b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
    ),
    (
        b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmn"
        b"hijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu",
        "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1",
    ),
    (b"a" * 55, "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"),
    (b"a" * 56, "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"),
    (b"a" * 64, "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"),
]

#: Message lengths that straddle a padding block boundary.
PADDING_BOUNDARY_LENGTHS = [0, 1, 54, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128]
