"""Public SHA-256 test vectors.

The first three are the classic FIPS 180-2 / NIST examples; the remainder
exercise the padding boundaries, which is where hand-written SHA-256
implementations most often go wrong (a message of exactly 55 or 56 bytes is the
classic off-by-one).  All were checked against :func:`hashlib.sha256`.
"""

from __future__ import annotations

__all__ = [
    "NIST_CAVP_SHA3_256",
    "NIST_CAVP_SHA256",
    "NIST_CAVP_SHA512",
    "NIST_VECTORS",
    "PADDING_BOUNDARY_LENGTHS",
]

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

#: Selected byte-oriented vectors from the NIST CAVP SHA Test Vectors
#: (SHA256ShortMsg.rsp / SHA256LongMsg.rsp), given as (message_hex, digest_hex).
#:
#: These matter because they are *published, third-party* expected outputs. A
#: comparison against hashlib shows two implementations agree; a comparison
#: against CAVP shows agreement with the standard's own test data, which is a
#: different and stronger statement. Every entry below is additionally checked
#: against hashlib by the test suite, so a transcription error cannot pass
#: silently.
NIST_CAVP_SHA256: list[tuple[str, str]] = [
    ("", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("d3", "28969cdfa74a12c82f3bad960b0b000aca2ac329deea5c2328ebc6f2ba9802c1"),
    ("11af", "5ca7133fa735326081558ac312c620eeca9970d1e70a4b95533d956f072d1f98"),
    ("b4190e", "dff2e73091f6c05e528896c4c831b9448653dc2ff043528f6769437bc7b975c2"),
    ("74ba2521", "b16aa56be3880d18cd41e68384cf1ec8c17680c45a02b1575dc1518923ae8b0e"),
    (
        "c299209682",
        "f0887fe961c9cd3beab957e8222494abb969b1ce4c6557976df8b0f6d20e9166",
    ),
    (
        "e1dc724d5621",
        "eca0a060b489636225b4fa64d267dabbe44273067ac679f20820bddc6b6a90ac",
    ),
    (
        "06e076f5a442d5",
        "3fd877e27450e6bbd5d74bb82f9870c64c66e109418baa8e6bbcff355e287926",
    ),
    (
        "5738c929c4f4ccb6",
        "963bb88f27f512777aab6c8b1a02c70ec0ad651d428f870036e1917120fb48bf",
    ),
    (
        "3334c58075d3f4139e",
        "078da3d77ed43bd3037a433fd0341855023793f9afd08b4b08ea1e5597ceef20",
    ),
]

#: NIST CAVP SHA-512 vectors, same rationale.
NIST_CAVP_SHA512: list[tuple[str, str]] = [
    (
        "",
        "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
        "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e",
    ),
    (
        "21",
        "3831a6a6155e509dee59a7f451eb35324d8f8f2df6e3708894740f98fdee2388"
        "9f4de5adb0c5010dfb555cda77c8ab5dc902094c52de3278f35a75ebc25f093a",
    ),
    (
        "9083",
        "55586ebba48768aeb323655ab6f4298fc9f670964fc2e5f2731e34dfa4b0c09e"
        "6e1e12e3d7286b3145c61c2047fb1a2a1297f36da64160b31fa4c8c2cddd2fb4",
    ),
    (
        "0a55db",
        "7952585e5330cb247d72bae696fc8a6b0f7d0804577e347d99bc1b11e52f3849"
        "85a428449382306a89261ae143c2f3fb613804ab20b42dc097e5bf4a96ef919b",
    ),
]

#: NIST CAVP SHA3-256 vectors.
NIST_CAVP_SHA3_256: list[tuple[str, str]] = [
    ("", "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a"),
    ("e9", "f0d04dd1e6cfc29a4460d521796852f25d9ef8d28b44ee91ff5b759d72c1e6d6"),
    ("d477", "94279e8f5ccdf6e17f292b59698ab4e614dfe696a46c46da78305fc6a3146ab7"),
    (
        "b053fa",
        "9d0ff086cd0ec06a682c51c094dc73abdc492004292344bd41b82a60498ccfdb",
    ),
    (
        "e7372105",
        "3a42b68ab079f28c4ca3c752296f279006c4fe78b1eb79d989777f051e4046ae",
    ),
]
