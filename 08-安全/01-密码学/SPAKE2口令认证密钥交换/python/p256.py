"""P-256 曲线算术（参数取自 FIPS 186-4 附录 D.1.2.3 "Curve P-256"）。

FIPS 186-4 §D.1.2 原文：E: y^2 = x^3 - 3x + b (mod p)，素阶 n，余因子 h = 1。
p 与 n 在规范里是十进制，b / Gx / Gy 是十六进制；本文件按原文取值。

仿射坐标 + 模逆（pow(x, p-2, p)），不做任何常数时间优化——本 demo 要的是
"与 RFC 9382 附录 B 的数值逐字节吻合"，不是抗侧信道。
"""

P = 115792089210356248762697446949407573530086143415290314195533631308867097853951
N = 115792089210356248762697446949407573529996955224135760342422259061068512044369
A = P - 3                                                                   # a = -3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
H = 1                                                                       # 余因子
FIELD_LEN = 32                                                              # 域元素字节长

G = (GX, GY)
IDENTITY = None                                                             # 无穷远点


def is_on_curve(pt):
    if pt is None:
        return True
    x, y = pt
    return (y * y - (x * x * x + A * x + B)) % P == 0


def inv(x):
    return pow(x % P, P - 2, P)


def neg(pt):
    if pt is None:
        return None
    return (pt[0], (-pt[1]) % P)


def add(p1, p2):
    """完全点加（含倍点、互逆、无穷远点）。"""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        # 倍点
        lam = (3 * x1 * x1 + A) * inv(2 * y1) % P
    else:
        lam = (y2 - y1) * inv(x2 - x1) % P
    x3 = (lam * lam - x1 - x2) % P
    y3 = (lam * (x1 - x3) - y1) % P
    return (x3, y3)


def mul(k, pt):
    """标量乘，k 可为任意整数（负数取逆元后再乘）。"""
    if k < 0:
        return mul(-k, neg(pt))
    k %= N
    r = None
    base = pt
    while k:
        if k & 1:
            r = add(r, base)
        base = add(base, base)
        k >>= 1
    return r


# ------------------------------------------------------------- SEC1 编解码

def encode_uncompressed(pt):
    """SEC1 未压缩：0x04 || X(32) || Y(32)。"""
    if pt is None:
        raise ValueError("cannot encode the identity")
    return b"\x04" + pt[0].to_bytes(FIELD_LEN, "big") + pt[1].to_bytes(FIELD_LEN, "big")


def decode(b):
    """SEC1 解码，同时接受未压缩与压缩两种格式。"""
    if len(b) == 0:
        raise ValueError("empty encoding")
    prefix = b[0]
    if prefix == 0x04:
        if len(b) != 1 + 2 * FIELD_LEN:
            raise ValueError("bad uncompressed length")
        pt = (int.from_bytes(b[1:1 + FIELD_LEN], "big"),
              int.from_bytes(b[1 + FIELD_LEN:], "big"))
        if not is_on_curve(pt):
            raise ValueError("point not on curve")
        return pt
    if prefix in (0x02, 0x03):
        if len(b) != 1 + FIELD_LEN:
            raise ValueError("bad compressed length")
        return decompress(b)
    raise ValueError("unsupported prefix 0x%02x" % prefix)


def decompress(b):
    """SEC1 §2.3.4 压缩点解压：由前缀最低位决定 y 的奇偶。"""
    x = int.from_bytes(b[1:], "big")
    if x >= P:
        raise ValueError("x out of range")
    y2 = (x * x * x + A * x + B) % P
    # p ≡ 3 (mod 4)，故平方根可取 y2^((p+1)/4)
    y = pow(y2, (P + 1) // 4, P)
    if y * y % P != y2:
        raise ValueError("x is not the abscissa of any point")
    if (y & 1) != (b[0] & 1):
        y = P - y
    pt = (x, y)
    if not is_on_curve(pt):
        raise ValueError("decompressed point not on curve")
    return pt
