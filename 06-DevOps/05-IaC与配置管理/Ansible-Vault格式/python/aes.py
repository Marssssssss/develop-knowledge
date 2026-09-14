"""Minimal AES-256 (FIPS-197) + CTR mode, pure standard library.

Why hand-roll it: Python's standard library ships no block cipher, and the
project rule for these demos is "no third-party dependencies". Go's stdlib has
`crypto/aes`, so the Go twin of this demo uses the standard library instead --
which makes the pair a useful contrast (scratch implementation vs. vetted one).

Refs read before writing this file:
  - FIPS-197, "Announcing the Advanced Encryption Standard (AES)"
    https://csrc.nist.gov/pubs/fips/197/final
  - Wikipedia, "Rijndael S-box" (the p/q generation algorithm used below)
    https://en.wikipedia.org/wiki/Rijndael_S-box
  - NIST SP 800-38A, "Recommendation for Block Cipher Modes of Operation"
    https://csrc.nist.gov/pubs/sp/800/38/a/final
"""

from __future__ import annotations

NB = 4          # AES block size in 32-bit words (128 bits)
NK = 8          # AES-256 key size in 32-bit words
NR = 14         # AES-256 number of rounds


def _rotl8(x: int, shift: int) -> int:
    return ((x << shift) | (x >> (8 - shift))) & 0xFF


def _build_sbox() -> list[int]:
    """Generate the S-box with the p/q recurrence instead of a hardcoded table.

    Hand-typing 256 constants is a classic source of silent bugs; deriving them
    keeps this file honest, and `_selftest()` pins the result to FIPS-197.
    """
    sbox = [0] * 256
    p = q = 1
    while True:
        # p *= 3 in GF(2^8) (the Rijndael generator is 3)
        p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        # q /= 3 in GF(2^8), i.e. q *= 0xF6
        q = (q ^ ((q << 1) & 0xFF)) & 0xFF
        q = (q ^ ((q << 2) & 0xFF)) & 0xFF
        q = (q ^ ((q << 4) & 0xFF)) & 0xFF
        if q & 0x80:
            q ^= 0x09
        xformed = q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3) ^ _rotl8(q, 4)
        sbox[p] = xformed ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63  # the affine transform of 0 is 0x63, not 0
    return sbox


SBOX = _build_sbox()


def _xtime(x: int) -> int:
    """Multiply by 2 in GF(2^8) modulo the Rijndael polynomial x^8+x^4+x^3+x+1."""
    x <<= 1
    if x & 0x100:
        x ^= 0x11B
    return x & 0xFF


def _mul(a: int, b: int) -> int:
    """Russian-peasant multiplication in GF(2^8)."""
    out = 0
    while b:
        if b & 1:
            out ^= a
        a = _xtime(a)
        b >>= 1
    return out & 0xFF


class AES256:
    """AES-256 block cipher (encryption direction only, which is all CTR needs)."""

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("AES-256 needs a 32-byte key")
        self._w = self._expand_key(key)

    @staticmethod
    def _expand_key(key: bytes) -> list[list[int]]:
        """FIPS-197 §5.2 key expansion for Nk=8 -> 4*(Nr+1) = 60 words."""
        words = [list(key[4 * i:4 * i + 4]) for i in range(NK)]
        rcon = 1
        for i in range(NK, NB * (NR + 1)):
            temp = list(words[i - 1])
            if i % NK == 0:
                temp = temp[1:] + temp[:1]                      # RotWord
                temp = [SBOX[b] for b in temp]                  # SubWord
                temp[0] ^= rcon                                 # XOR Rcon
                rcon = _xtime(rcon)
            elif i % NK == 4:
                temp = [SBOX[b] for b in temp]                  # extra SubWord
            words.append([words[i - NK][j] ^ temp[j] for j in range(4)])
        return words

    def encrypt_block(self, block: bytes) -> bytes:
        if len(block) != 16:
            raise ValueError("AES block must be 16 bytes")
        # State is column-major: state[r][c] = in[4*c + r]  (FIPS-197 §3.4)
        state = [[block[4 * c + r] for c in range(4)] for r in range(4)]
        self._add_round_key(state, 0)
        for rnd in range(1, NR):
            self._sub_bytes(state)
            self._shift_rows(state)
            self._mix_columns(state)
            self._add_round_key(state, rnd)
        self._sub_bytes(state)
        self._shift_rows(state)
        self._add_round_key(state, NR)
        return bytes(state[r][c] for c in range(4) for r in range(4))

    def _add_round_key(self, state: list[list[int]], rnd: int) -> None:
        for c in range(4):
            word = self._w[4 * rnd + c]
            for r in range(4):
                state[r][c] ^= word[r]

    @staticmethod
    def _sub_bytes(state: list[list[int]]) -> None:
        for r in range(4):
            for c in range(4):
                state[r][c] = SBOX[state[r][c]]

    @staticmethod
    def _shift_rows(state: list[list[int]]) -> None:
        for r in range(1, 4):
            state[r] = state[r][r:] + state[r][:r]

    @staticmethod
    def _mix_columns(state: list[list[int]]) -> None:
        for c in range(4):
            a = [state[r][c] for r in range(4)]
            state[0][c] = _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3]
            state[1][c] = a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3]
            state[2][c] = a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3)
            state[3][c] = _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2)


def ctr_keystream(cipher: AES256, iv: int, length: int) -> bytes:
    """SP 800-38A §6.5: counter block is a 128-bit big-endian integer that is
    incremented once per block. Ansible Vault seeds it from the 16-byte IV."""
    out = bytearray()
    counter = iv & ((1 << 128) - 1)
    while len(out) < length:
        out += cipher.encrypt_block(counter.to_bytes(16, "big"))
        counter = (counter + 1) & ((1 << 128) - 1)
    return bytes(out[:length])


def xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _selftest() -> None:
    """Pin the implementation to published test vectors."""
    assert SBOX[0x00] == 0x63 and SBOX[0x01] == 0x7C and SBOX[0x53] == 0xED, "bad S-box"

    # FIPS-197 Appendix C.3 -- AES-256 ECB example vector.
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f"
                        "101112131415161718191a1b1c1d1e1f")
    plain = bytes.fromhex("00112233445566778899aabbccddeeff")
    want = "8ea2b7ca516745bfeafc49904b496089"
    got = AES256(key).encrypt_block(plain).hex()
    assert got == want, f"AES-256 mismatch: {got} != {want}"

    # CTR is its own inverse: encrypting twice returns the plaintext.
    iv = int.from_bytes(b"\x00" * 16, "big")
    ct = xor_bytes(plain, ctr_keystream(AES256(key), iv, len(plain)))
    assert xor_bytes(ct, ctr_keystream(AES256(key), iv, len(ct))) == plain
    print("  [selftest] S-box ok, FIPS-197 C.3 AES-256 vector ok, CTR round-trip ok")


if __name__ == "__main__":
    _selftest()
    c = AES256(bytes(range(32)))
    print("  block[0:16] =", c.encrypt_block(b"0123456789abcdef").hex())
