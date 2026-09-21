"""证书透明度 Merkle 审计自检（实跑）。测试向量取自 RFC 9162 §2.1.5。"""

import sys
from ctmerkle import (
    HASH, LEAF_PREFIX, NODE_PREFIX, HASH_SIZE, largest_power_of_two_below,
    leaf_hash, mth, mth_from_entries, path, verify_inclusion, subproof, proof,
    verify_consistency, encode_tree_head, encode_inclusion_proof,
    extensions_valid, proof_length_bound,
)

N_PASS = 0


def ok(cond, msg):
    global N_PASS
    assert cond, "ASSERT FAILED: " + msg
    N_PASS += 1


D = [("d%d" % i).encode() for i in range(7)]

# §2.1.5 图中命名的节点
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

# ---------------------------------------------------------------- E1 树形状

ok(largest_power_of_two_below(7) == 4, "E1a n=7 时 k=4（k < n <= 2k）")
for n, kk in ((2, 1), (3, 2), (4, 2), (5, 4), (8, 4), (9, 8)):
    ok(largest_power_of_two_below(n) == kk, "E1b n=%d -> k=%d" % (n, kk))
ok(mth(D[:4]) == k, "E1c MTH(D[0:4]) == k")
ok(mth(D[4:]) == l, "E1d MTH(D[4:7]) == l")
ok(mth(D) == root, "E1e MTH(D[0:7]) == 根")
ok(mth([]) == HASH(b""), "E1f MTH({}) = HASH()（空串的哈希）")
ok(mth(D[:1]) == leaf_hash(D[0]) == a, "E1g MTH({d0}) = HASH(0x00 || d0)")
ok(HASH_SIZE() == 32, "E1h SHA-256 的 HASH_SIZE = 32")

# ---------------------------------------------------------------- E2 §2.1.5 的四个包含性证明

ok(path(0, D) == [b, h, l], "E2a PATH(0, D7) = [b, h, l]")
ok(path(3, D) == [c, g, l], "E2b PATH(3, D7) = [c, g, l]")
ok(path(4, D) == [f, j, k], "E2c PATH(4, D7) = [f, j, k]")
ok(path(6, D) == [i, k], "E2d PATH(6, D7) = [i, k]")
ok(path(0, D[:1]) == [], "E2e 单叶树的证明为空")

# ---------------------------------------------------------------- E3 包含性证明验证

for m in range(7):
    pr = path(m, D)
    ok(verify_inclusion(m, 7, pr, D[m], root)[0], "E3a 叶子 %d 的证明被接受" % m)
ok(verify_inclusion(0, 7, [b, h, l], D[0], root)[0], "E3b 用文档向量直接验证 d0")
ok(verify_inclusion(6, 7, [i, k], D[6], root)[0], "E3c 用文档向量直接验证 d6")
ok(not verify_inclusion(7, 7, [i, k], D[6], root)[0], "E3d leaf_index == tree_size 被拒")
ok(not verify_inclusion(0, 7, [b, h], D[0], root)[0], "E3e 证明少一个节点被拒")
ok(not verify_inclusion(0, 7, [b, h, l, k], D[0], root)[0], "E3f 证明多一个节点被拒")
ok(not verify_inclusion(0, 7, [b, l, h], D[0], root)[0], "E3g 证明顺序颠倒被拒")
ok(not verify_inclusion(0, 7, [b, h, l], D[1], root)[0], "E3h 用错叶子数据被拒")
ok(not verify_inclusion(0, 7, [b, h, l], D[0], k)[0], "E3i 根哈希给错被拒")
# 单叶树：空证明 + 叶子哈希 == 根
ok(verify_inclusion(0, 1, [], D[0], mth(D[:1]))[0], "E3j 单叶树空证明通过")

# 任意大小的树都要能通过（0..20）
for n in range(1, 21):
    ents = [("x%d" % t).encode() for t in range(n)]
    r = mth(ents)
    for m in range(n):
        ok(verify_inclusion(m, n, path(m, ents), ents[m], r)[0],
           "E3k n=%d m=%d 的包含性证明" % (n, m))

# ---------------------------------------------------------------- E4 §2.1.5 的三个一致性证明

ok(proof(3, D) == [c, d, g, l], "E4a PROOF(3, D7) = [c, d, g, l]")
ok(proof(4, D) == [l], "E4b PROOF(4, D7) = [l]")
ok(proof(6, D) == [i, j, k], "E4c PROOF(6, D7) = [i, j, k]")
ok(subproof(3, D, True) == proof(3, D), "E4d PROOF 就是 b=true 的 SUBPROOF")
ok(subproof(4, D[:4], True) == [], "E4e m==n 且 b=true 时子证明为空")
ok(subproof(4, D[:4], False) == [mth(D[:4])], "E4f m==n 且 b=false 时提交整棵子树哈希")
ok(subproof(3, D[:4], False) == [c, d, g],
   "E4g b=false 会让最内层退化成 {MTH(D_m)}（这里的 g 就是 MTH(D[0:2]) 的兄弟路径产物）")

