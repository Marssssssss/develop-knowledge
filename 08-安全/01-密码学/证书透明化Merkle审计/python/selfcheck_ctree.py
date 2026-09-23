"""证书透明化自检：RFC 6962 §2.1 的 7 叶示例 + 全尺寸穷举 + SCT/STH 编码往返。"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ct_merkle as M
import ct_struct as S

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


def raises(fn, msg):
    try:
        fn()
    except Exception:
        ck(True, msg)
        return
    ck(False, msg + " —— 期望抛错但没有")


def data(n):
    return [("cert-%d" % i).encode() for i in range(n)]


# ================================================= A. 哈希前缀与空树
ck(M.LEAF_PREFIX == b"\x00" and M.NODE_PREFIX == b"\x01", "叶子前缀 0x00、节点前缀 0x01")
ck(M.empty_root() == hashlib.sha256(b"").digest(),
   "空树的根 = SHA-256(\"\")（不是全零）")
ck(M.empty_root().hex() == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
   "空树根的官方十六进制值")
ck(M.leaf_hash(b"x") == hashlib.sha256(b"\x00x").digest(), "叶子哈希带 0x00 前缀")
ck(M.node_hash(b"a" * 32, b"b" * 32) == hashlib.sha256(b"\x01" + b"a" * 32 + b"b" * 32).digest(),
   "节点哈希带 0x01 前缀")
ck(M.leaf_hash(b"x") != M.node_hash(b"x", b"x"), "域分隔：叶子与节点哈希不可能撞上")
ck(M.largest_power_of_two_below(7) == 4, "7 叶树里 k = 4")
ck(M.largest_power_of_two_below(8) == 4, "8 叶树里 k = 4（严格小于）")

# ============================== B. RFC 6962 §2.1.3 的 7 叶示例（官方结构）
d7 = data(7)
root7 = M.mth(d7)
for m, expect_len in ((0, 3), (3, 3), (4, 3), (6, 2)):
    p = M.audit_path(m, d7)
    ck(len(p) == expect_len,
       "7 叶树里 d%d 的审计路径有 %d 个节点（RFC 6962 §2.1.3）" % (m, expect_len))
    ck(M.root_from_audit_path(M.leaf_hash(d7[m]), m, 7, p) == root7,
       "由 d%d 的审计路径复算出根" % m)
    ck(M.verify_inclusion(d7[m], m, 7, p, root7), "verify_inclusion(d%d) 为真" % m)

# ===================================== C. 全尺寸穷举：每个叶子的路径都能复算根
for n in range(1, 23):
    ds = data(n)
    root = M.mth(ds)
    ck(len(root) == 32, "树根 32 字节 n=%d" % n)
    for m in range(n):
        p = M.audit_path(m, ds)
        ck(M.root_from_audit_path(M.leaf_hash(ds[m]), m, n, p) == root,
           "n=%d 时 d%d 的审计路径复算出根" % (n, m))
    ck(not M.verify_inclusion(ds[0], 0, n, M.audit_path(0, ds), bytes(32)),
       "根不匹配时验证失败 n=%d" % n)
    ck(not M.verify_inclusion(b"not-in-tree", 0, n, M.audit_path(0, ds), root),
       "换一个不存在的叶子即验证失败 n=%d" % n)

# 非满树时"看比特决定左右"那条口诀会出错，这里钉住反例
ds3 = data(3)
ck(M.root_from_audit_path(M.leaf_hash(ds3[2]), 2, 3, M.audit_path(2, ds3)) == M.mth(ds3),
   "n=3、m=2：按递归定义能复算出根")
naive = M.node_hash(M.leaf_hash(ds3[2]), M.audit_path(2, ds3)[0])
ck(naive != M.mth(ds3),
   "n=3、m=2 时「当前值永远放左边」的口诀给出的是错误的根（非满树的坑）")
raises(lambda: M.root_from_audit_path(M.leaf_hash(ds3[0]), 0, 1, [bytes(32)]),
       "单叶树的审计路径必须为空")

# ============================================== D. 一致性证明（§2.1.2）
for (old, new) in ((1, 2), (3, 7), (4, 8), (5, 6), (1, 9), (6, 6), (7, 13)):
    ds = data(new)
    proof = M.consistency_proof(old, ds)
    ck(M.verify_consistency(old, new, proof, M.mth(ds[:old]), M.mth(ds)),
       "一致性证明通过 old=%d new=%d（证明 %d 个节点）" % (old, new, len(proof)))
    ck(len(proof) <= (new - 1).bit_length() + 1,
       "证明节点数不超过 ceil(log2(n))+1 old=%d new=%d" % (old, new))
bad_proof = list(M.consistency_proof(3, data(7)))
bad_proof[0] = bytes([bad_proof[0][0] ^ 1]) + bad_proof[0][1:]
ck(not M.verify_consistency(3, 7, bad_proof, M.mth(data(7)[:3]), M.mth(data(7))),
   "篡改一致性证明后验证失败")
raises(lambda: M.consistency_proof(0, data(5)), "从空树出发的一致性证明没有意义")
raises(lambda: M.consistency_proof(6, data(5)), "old > new 时报错")
ck(M.consistency_proof(5, data(5)) == [], "old == new 时证明为空")

# ================================================ E. LogID / 变长字段编码
spki = bytes(range(64))
lid = S.log_id(spki)
ck(len(lid) == 32, "LogID 是 32 字节")
ck(lid == hashlib.sha256(spki).digest(), "LogID = SHA-256(日志公钥的 DER SPKI)")
ck(len(S.vector24(b"x" * 300)) == 3 + 300, "ASN.1Cert 用 3 字节长度前缀")
ck(len(S.vector16(b"x" * 300)) == 2 + 300, "CtExtensions 用 2 字节长度前缀")
raises(lambda: S.vector24(b""), "ASN.1Cert 至少 1 字节（下界不是 0）")
ck(S.vector16(b"") == b"\x00\x00", "CtExtensions 可以是空串（下界是 0）")

# ==================================================== F. SCT / STH 结构
entry = S.signed_entry(S.ENTRY_TYPE_X509, cert_der=b"\x30\x82\x01\x02")
sig_in = S.sct_signing_input(1700000000000, S.ENTRY_TYPE_X509, entry)
ck(sig_in[0] == S.VERSION_V1, "SCT 签名输入以 version = v1(0) 开头")
ck(sig_in[1] == S.SIG_TYPE_CERTIFICATE_TIMESTAMP, "signature_type = certificate_timestamp(0)")
ck(len(sig_in) == 1 + 1 + 8 + 2 + 3 + 4 + 2, "SCT 签名输入各段长度之和")
sct = S.build_sct(lid, 1700000000000, entry, b"\xAA" * 71)
parsed = S.parse_sct(sct)
ck(parsed["version"] == S.VERSION_V1, "解析出的 version 是 v1")
ck(parsed["log_id"] == lid, "解析出的 LogID 往返一致")
ck(parsed["timestamp"] == 1700000000000, "timestamp 是毫秒级 NTP 时间")
ck(parsed["hash_alg"] == S.HASH_ALG_SHA256 and parsed["sig_alg"] == S.SIG_ALG_ECDSA,
   "digitally-signed 前面是 hash_alg ‖ sig_alg 两个字节")
ck(parsed["signature"] == b"\xAA" * 71, "签名原样取出")
ck(len(sct) == 1 + 32 + 8 + 2 + 1 + 1 + 2 + 71, "SCT 总长度正确")
raises(lambda: S.build_sct(b"\x00" * 31, 0, entry, b"sig"), "LogID 不是 32 字节时报错")
raises(lambda: S.parse_sct(sct + b"\x00"), "SCT 尾部有多余字节时报错")
pre = S.signed_entry(S.ENTRY_TYPE_PRECERT, issuer_key_hash=bytes(32), tbs=b"\x30\x01")
ck(len(pre) == 2 + 32 + 3 + 2, "precert_entry = 类型 ‖ issuer_key_hash ‖ TBSCertificate")
raises(lambda: S.signed_entry(S.ENTRY_TYPE_PRECERT, issuer_key_hash=b"\x00" * 31, tbs=b"x"),
       "issuer_key_hash 必须是 32 字节")

sth_in = S.sth_signing_input(1700000000000, 12345, bytes(range(32)))
ck(len(sth_in) == 50, "STH 签名输入正好 50 字节")
st = S.parse_sth_signature_input(sth_in)
ck(st["sig_type"] == S.SIG_TYPE_TREE_HASH, "STH 的 signature_type = tree_hash(1)")
ck(st["tree_size"] == 12345 and st["timestamp"] == 1700000000000, "STH 的树大小与时间往返一致")
ck(st["root_hash"] == bytes(range(32)), "STH 的根哈希原样往返")
raises(lambda: S.sth_signing_input(0, 1, b"\x00" * 31), "root_hash 必须是 32 字节")
ck(S.sth_signing_input(0, 1, bytes(32))[1] != S.sct_signing_input(
    0, S.ENTRY_TYPE_X509, entry)[1], "SCT 与 STH 的 signature_type 不同（域分隔）")

print("PASS=%d FAIL=%d" % (PASS, FAIL))
for m in BAD[:12]:
    print("  FAIL:", m)
sys.exit(1 if FAIL else 0)
