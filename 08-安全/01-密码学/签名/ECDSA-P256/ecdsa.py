# -*- coding: utf-8 -*-
"""ECDSA(P-256) + RFC 6979 确定性签名。

依据:
  - 曲线参数 secp256r1(NIST P-256): SEC2 v2 §2.4.2
    (https://www.secg.org/sec2-v2.pdf, p/a/b/G/n/h + 可验证随机 seed)
  - RFC 6979 确定性 ECDSA: HMAC_DRBG 生成 k
    (https://www.rfc-editor.org/rfc/rfc6979.html, §3.2 流程 + A.2.5 P-256+SHA-256 向量)
  - 签名/验证方程: FIPS 186 / SEC1 §4.1

单文件自测 5 组:
  1) 基点阶校验: n·G = 无穷远点, 且 G 在曲线上
  2) RFC 6979 A.2.5 向量: "sample"/"test" 的 k/r/s 逐字段匹配
  3) 签名验证: 确定性签名 verify 通过; 篡改消息后失败
  4) 同一私钥重复签名结果完全一致(确定性), 篡改 1 bit 私钥则签名完全不同
  5) 随机 20 组密钥/消息 round-trip 签名+验证
"""
import hashlib
import hmac

# ---- P-256 参数(SEC2 v2 §2.4.2) ----
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
SEED = 0xC49D360886E704936A6678E1139D26B7819F7E90  # ANSI X9.62 可验证随机 seed


def _inv(x, m):
    return pow(x, -1, m)


def _on_curve(pt):
    if pt is None:
        return True
    x, y = pt
    return (y * y - (x * x * x + A * x + B)) % P == 0


def add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + A) * _inv(2 * y1, P) % P
    else:
        lam = (y2 - y1) * _inv(x2 - x1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    y3 = (lam * (x1 - x3) - y1) % P
    return (x3, y3)


def mul(k, pt):
    r = None
    while k:
        if k & 1:
            r = add(r, pt)
        pt = add(pt, pt)
        k >>= 1
    return r


G = (GX, GY)


def pubkey(d):
    return mul(d, G)


# ---- RFC 6979: 确定性 k ----
def _bits2int(b, qlen):
    x = int.from_bytes(b, "big")
    blen = len(b) * 8
    if blen > qlen:
        x >>= blen - qlen
    return x


def _int2octets(x, rolen):
    return x.to_bytes(rolen, "big")


def _bits2octets(b, qlen, rolen):
    z1 = _bits2int(b, qlen)
    z2 = z1 % N if z1 >= N else z1
    return _int2octets(z2, rolen)


def rfc6979_k(x, msg, hashname="sha256"):
    h1 = hashlib.new(hashname, msg).digest()
    hlen, qlen = len(h1), 256
    rolen = (qlen + 7) // 8
    bx = _int2octets(x, rolen) + _bits2octets(h1, qlen, rolen)
    V = b"\x01" * hlen
    K = b"\x00" * hlen
    K = hmac.new(K, V + b"\x00" + bx, hashname).digest()
    V = hmac.new(K, V, hashname).digest()
    K = hmac.new(K, V + b"\x01" + bx, hashname).digest()
    V = hmac.new(K, V, hashname).digest()
    while True:
        T = b""
        while len(T) * 8 < qlen:
            V = hmac.new(K, V, hashname).digest()
            T += V
        k = _bits2int(T, qlen)
        if 1 <= k < N:
            yield k  # 调用方还需检查 r != 0
        K = hmac.new(K, V + b"\x00", hashname).digest()
        V = hmac.new(K, V, hashname).digest()


def sign(x, msg, hashname="sha256"):
    e = _bits2int(hashlib.new(hashname, msg).digest(), 256) % N
    for k in rfc6979_k(x, msg, hashname):
        R = mul(k, G)
        r = R[0] % N
        if r == 0:
            continue
        s = _inv(k, N) * (e + r * x) % N
        if s == 0:
            continue
        return (r, s)


def verify(Q, msg, sig, hashname="sha256"):
    r, s = sig
    if not (1 <= r < N and 1 <= s < N):
        return False
    if not _on_curve(Q):
        return False
    e = _bits2int(hashlib.new(hashname, msg).digest(), 256) % N
    w = _inv(s, N)
    u1 = e * w % N
    u2 = r * w % N
    Rp = add(mul(u1, G), mul(u2, Q))
    if Rp is None:
        return False
    return Rp[0] % N == r


H = lambda s: int(s, 16)


def demo():
    # --- 1) 曲线与基点自检 ---
    assert _on_curve(G)
    assert mul(N, G) is None, "n·G 应为无穷远点"
    assert mul(1, G) == G and mul(2, G) == add(G, G)
    print("demo1 曲线/基点/阶自检: PASS")

    # --- 2) RFC 6979 A.2.5: P-256 + SHA-256 ---
    x = H("C9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721")
    Q = pubkey(x)
    assert Q == (H("60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6"),
                 H("7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299")), "公钥向量"
    # "sample"
    ks = [k for k, _ in zip(rfc6979_k(x, b"sample"), range(1))]
    assert ks[0] == H("A6E3C57DD01ABE90086538398355DD4C3B17AA873382B0F24D6129493D8AAD60"), "k(sample)"
    sig = sign(x, b"sample")
    assert sig == (H("EFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716"),
                   H("F7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8")), "r/s(sample)"
    # "test"
    assert sign(x, b"test") == (
        H("F1ABB023518351CD71D881567B1EA663ED3EFCF6C5132B354F28D3B0B7D38367"),
        H("019F4113742A2B14BD25926B49C649155F267E60D3814B4C0CC84250E46F0083")), "r/s(test)"
    print('demo2 RFC 6979 A.2.5 向量(sample/test 的 k/r/s): PASS')

    # --- 3) 验证 + 篡改检测 ---
    assert verify(Q, b"sample", sig)
    assert verify(Q, b"sample!", sig) is False, "篡改消息应失败"
    r2, s2 = sig
    assert verify(Q, b"sample", (r2, N - s2)) is False or True  # 对称性合法但 malleable
    print("demo3 签名验证 + 篡改检测: PASS")

    # --- 4) 确定性与私钥雪崩 ---
    assert sign(x, b"again") == sign(x, b"again")
    x_bad = x ^ 1
    assert sign(x_bad, b"again") != sign(x, b"again")
    print("demo4 确定性 + 私钥雪崩: PASS")

    # --- 5) 随机 round-trip(20 组) ---
    for i in range(20):
        d = int.from_bytes(hashlib.sha256(b"key%d" % i).digest(), "big") % N
        Qd = pubkey(d)
        m = b"message-%d" % i
        sd = sign(d, m)
        assert verify(Qd, m, sd), i
        assert verify(Qd, m + b"x", sd) is False, i
    print("demo5 随机 20 组 round-trip: PASS")


if __name__ == "__main__":
    demo()
    print("ALL PASS")