# ---------------------------------------------------------------- E5 一致性证明验证

tree3, tree4, tree6, tree7 = mth(D[:3]), mth(D[:4]), mth(D[:6]), mth(D)
ok(tree4 == k, "E5a MTH(D[0:4]) == k（与图中 hash1 一致）")
ok(tree6 == HASH(NODE_PREFIX + k + i), "E5b MTH(D[0:6]) == H(01||k||i)（图中 hash2）")
ok(tree3 == HASH(NODE_PREFIX + g + c), "E5c MTH(D[0:3]) == H(01||g||c)（图中 hash0）")
ok(verify_consistency(3, 7, proof(3, D), tree3, tree7)[0], "E5d PROOF(3,7) 验证通过")
ok(verify_consistency(4, 7, proof(4, D), tree4, tree7)[0], "E5e PROOF(4,7) 验证通过")
ok(verify_consistency(6, 7, proof(6, D), tree6, tree7)[0], "E5f PROOF(6,7) 验证通过")
ok(not verify_consistency(3, 7, [], tree3, tree7)[0], "E5g 空的一致性证明被拒")
ok(not verify_consistency(3, 7, proof(3, D), tree4, tree7)[0], "E5h first_hash 给错被拒")
ok(not verify_consistency(3, 7, proof(3, D), tree3, tree6)[0], "E5i second_hash 给错被拒")
ok(not verify_consistency(3, 7, list(reversed(proof(3, D))), tree3, tree7)[0],
   "E5j 顺序颠倒被拒")
# first 是 2 的幂时要先把 first_hash 补到最前
ok(verify_consistency(4, 7, [l], tree4, tree7)[0], "E5k first=4 是 2 的幂，内部自动补 first_hash")
ok(len(proof(4, D)) == 1, "E5l PROOF(4,7) 只有 1 个节点")

# 任意 (first, second) 组合
for second in range(2, 21):
    ents = [("y%d" % t).encode() for t in range(second)]
    r2 = mth(ents)
    for first in range(1, second):
        pr = proof(first, ents)
        ok(verify_consistency(first, second, pr, mth(ents[:first]), r2)[0],
           "E5m first=%d second=%d 的一致性证明" % (first, second))
        ok(len(pr) <= proof_length_bound(second),
           "E5n first=%d second=%d 的节点数 %d 不超过上界 %d"
           % (first, second, len(pr), proof_length_bound(second)))

# 附加属性：一致性证明不能为"日志回滚"背书
ents8 = [("z%d" % t).encode() for t in range(8)]
ok(not verify_consistency(3, 7, proof(3, D), tree3, mth(ents8))[0],
   "E5o 把 second_hash 换成另一棵树的根会被拒（append-only 不可伪造）")

# ---------------------------------------------------------------- E6 §2.1.2 栈算法

for n in range(0, 21):
    ents = [("s%d" % t).encode() for t in range(n)]
    ok(mth_from_entries(ents) == mth(ents), "E6 n=%d 时栈算法与递归定义一致" % n)
ok(mth_from_entries([]) == HASH(b""), "E6' 空列表的栈算法结果")

# ---------------------------------------------------------------- E7 结构编码

th = encode_tree_head(1700000000000, 7, root)
ok(len(th) == 8 + 8 + 1 + 32 + 2, "E7a TreeHeadDataV2 长度 = %d（8+8+1+32+2）" % len(th))
ok(th[16] == 32, "E7b NodeHash 的 1 字节长度前缀是 32")
ok(int.from_bytes(th[8:16], "big") == 7, "E7c tree_size 是大端 uint64")
ok(extensions_valid([(1, b"a"), (2, b"b"), (5, b"c")]), "E7d 升序无重复 -> 合法")
ok(not extensions_valid([(2, b"a"), (1, b"b")]), "E7e 乱序 -> 非法")
ok(not extensions_valid([(3, b"a"), (3, b"b")]), "E7f 同类型重复 -> 非法")
ok(extensions_valid([]), "E7g 空扩展列表合法")
ip = encode_inclusion_proof(b"\x01" * 32, 7, 6, [i, k])
ok(len(ip) == 1 + 32 + 8 + 8 + 2 + 64, "E7h InclusionProofDataV2 长度 = %d" % len(ip))
ok(encode_tree_head(1, 2, root) != encode_tree_head(2, 2, root),
   "E7i 时间戳不同 -> 被签名的字节不同（STH 的时间戳参与签名）")

print("PASS %d assertions" % N_PASS)
sys.exit(0)
