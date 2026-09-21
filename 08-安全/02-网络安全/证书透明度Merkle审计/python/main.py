"""证书透明度 Merkle 审计：把 RFC 9162 §2.1.5 的 7 叶树跑出来。"""

from ctmerkle import (
    HASH, NODE_PREFIX, leaf_hash, mth, mth_from_entries, path,
    verify_inclusion, proof, verify_consistency, proof_length_bound,
    encode_tree_head, encode_inclusion_proof,
)

D = [("d%d" % i).encode() for i in range(7)]
a, b = leaf_hash(D[0]), leaf_hash(D[1])
c, d = leaf_hash(D[2]), leaf_hash(D[3])
e, f = leaf_hash(D[4]), leaf_hash(D[5])
g = HASH(NODE_PREFIX + a + b)
h = HASH(NODE_PREFIX + c + d)
i = HASH(NODE_PREFIX + e + f)
j = leaf_hash(D[6])
k = HASH(NODE_PREFIX + g + h)
l = HASH(NODE_PREFIX + i + j)
root = HASH(NODE_PREFIX + k + l)

print("== 1. §2.1.5 的 7 叶树 ==")
print("   叶: a=%s… b=%s…" % (a.hex()[:8], b.hex()[:8]))
print("   g=H(01||a||b)  h=H(01||c||d)  i=H(01||e||f)  j=leaf(d6)")
print("   k=H(01||g||h)  l=H(01||i||j)  root=H(01||k||l)")
print("   MTH(D[0:4])==k : %s ; MTH(D[4:7])==l : %s" % (mth(D[:4]) == k, mth(D[4:]) == l))

print("\n== 2. 四个包含性证明（§2.1.5）==")
for m, expect in ((0, [b, h, l]), (3, [c, g, l]), (4, [f, j, k]), (6, [i, k])):
    got = path(m, D)
    print("   PATH(%d, D7) = %s  与文档一致: %s" % (
        m, " ".join(x.hex()[:6] for x in got), got == expect))

print("\n== 3. 包含性验证（§2.1.3.2）==")
for m in range(7):
    okk, why = verify_inclusion(m, 7, path(m, D), D[m], root)
    print("   leaf %d -> %s %s" % (m, "OK " if okk else "FAIL", why))
bad = [
    ("顺序颠倒", lambda: verify_inclusion(0, 7, [b, l, h], D[0], root)),
    ("少一个节点", lambda: verify_inclusion(0, 7, [b, h], D[0], root)),
    ("多一个节点", lambda: verify_inclusion(0, 7, [b, h, l, k], D[0], root)),
    ("index 越界", lambda: verify_inclusion(7, 7, [i, k], D[6], root)),
]
for name, fn in bad:
    okk, why = fn()
    print("   负例 %-10s -> %s (%s)" % (name, "通过" if okk else "拒绝", why))

print("\n== 4. 三个一致性证明（§2.1.5）==")
t3, t4, t6, t7 = mth(D[:3]), mth(D[:4]), mth(D[:6]), mth(D)
for m, expect in ((3, [c, d, g, l]), (4, [l]), (6, [i, j, k])):
    got = proof(m, D)
    print("   PROOF(%d, D7) 节点数=%d 与文档一致: %s" % (m, len(got), got == expect))
for m, th in ((3, t3), (4, t4), (6, t6)):
    okk, why = verify_consistency(m, 7, proof(m, D), th, t7)
    print("   verify(%d -> 7) = %s %s" % (m, okk, why))
print("   节点数上界 ceil(log2(7))+1 = %d" % proof_length_bound(7))

print("\n== 5. 栈算法（§2.1.2）与递归定义对拍 ==")
print("   n=0..20 全部一致: %s" % all(
    mth_from_entries([("s%d" % t).encode() for t in range(n)])
    == mth([("s%d" % t).encode() for t in range(n)]) for n in range(21)))

print("\n== 6. 结构编码长度 ==")
print("   TreeHeadDataV2       = %d 字节 (8+8+1+32+2)" % len(encode_tree_head(0, 7, root)))
print("   InclusionProofDataV2 = %d 字节 (1+32+8+8+2+64)"
      % len(encode_inclusion_proof(b"\x01" * 32, 7, 6, [i, k])))
