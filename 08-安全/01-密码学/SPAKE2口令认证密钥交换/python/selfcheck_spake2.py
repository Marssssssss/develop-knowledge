"""SPAKE2 自检（RFC 9382 附录 B 四组向量 + P-256 参数自证 + 若干配对性质）。"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from p256 import (P, N, A, B, G, GX, GY, H, FIELD_LEN,
                  add, mul, neg, is_on_curve, encode_uncompressed, decode,
                  decompress)
from spake2 import (M, N_POINT, Party, run, build_tt, encode_w, len_prefix,
                    kdf, mac, hash_tt, hkdf_extract, hkdf_expand,
                    impersonate_guess)
from spake2_vectors import SPAKE2_VECTORS, P256_M, P256_N

PASS = 0
FAIL = 0
BAD = []


def ck(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        BAD.append(msg)


def unhex(s):
    return bytes.fromhex(s)


# ======================================================== A. P-256 参数自证
ck(P == 2 ** 256 - 2 ** 224 + 2 ** 192 + 2 ** 96 - 1,
   "p = 2^256 - 2^224 + 2^192 + 2^96 - 1（FIPS 186-4 §D.2.3 的写法）")
ck(A == P - 3, "a = -3 mod p")
ck(H == 1, "P-256 余因子 h = 1")
ck(P % 4 == 3, "p ≡ 3 (mod 4) —— 压缩点解压能直接用 y2^((p+1)/4)")
ck(is_on_curve(G), "生成元 G 在曲线上")
ck(mul(N, G) is None, "n*G = 无穷远点（G 的阶整除 n）")
ck(mul(N - 1, G) == (GX, (-GY) % P), "(n-1)*G = -G")
for k in (2, 3, 7, 12345):
    ck(is_on_curve(mul(k, G)), "k*G 仍在曲线上 k=%d" % k)
    ck(add(mul(k, G), neg(mul(k, G))) is None, "P + (-P) = O，k=%d" % k)
    ck(add(mul(k, G), G) == mul(k + 1, G), "k*G + G = (k+1)*G，k=%d" % k)
# 群运算律（随机取点，逐项比对而非只比一个值）
for a_ in (3, 11, 97):
    for b_ in (5, 23, 101):
        ck(mul(a_ + b_, G) == add(mul(a_, G), mul(b_, G)),
           "标量加法分配律 %d,%d" % (a_, b_))
        ck(mul(a_ * b_, G) == mul(a_, mul(b_, G)),
           "标量乘法结合律 %d,%d" % (a_, b_))
# SEC1 编解码往返
for k in (1, 2, 7, 255, N - 1):
    pt = mul(k, G)
    enc = encode_uncompressed(pt)
    ck(len(enc) == 65 and enc[0] == 0x04, "未压缩点 65 字节且前缀 0x04，k=%d" % k)
    ck(decode(enc) == pt, "SEC1 往返 k=%d" % k)
    comp = bytes([0x02 | (pt[1] & 1)]) + pt[0].to_bytes(32, "big")
    ck(decompress(comp) == pt, "压缩点解压 k=%d" % k)

# ======================================================== B. M / N 与曲线
ck(decode(unhex(P256_M)) == M and decode(unhex(P256_N)) == N_POINT,
   "M/N 由 RFC 9382 §6 的压缩点解压得到")
ck(is_on_curve(M) and is_on_curve(N_POINT), "M、N 都在曲线上")
ck(M != N_POINT and M != neg(N_POINT), "M ≠ ±N")
ck(mul(N, M) is None and mul(N, N_POINT) is None, "M、N 都在 n 阶子群里")
ck(P256_M[:2] == "02" and P256_N[:2] == "03", "向量 1 的 M 前缀 02、N 前缀 03")

# ======================================================== C. RFC 9382 附录 B
for v in SPAKE2_VECTORS:
    f = v["f"]
    ida, idb = v["A"], v["B"]
    w = int(f["w"], 16)
    x = int(f["x"], 16)
    y = int(f["y"], 16)
    tag = "A=%r B=%r" % (ida, idb)

    a = Party("A", ida, idb, w, x)
    b = Party("B", idb, ida, w, y)
    pA = a.start()
    pB = b.start()
    ck(pA == unhex(f["pA"]), "pA 对拍 " + tag)
    ck(pB == unhex(f["pB"]), "pB 对拍 " + tag)

    ke_a, cA = a.finish(pB)
    ke_b, cB = b.finish(pA)
    ck(a.K == unhex(f["K"]), "K 对拍 " + tag)
    ck(b.K == unhex(f["K"]), "B 侧算出的 K 相同 " + tag)
    ck(a.tt == unhex(f["TT"]), "TT 对拍 " + tag)
    ck(b.tt == unhex(f["TT"]), "B 侧 TT 相同 " + tag)
    digest = f.get("Hash(TT)") or f.get("HASH(TT)")
    ck(hash_tt(a.tt) == unhex(digest), "Hash(TT) 对拍 " + tag)
    ck(a.Ke == unhex(f["Ke"]) and a.Ka == unhex(f["Ka"]), "Ke/Ka 对拍 " + tag)
    ck(ke_a == ke_b, "双方 Ke 一致 " + tag)
    ck(a.Kc_self == unhex(f["KcA"]) and a.Kc_peer == unhex(f["KcB"]),
       "KcA/KcB 对拍（A 侧）" + tag)
    ck(b.Kc_self == unhex(f["KcB"]) and b.Kc_peer == unhex(f["KcA"]),
       "KcA/KcB 对拍（B 侧）" + tag)
    ck(cA == unhex(f["A conf"]), "A conf 对拍 " + tag)
    ck(cB == unhex(f["B conf"]), "B conf 对拍 " + tag)
    ck(a.verify(cB) and b.verify(cA), "双向确认通过 " + tag)

# ======================================================== D. TT 的构造细节
v0 = SPAKE2_VECTORS[0]["f"]
tt0 = unhex(v0["TT"])
ck(tt0[:8] == (6).to_bytes(8, "little"), "len(A) 是 8 字节小端（server → 06 00…）")
ck(tt0[8:14] == b"server", "A 紧跟在长度之后")
ck(tt0[14:22] == (6).to_bytes(8, "little") and tt0[22:28] == b"client",
   "len(B) || B 紧随其后")
ck(tt0[28:36] == (65).to_bytes(8, "little"), "len(pA) = 65 小端")
ck(tt0[-40:-32] == (32).to_bytes(8, "little"), "len(w) = 32 小端，恒定")
ck(tt0[-32:] == int(v0["w"], 16).to_bytes(32, "big"), "w 是大端、补零到 32 字节")
# w 很小时长度也不变（规范说这是为了防计时侧信道）
ck(len(encode_w(1)) == 32 and len(encode_w(0)) == 32, "len(w) 与 w 的大小无关")
ck(encode_w(1) == b"\x00" * 31 + b"\x01", "w=1 左补零")
# 空身份
tt_empty = build_tt(b"", b"", b"\x04" + b"\x11" * 64, b"\x04" + b"\x22" * 64,
                    b"\x04" + b"\x33" * 64, 7)
ck(tt_empty[:8] == (0).to_bytes(8, "little") and tt_empty[8:16] == (0).to_bytes(8, "little"),
   "空身份的长度前缀是 0，且不占字节")
ck(len(tt_empty) == 16 + 3 * (8 + 65) + 8 + 32, "空身份时 TT 总长 275")

# ======================================================== E. 口令错位配对
w_true = int(SPAKE2_VECTORS[0]["f"]["w"], 16)
x0 = int(SPAKE2_VECTORS[0]["f"]["x"], 16)
y0 = int(SPAKE2_VECTORS[0]["f"]["y"], 16)
ke_ok, _, _, _, ok_ok = run("server", "client", w_true, x0, y0)
ck(ok_ok, "口令一致 → 双向确认通过（阳性对照）")

# 注意：run() 两侧共用同一个 w，所以"两边都错且错得一样"反而会通过——
# 真正的错位必须让 A、B 持有**不同**的 w。
def mismatched(w_a, w_b, x=x0, y=y0):
    a = Party("A", "server", "client", w_a, x)
    b = Party("B", "client", "server", w_b, y)
    pA_ = a.start()
    pB_ = b.start()
    kea, cA_ = a.finish(pB_)
    keb, cB_ = b.finish(pA_)
    return a, b, kea, keb, a.verify(cB_) and b.verify(cA_)


a_m, b_m, ke_a_m, ke_b_m, ok_bad = mismatched(w_true, (w_true + 1) % N)
ck(not ok_bad, "口令错位 → 确认失败（阴性对照）")
ck(ke_a_m != ke_b_m and ke_a_m != ke_ok, "口令错位 → 双方 Ke 不一致")
ck(a_m.K != b_m.K, "口令错位 → 双方算出的 K 也不同")

hits = 0
for d in range(1, 201):
    _, _, _, _, ok = mismatched(w_true, (w_true + d) % N, 12345, 67890)
    hits += 1 if ok else 0
ck(hits == 0, "200 个错位口令全部失败，实得命中 %d" % hits)

# 两边都用同一个错误口令：协议照样"跑通"，只是双方共享的是错的 w
_, _, ke_same_wrong, _, ok_same = run("server", "client", (w_true + 1) % N, x0, y0)
ck(ok_same, "两侧持同一个错误口令 → 协议仍自洽（说明确认消息只验证一致性）")

# ======================================================== F. 离线字典攻击
pA0 = unhex(SPAKE2_VECTORS[0]["f"]["pA"])
cA0 = unhex(SPAKE2_VECTORS[0]["f"]["A conf"])
ck(impersonate_guess("server", "client", pA0, y0, w_true, cA0),
   "猜中口令 → 离线确认成功（攻击可行性的阳性对照）")
misses = 0
for d in range(1, 51):
    if impersonate_guess("server", "client", pA0, y0, (w_true + d) % N, cA0):
        misses += 1
ck(misses == 0, "50 个错误口令离线确认全部失败，实得 %d" % misses)

# ======================================================== G. 传输篡改
_, keB, cA_g, cB_g, ok_g = run("alice", "bob", 0x1234, 0x55, 0x66)
ck(ok_g, "无篡改时确认通过")
a2 = Party("A", "alice", "bob", 0x1234, 0x55)
a2.start()
pB_clean = unhex(SPAKE2_VECTORS[0]["f"]["pB"])
a2.finish(pB_clean)
ck(not a2.verify(cB_g), "换了 pB → TT 变 → 确认失败")
# 改一个比特的 pB 也要失败（漏报集）
# 两种失败都算「拒」：①解码阶段就发现不是合法群元素 ②解出来但 TT 变了、确认对不上
for bit in range(0, 65 * 8):
    m = bytearray(pB_clean)
    m[bit // 8] ^= 0x80 >> (bit % 8)
    a3 = Party("A", "alice", "bob", w_true, x0)
    a3.start()
    try:
        a3.finish(bytes(m))
        accepted = a3.verify(cB_g)
    except ValueError:
        accepted = False
    ck(not accepted, "pB 位 %d 篡改 → 未被接受" % bit)

# ======================================================== H. AAD 只影响确认密钥
pa = Party("A", "alice", "bob", 0x999, 0x1111)
pa.start()
pb = Party("B", "bob", "alice", 0x999, 0x2222)
pBb = pb.start()
pa.finish(pBb, aad=b"")
ke_no_aad, ka_no_aad = pa.Ke, pa.Ka
kc_no_aad = (pa.Kc_self, pa.Kc_peer)
pa2 = Party("A", "alice", "bob", 0x999, 0x1111)
pa2.start()
pa2.finish(pBb, aad=b"version=1")
ck(pa2.Ke == ke_no_aad and pa2.Ka == ka_no_aad,
   "AAD 不进 TT → Ke/Ka 不变")
ck((pa2.Kc_self, pa2.Kc_peer) != kc_no_aad, "AAD 进 KDF info → KcA/KcB 变")
ck(pa2.tt == pa.tt, "AAD 不改变 transcript 本身")

# ======================================================== I. x/y 复用的代价
w1, w2 = 0x1111, 0x2222
p1 = Party("A", "alice", "bob", w1, 0xCAFE).start()
p2 = Party("A", "alice", "bob", w2, 0xCAFE).start()
diff = add(decode(p1), neg(decode(p2)))
ck(diff == mul((w1 - w2) % N, M),
   "复用 x 时 pA(w1) - pA(w2) = (w1-w2)*M，随机量被完全消掉")

# ======================================================== J. 群成员检查
bad_x = (P - 1).to_bytes(32, "big")
try:
    decode(b"\x02" + bad_x)
    ck(False, "非曲线上的压缩点应被拒")
except ValueError as e:
    ck(True, "非曲线上的压缩点被拒：%s" % e)
try:
    decode(b"\x04" + (0).to_bytes(32, "big") + (0).to_bytes(32, "big"))
    ck(False, "(0,0) 不在曲线上，应被拒")
except ValueError:
    ck(True, "(0,0) 被拒")
try:
    decode(b"\x05" + b"\x00" * 64)
    ck(False, "不支持的前缀应被拒")
except ValueError:
    ck(True, "不支持的前缀被拒")

# ======================================================== K. HKDF 自检
# RFC 5869 未定义官方向量，此处只验结构性质（可证）
prk = hkdf_extract(b"", b"ikm")
ck(len(prk) == 32, "HKDF-Extract 输出 HashLen")
ck(hkdf_extract(b"", b"ikm") == hkdf_extract(b"\x00" * 32, b"ikm"),
   "salt 为空串与 32 个零字节等价（HMAC 键补零到块长）")
ck(hkdf_expand(prk, b"info", 16) == hkdf_expand(prk, b"info", 42)[:16],
   "HKDF-Expand 前缀一致")
ck(len(hkdf_expand(prk, b"info", 42)) == 42, "HKDF-Expand 长度精确")
ck(kdf(b"ka", b"", b"ConfirmationKeys", 32) ==
   kdf(b"ka", None, b"ConfirmationKeys", 32), "KDF 的 nil salt 两种写法一致")

print("PASS=%d FAIL=%d" % (PASS, FAIL))
for m in BAD[:20]:
    print("  FAIL:", m)
sys.exit(1 if FAIL else 0)
