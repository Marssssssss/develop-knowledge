# AES-128 — FIPS 197 (无第三方依赖,纯 Python stdlib 实现)
#
# 参考:
#   FIPS 197 (Advanced Encryption Standard), 2001
#   https://csrc.nist.gov/pubs/fips/197/final
#
# 只实现 AES-128 (Nk=4, Nr=10),足以满足 AES-GCM demo 的需求。
# 加密与解密都按 FIPS 197 §5.1 / §5.3 的伪代码逐轮实现。

# FIPS 197 §5.1.1 Fig.7 的 S-box
_SBOX = (
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
)

# FIPS 197 §5.3.3 的 GF(2^8) inverse,用于 InvSubBytes
_INV_SBOX = [0] * 256
for _i in range(256):
    _INV_SBOX[_SBOX[_i]] = _i

# FIPS 197 §5.2 的轮常数
_RCON = (0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36)


def _xtime(b):
    """GF(2^8) 上的 ×2 运算(FIPS 197 §4.2.1)。"""
    return ((b << 1) ^ 0x1b) & 0xff if b & 0x80 else (b << 1) & 0xff


def _gmul(a, b):
    """GF(2^8) 上任意两元素相乘(FIPS 197 隐含使用)。"""
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


class Aes128:
    """AES-128 单块加/解密器(FIPS 197)。

    用法:
        cipher = Aes128(key_bytes_16)
        cipher.encrypt_block(plaintext_16) -> ciphertext_16
    """

    Nb, Nk, Nr = 4, 4, 10  # AES-128 固定参数

    def __init__(self, key: bytes):
        assert len(key) == 16, f"AES-128 需要 16 字节密钥,got {len(key)}"
        self._round_keys = self._key_expansion(key)

    def _key_expansion(self, key: bytes) -> list:
        """FIPS 197 §5.1.1 / §5.2 的密钥扩展,生成 11 组轮密钥(每组 16 字节)。"""
        w = [0] * (self.Nb * (self.Nr + 1))
        for i in range(self.Nk):
            w[i] = int.from_bytes(key[4 * i:4 * i + 4], "big")
        for i in range(self.Nk, self.Nb * (self.Nr + 1)):
            temp = w[i - 1]
            if i % self.Nk == 0:
                # RotWord + SubWord + Rcon
                temp = ((temp << 8) | (temp >> 24)) & 0xffffffff
                temp = ((_SBOX[(temp >> 24) & 0xff] << 24)
                        | (_SBOX[(temp >> 16) & 0xff] << 16)
                        | (_SBOX[(temp >> 8) & 0xff] << 8)
                        | _SBOX[temp & 0xff])
                temp ^= _RCON[i // self.Nk] << 24
            w[i] = w[i - self.Nk] ^ temp
        # 合并成 16 字节块列表
        rk = []
        for r in range(self.Nr + 1):
            rk.append(b"".join(w[r * 4 + c].to_bytes(4, "big") for c in range(4)))
        return rk

    def _sub_bytes(self, state, box):
        return bytes(box[b] for b in state)

    def _shift_rows(self, s):
        # Row 0 不动;Row 1 左移 1;Row 2 左移 2;Row 3 左移 3
        return bytes([s[0], s[5], s[10], s[15],
                      s[4], s[9], s[14], s[3],
                      s[8], s[13], s[2], s[7],
                      s[12], s[1], s[6], s[11]])

    def _inv_shift_rows(self, s):
        return bytes([s[0], s[13], s[10], s[7],
                      s[4], s[1], s[14], s[11],
                      s[8], s[5], s[2], s[15],
                      s[12], s[9], s[6], s[3]])

    def _mix_columns(self, s):
        out = bytearray(16)
        for c in range(4):
            i = 4 * c
            a = s[i:i + 4]
            out[i] = _xtime(a[0]) ^ (a[1] ^ _xtime(a[1])) ^ a[2] ^ a[3]
            out[i + 1] = a[0] ^ _xtime(a[1]) ^ (a[2] ^ _xtime(a[2])) ^ a[3]
            out[i + 2] = a[0] ^ a[1] ^ _xtime(a[2]) ^ (a[3] ^ _xtime(a[3]))
            out[i + 3] = (a[0] ^ _xtime(a[0])) ^ a[1] ^ a[2] ^ _xtime(a[3])
        return bytes(out)

    def _inv_mix_columns(self, s):
        # FIPS 197 §5.3.3 InvMixColumns 矩阵乘法 {0e,0b,0d,09}
        out = bytearray(16)
        for c in range(4):
            i = 4 * c
            a = s[i:i + 4]
            out[i]     = (_gmul(0x0e, a[0]) ^ _gmul(0x0b, a[1])
                          ^ _gmul(0x0d, a[2]) ^ _gmul(0x09, a[3]))
            out[i + 1] = (_gmul(0x09, a[0]) ^ _gmul(0x0e, a[1])
                          ^ _gmul(0x0b, a[2]) ^ _gmul(0x0d, a[3]))
            out[i + 2] = (_gmul(0x0d, a[0]) ^ _gmul(0x09, a[1])
                          ^ _gmul(0x0e, a[2]) ^ _gmul(0x0b, a[3]))
            out[i + 3] = (_gmul(0x0b, a[0]) ^ _gmul(0x0d, a[1])
                          ^ _gmul(0x09, a[2]) ^ _gmul(0x0e, a[3]))
        return bytes(out)

    def encrypt_block(self, block: bytes) -> bytes:
        """FIPS 197 §5.1 Cipher() 伪代码,16 字节输入 → 16 字节密文。"""
        assert len(block) == 16
        state = bytes(a ^ b for a, b in zip(block, self._round_keys[0]))
        for r in range(1, self.Nr):
            state = self._sub_bytes(state, _SBOX)
            state = self._shift_rows(state)
            state = self._mix_columns(state)
            state = bytes(a ^ b for a, b in zip(state, self._round_keys[r]))
        # 最后一轮不做 MixColumns
        state = self._sub_bytes(state, _SBOX)
        state = self._shift_rows(state)
        return bytes(a ^ b for a, b in zip(state, self._round_keys[self.Nr]))

    def decrypt_block(self, block: bytes) -> bytes:
        """FIPS 197 §5.3 InvCipher() 伪代码。"""
        assert len(block) == 16
        state = bytes(a ^ b for a, b in zip(block, self._round_keys[self.Nr]))
        for r in range(self.Nr - 1, 0, -1):
            state = self._inv_shift_rows(state)
            state = self._sub_bytes(state, _INV_SBOX)
            state = bytes(a ^ b for a, b in zip(state, self._round_keys[r]))
            state = self._inv_mix_columns(state)
        state = self._inv_shift_rows(state)
        state = self._sub_bytes(state, _INV_SBOX)
        return bytes(a ^ b for a, b in zip(state, self._round_keys[0]))