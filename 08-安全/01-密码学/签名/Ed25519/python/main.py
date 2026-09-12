"""Ed25519 签名/验签 — RFC 8032 (无第三方依赖,纯 stdlib)

参考:
  RFC 8032, "Edwards-Curve Digital Signature Algorithm (EdDSA)",
  https://www.rfc-editor.org/rfc/rfc8032
  §5.1.5 Key Generation、§5.1.6 Sign、§5.1.7 Verify

实现要点:
  - 域 F_p,p = 2^255 - 19
  - 扭曲爱德华兹曲线 -x^2 + y^2 = 1 + d*x^2*y^2 (a = -1)
  - 扩展坐标点加 (RFC 8032 §5.1.4 完整公式)
  - 标量乘(双加)
  - SHA-512 域分离
"""

import hashlib
from typing import Tuple


# ---------- F_p 上的算术(§5.1.1) ----------
# p = 2^255 - 19
P = (1 << 255) - 19
# 阶 L = 2^252 + 27742317777372353535851937790883648493
L = 2**252 + 27742317777372353535851937790883648493
# d = -121665/121666 mod p
D_CURVE = (-121665 * pow(121666, -1, P)) % P
# 基点 B = (Bx, By):By = 4/5 mod p(§5.1.1),Bx 由 y 恢复并取 x & 1 == 0 的版本
_BY = 4 * pow(5, -1, P) % P
_u = (_BY * _BY - 1) % P
_v = (D_CURVE * _BY * _BY + 1) % P
_x_sq = (_u * pow(_v, -1, P)) % P
_BX = pow(_x_sq, (P + 3) // 8, P)
# Tonelli-Shanks for p ≡ 5 mod 8:若 (sqrt)^2 != x_sq 则乘以 2^((p-1)/4) 修正
if (_BX * _BX - _x_sq) % P != 0:
    _BX = _BX * pow(2, (P - 1) // 4, P) % P
# canonical BASE x 的 sign bit = 0,所以取偶数版本
if _BX & 1 != 0:
    _BX = P - _BX


def _inv(x: int) -> int:
    """F_p 上的逆元:x^(p-2) mod p,Fermat 小定理。"""
    return pow(x, P - 2, P)


# ---------- 曲线点(扩展坐标,X,Y,Z,T 满足 x = X/Z, y = Y/Z, xy = T/Z) ----------

class Point:
    """扭曲爱德华兹曲线上的点(X,Y,Z,T),曲线 a = -1,曲线常数 = D_CURVE。"""

    __slots__ = ("X", "Y", "Z", "T")

    def __init__(self, x: int, y: int, z: int = 1, t: int = None):
        self.X = x % P
        self.Y = y % P
        self.Z = z % P
        if t is None:
            t = (x * y) % P
        self.T = t % P

    def __add__(self, other: "Point") -> "Point":
        # RFC 8032 §5.1.4 完整点加(complete 公式,无 exception case)
        A = self.X * other.X % P
        B = self.Y * other.Y % P
        C = D_CURVE * self.T * other.T % P
        DD = self.Z * other.Z % P
        E = ((self.X + self.Y) * (other.X + other.Y) - A - B) % P
        F = (DD - C) % P
        G = (DD + C) % P
        H = (B - (-1) * A) % P  # a = -1 ⇒ H = B + A
        return Point(E * F % P, G * H % P, F * G % P, E * H % P)

    def __rmul__(self, n: int) -> "Point":
        """标量乘:Montgomery ladder(常数时间,O(n) 点加)。

        不变量:r0 = [k]P,r1 = [k+1]P(其中 k 是已处理位的 prefix)。
        每读一位 b,把 prefix 左移 1 再加 b:k -> 2k+b。
          b = 0:new_r0 = [2k]P = r0+r0,new_r1 = [2k+1]P = r0+r1
          b = 1:new_r0 = [2k+1]P = r0+r1,new_r1 = [2k+2]P = r1+r1
        """
        if n < 0:
            return (-self) * (-n)
        r0, r1 = ZERO, self
        for i in range(n.bit_length() - 1, -1, -1):
            if (n >> i) & 1:
                r0, r1 = r0 + r1, r1 + r1
            else:
                r0, r1 = r0 + r0, r0 + r1
        return r0

    def __neg__(self):
        return Point(-self.X, self.Y, self.Z, -self.T)

    def encode(self) -> bytes:
        """§5.1.2 编码:32 字节,低位存 y 的低 255 bit,最高位存 x 的符号位。"""
        zi = _inv(self.Z)
        x = self.X * zi % P
        y = self.Y * zi % P
        b = bytearray(y.to_bytes(32, "little"))
        b[31] |= (x & 1) << 7
        return bytes(b)

    @classmethod
    def decode(cls, b: bytes) -> "Point":
        """§5.1.3 解码:32 字节 y 编码 + 最高位 x 符号位。"""
        if len(b) != 32:
            raise ValueError("Ed25519 point encoding must be 32 bytes")
        y = int.from_bytes(b, "little")
        sign = (y >> 255) & 1
        y &= (1 << 255) - 1
        u = (y * y - 1) % P
        v = (D_CURVE * y * y + 1) % P
        x_sq = (u * _inv(v)) % P
        x = pow(x_sq, (P + 3) // 8, P)
        if (x * x - x_sq) % P != 0:  # 不是平方根,修正
            x = x * pow(2, (P - 1) // 4, P) % P
        if (x * x - x_sq) % P != 0:
            raise ValueError("not on curve")
        if x & 1 != sign:
            x = P - x
        return cls(x, y)


ZERO = Point(0, 1)
BASE = Point(_BX, _BY)


# ---------- §5.1.5 Key Generation ----------

def _scalar_clamp(h_bytes: bytes) -> int:
    """§5.1.5:取 SHA-512 输出低 32 字节,清除 bit 0/1/2,置 bit 254,清 bit 255。"""
    a = int.from_bytes(h_bytes, "little")
    a &= ~7                       # clear bit 0,1,2
    a &= (1 << 255) - 1           # clear bit 255
    a |= 1 << 254                 # set bit 254
    return a


def generate_keypair(seed: bytes) -> Tuple[bytes, bytes]:
    """Ed25519 密钥对(seed 32B → sk 32B + pk 32B)。"""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = _scalar_clamp(h[:32])
    a %= L  # 标量必须 < L(RFC 8032 §5.1.5 step 3 注 1)
    A = a * BASE
    pk = A.encode()
    sk = seed + pk  # §5.1.5 step 2
    return sk, pk


def sign(sk: bytes, msg: bytes) -> bytes:
    """Ed25519 签名(PureEdDSA,无 context,无 prehash)。"""
    seed, pk = sk[:32], sk[32:]
    h = hashlib.sha512(seed).digest()
    a = _scalar_clamp(h[:32])
    prefix = h[32:]
    # r = SHA-512(prefix || M)  mod L
    r_int = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % L
    R = r_int * BASE
    # s = r + SHA-512(R || A || M)·a  mod L
    s = (r_int + int.from_bytes(hashlib.sha512(R.encode() + pk + msg).digest(), "little") * a) % L
    return R.encode() + s.to_bytes(32, "little")


def verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    """Ed25519 验签,失败返回 False(不抛错)。"""
    if len(pk) != 32 or len(sig) != 64:
        return False
    try:
        A = Point.decode(pk)
        R = Point.decode(sig[:32])
    except ValueError:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= L:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + pk + msg).digest(), "little")
    lhs = s * BASE
    rhs = R + (h * A)
    return (lhs.X * rhs.Z - rhs.X * lhs.Z) % P == 0 and (lhs.Y * rhs.Z - rhs.Y * lhs.Z) % P == 0


# ---------- DEMO ----------

def main() -> None:
    print("[1] RFC 8032 §7.1 Test Vector 1")
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc4"
                          "4449c5697b326919703bac031cae7f60")
    expected_pk = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    expected_sig = ("e5564300c360ac729086e2cc806e828a"
                    "84877f1eb8e5d974d873e06522490155"
                    "5fb8821590a33bacc61e39701cf9b46b"
                    "d25bf5f0595bbe24655141438e7a100b")
    sk, pk = generate_keypair(seed)
    assert pk.hex() == expected_pk, f"pk 失配:\n  got  = {pk.hex()}\n  want = {expected_pk}"
    print("  ✓ 公钥与 RFC §7.1 Test 1 一致")

    msg = b""
    sig = sign(sk, msg)
    assert sig.hex() == expected_sig, f"sig 失配:\n  got  = {sig.hex()}\n  want = {expected_sig}"
    print("  ✓ 签名与 RFC §7.1 Test 1 一致")

    assert verify(pk, msg, sig)
    print("  ✓ 自验签通过")

    assert not verify(pk, b"x", sig)
    print("  ✓ 篡改消息被检测")
    bad = bytearray(sig); bad[0] ^= 1
    assert not verify(pk, msg, bytes(bad))
    print("  ✓ 篡改签名被检测")

    # §7.1 Test 2
    print("\n[2] RFC 8032 §7.1 Test Vector 2")
    seed2 = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
    expected_pk2 = "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c"
    sk2, pk2 = generate_keypair(seed2)
    assert pk2.hex() == expected_pk2
    print("  ✓ 公钥与 §7.1 Test 2 一致")

    print("\n全部 Ed25519 测试通过 ✓")


if __name__ == "__main__":
    main()