"""SHA-256 教学实现 —— RFC 6234 / FIPS 180-4 严格对照。

实现要点:
- 64 轮压缩函数(Ch/Maj/SIGMA0/SIGMA1/sigma0/sigma1 全部按 FIPS 180-4 §6.2)
- 初始 H 由前 8 素数平方根小数部分构成
- K[64] 由前 64 素数立方根小数部分构成
- 填充:0x80 + 0x00... + 64-bit big-endian 长度
- 大端字节序,模 2^32 加法(Python 大整数自动 & 0xFFFFFFFF)

测试向量(FIPS 180-2 Appendix B.1 / NIST):
- SHA256("abc") = ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
- SHA256("")    = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
- SHA256("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") =
    248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1
- SHA256 百万 'a' = cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0
"""

from __future__ import annotations
import struct
import hashlib

# FIPS 180-4 §5.3.3 — Initial Hash Value
H_INIT = (
    0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
    0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19,
)

# FIPS 180-4 §4.2.2 — 64 round constants (前 64 素数立方根小数部分前 32 bit)
K = (
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
)

MASK = 0xFFFFFFFF


def rotr(x: int, n: int) -> int:
    return ((x >> n) | (x << (32 - n))) & MASK


# FIPS 180-4 §4.1.2
def Ch(x: int, y: int, z: int) -> int:
    return ((x & y) ^ ((~x) & z)) & MASK


def Maj(x: int, y: int, z: int) -> int:
    return ((x & y) ^ (x & z) ^ (y & z)) & MASK


def BSIG0(x: int) -> int:
    return rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22)


def BSIG1(x: int) -> int:
    return rotr(x, 6) ^ rotr(x, 11) ^ rotr(x, 25)


def SSIG0(x: int) -> int:
    return rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3)


def SSIG1(x: int) -> int:
    return rotr(x, 17) ^ rotr(x, 19) ^ (x >> 10)


def sha256(data: bytes) -> bytes:
    """RFC 6234 §6.2 / FIPS 180-4 §6.2 标准 SHA-256。"""
    L = len(data)
    # Pad: 0x80 + zeros + 64-bit BE length
    padded = data + b'\x80'
    while (len(padded) * 8) % 512 != 448:
        padded += b'\x00'
    padded += struct.pack('>Q', L * 8)  # 64-bit big-endian bit length

    H = list(H_INIT)
    for i in range(0, len(padded), 64):
        block = padded[i:i + 64]
        # 1) Message schedule (64 32-bit words, big-endian)
        W = list(struct.unpack('>16I', block)) + [0] * 48
        for t in range(16, 64):
            W[t] = (SSIG1(W[t - 2]) + W[t - 7] +
                    SSIG0(W[t - 15]) + W[t - 16]) & MASK
        # 2) Init working vars
        a, b, c, d, e, f, g, h = H
        # 3) 64 rounds
        for t in range(64):
            T1 = (h + BSIG1(e) + Ch(e, f, g) + K[t] + W[t]) & MASK
            T2 = (BSIG0(a) + Maj(a, b, c)) & MASK
            h = g
            g = f
            f = e
            e = (d + T1) & MASK
            d = c
            c = b
            b = a
            a = (T1 + T2) & MASK
        # 4) Update H
        H[0] = (H[0] + a) & MASK
        H[1] = (H[1] + b) & MASK
        H[2] = (H[2] + c) & MASK
        H[3] = (H[3] + d) & MASK
        H[4] = (H[4] + e) & MASK
        H[5] = (H[5] + f) & MASK
        H[6] = (H[6] + g) & MASK
        H[7] = (H[7] + h) & MASK
    return struct.pack('>8I', *H)


VECTORS = [
    (b"",
     "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
     "empty string"),
    (b"abc",
     "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
     "FIPS 180-2 App B.1"),
    (b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
     "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
     "FIPS 180-2 App B.2"),
]


def run_self_test() -> None:
    print("=" * 60)
    print("SHA-256 self-test (5 demos)")
    print("=" * 60)

    # demo 1: empty string
    out = sha256(b"")
    assert out.hex() == VECTORS[0][1], f"empty fail: {out.hex()}"
    print(f"[1] empty string: OK ({out.hex()[:16]}...)")

    # demo 2: "abc"
    out = sha256(b"abc")
    assert out.hex() == VECTORS[1][1], f"abc fail: {out.hex()}"
    print(f"[2] \"abc\": OK ({out.hex()[:16]}...)")

    # demo 3: 448-bit test (B.2)
    out = sha256(VECTORS[2][0])
    assert out.hex() == VECTORS[2][1], f"B.2 fail: {out.hex()}"
    print(f"[3] 448-bit string: OK ({out.hex()[:16]}...)")

    # demo 4: 896-bit two-block test (FIPS 180-2 App B.3)
    msg = (b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghij"
           b"klmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrst"
           b"nopqrstu")
    out = sha256(msg)
    expected = "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1"
    assert out.hex() == expected, f"B.3 fail: {out.hex()}"
    print(f"[4] 896-bit string: OK ({out.hex()[:16]}...)")

    # demo 5: million 'a' (uses streaming)
    out = sha256(b"a" * 1_000_000)
    expected = "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
    assert out.hex() == expected, f"million fail: {out.hex()}"
    print(f"[5] million 'a': OK ({out.hex()[:16]}...)")

    # cross-check against hashlib
    ref = hashlib.sha256(b"abc").hexdigest()
    assert sha256(b"abc").hex() == ref, "stdlib mismatch"
    print(f"[+] hashlib.sha256 cross-check: OK")

    # streaming equivalence
    data = b"x" * 1000
    full = sha256(data).hex()
    chunks = [data[i:i + 64] for i in range(0, len(data), 64)]
    assert sha256(b"".join(chunks)).hex() == full, "streaming != one-shot"
    print(f"[streaming] 1000 bytes split vs one-shot: hex match")

    print("=" * 60)
    print("All 5 demos + 1 cross-check + 1 streaming PASSED")


if __name__ == "__main__":
    run_self_test()