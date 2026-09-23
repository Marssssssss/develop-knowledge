"""RFC 9591 §3.1 要求的「素数阶群」的一个可复算实例。

为了让 demo 不依赖任何第三方库、又能真实体现 FROST 的代数结构，这里用
**Schnorr 群**（安全素数 p = 2q+1 的 q 阶子群）而不是 edwards25519：
群元素是模 p 的整数、标量是模 q 的整数，`ScalarMult` 就是模幂。
RFC 9591 全文只依赖 §3.1 列出的那几个抽象操作，所以协议逻辑逐行照抄即可。

p、q 由脚本确定性搜出并在自检里重新做素性验证，不是手抄的常数。
"""

import hashlib

# 256 位安全素数群：q 是素数，p = 2q+1 也是素数，g = 4 生成 q 阶子群
Q = 0x83f515064919a3dd423569f0ad5dd69fb204575536feac18d623193999d1c1d1
P = 0x107ea2a0c923347ba846ad3e15abbad3f6408aeaa6dfd5831ac46327333a383a3
G = 4

NS = 32  # 标量序列化长度（字节）
NE = 33  # 元素序列化长度（字节，p 是 257 位）
CONTEXT = b"FROST-DEMO-SCHNORR-v1"


# ----------------------------------------------------------- 群操作（§3.1）
def order():
    return Q


def identity():
    return 1


def scalar_base_mult(s):
    return pow(G, s % Q, P)


def scalar_mult(a, s):
    return pow(a, s % Q, P)


def element_add(a, b):
    return a * b % P


def scalar_add(a, b):
    return (a + b) % Q


def scalar_sub(a, b):
    return (a - b) % Q


def scalar_mul(a, b):
    return a * b % Q


def scalar_div(a, b):
    return a * pow(b, Q - 2, Q) % Q


def scalar_invert(a):
    return pow(a % Q, Q - 2, Q)


def serialize_element(a):
    return a.to_bytes(NE, "big")


def deserialize_element(buf):
    if len(buf) != NE:
        raise ValueError("bad element length")
    a = int.from_bytes(buf, "big")
    if not (1 <= a < P):
        raise ValueError("element out of range")
    if pow(a, Q, P) != 1:
        raise ValueError("not in prime-order subgroup")
    if a == 1:
        raise ValueError("identity element")
    return a


def serialize_scalar(s):
    return (s % Q).to_bytes(NS, "little")


def deserialize_scalar(buf):
    if len(buf) != NS:
        raise ValueError("bad scalar length")
    return int.from_bytes(buf, "little") % Q


# ------------------------------------------------- 域分离哈希 H1..H5（§6.1）
def _h(m):
    return hashlib.sha512(m).digest()


def _to_scalar(digest):
    return int.from_bytes(digest, "little") % Q


def h1(m):
    """rho：绑定因子。"""
    return _to_scalar(_h(CONTEXT + b"rho" + m))


def h2(m):
    """challenge：为了与 Schnorr 验签式兼容，RFC 9591 特意不给它加域分隔。"""
    return _to_scalar(_h(m))


def h3(m):
    """nonce：把随机性与长期私钥绑在一起，抵抗坏 RNG。"""
    return _to_scalar(_h(CONTEXT + b"nonce" + m))


def h4(m):
    """msg：把消息压成定长。"""
    return _h(CONTEXT + b"msg" + m)


def h5(m):
    """com：把承诺列表压成定长。"""
    return _h(CONTEXT + b"com" + m)
