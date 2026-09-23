"""RFC 6962 §2.1 的 Merkle 树：MTH、审计路径 PATH、一致性证明 PROOF/SUBPROOF。

三个要点（都写在规范原文里）：

1. **叶子与内部节点用不同的前缀**：`MTH({d0}) = SHA-256(0x00 ‖ d0)`，
   `MTH(D[n]) = SHA-256(0x01 ‖ MTH(D[0:k]) ‖ MTH(D[k:n]))`。
   规范明确说这个域分隔是**为了抗第二原像**——没有它就能把内部节点冒充成叶子。
2. **树不必是满的**：k 是「小于 n 的最大 2 的幂」，n 不是 2 的幂时树不平衡，
   但形状由叶子数唯一决定。
3. **空树的根是 SHA-256("")** 而不是全零（transparency-dev 的 `EmptyRoot()` 就这么写）。
"""

import hashlib

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


def sha256(b):
    return hashlib.sha256(b).digest()


def largest_power_of_two_below(n):
    """k < n <= 2k 里最大的那个 2 的幂。"""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def leaf_hash(data):
    return sha256(LEAF_PREFIX + data)


def node_hash(left, right):
    return sha256(NODE_PREFIX + left + right)


def empty_root():
    return sha256(b"")


def mth(entries):
    """Merkle Tree Hash（递归定义，直接照抄规范）。"""
    n = len(entries)
    if n == 0:
        return empty_root()
    if n == 1:
        return leaf_hash(entries[0])
    k = largest_power_of_two_below(n)
    return node_hash(mth(entries[:k]), mth(entries[k:]))


def audit_path(m, entries):
    """PATH(m, D[n])：叶子 d(m) 的审计路径（返回兄弟节点列表）。"""
    n = len(entries)
    if n == 1:
        return []
    k = largest_power_of_two_below(n)
    if m < k:
        return audit_path(m, entries[:k]) + [mth(entries[k:])]
    return audit_path(m - k, entries[k:]) + [mth(entries[:k])]


def root_from_audit_path(leaf, m, n, path):
    """按 PATH 的递归定义**反向**拼回根。

    注意这里不能套用「看 m 的第 i 位是 0 还是 1 决定左右」那条流行口诀 ——
    它只在满树时成立。n=3、m=2 时审计路径只有一项，口诀会拼出
    `H(leaf2 ‖ MTH01)`，而真根是 `H(MTH01 ‖ leaf2)`（左右反了）。
    照递归定义写才不会出错：m 落在左半边就拼 `(左, 右半)`，否则拼 `(左半, 右)`。
    """
    if n == 1:
        if path:
            raise ValueError("单叶树的审计路径必须为空")
        return leaf
    if not path:
        raise ValueError("审计路径长度不足")
    k = largest_power_of_two_below(n)
    if m < k:
        return node_hash(root_from_audit_path(leaf, m, k, path[:-1]), path[-1])
    return node_hash(path[-1], root_from_audit_path(leaf, m - k, n - k, path[:-1]))


def verify_inclusion(leaf_data, m, n, path, root):
    """验证「d(m) 在 n 个叶子的树里」，且根等于 root。"""
    if m >= n:
        return False
    return root_from_audit_path(leaf_hash(leaf_data), m, n, path) == root


def subproof(m, entries, b):
    """SUBPROOF(m, D[n], b)，RFC 6962 §2.1.2 的递归定义。"""
    n = len(entries)
    if m == n:
        return [] if b else [mth(entries)]
    k = largest_power_of_two_below(n)
    if m <= k:
        return subproof(m, entries[:k], b) + [mth(entries[k:])]
    return subproof(m - k, entries[k:], False) + [mth(entries[:k])]


def consistency_proof(m, entries):
    """PROOF(m, D[n]) = SUBPROOF(m, D[n], true)。"""
    if m == 0:
        raise ValueError("从空树出发的一致性证明没有意义")
    if m > len(entries):
        raise ValueError("m 不能大于当前树的大小")
    return subproof(m, entries, True)


def verify_consistency(old_size, new_size, proof, old_root, new_root):
    """由一致性证明同时复算两个根。

    RFC 6962 只定义**怎么构造**证明（PROOF/SUBPROOF），验证算法留给实现。
    这里是构造过程的逆过程：`b=True` 表示"旧根已知、证明里不含它"，
    `b=False` 表示"证明的第一项就是那个子树的旧根"。
    自检用「复算出的两个根 == 直接算出的 MTH」钉住它。
    """
    def rec(m, n, proof, b):
        """返回 (旧根, 新根)；b=True 时旧根沿用调用方已知值。"""
        if m == n:
            if b:
                if proof:
                    raise ValueError("旧根已知时不应再有多余的证明节点")
                return old_root, old_root
            if len(proof) != 1:
                raise ValueError("b=False 的边界情形应当只含一个节点")
            return proof[0], proof[0]
        if not proof:
            raise ValueError("证明长度不足")
        k = largest_power_of_two_below(n)
        last, rest = proof[-1], proof[:-1]
        if m <= k:                                   # 右子树只在新树里
            old, left = rec(m, k, rest, b)
            return old, node_hash(left, last)
        # m > k：D[0:k] 在两棵树里完全相同，last 既是旧根的左半也是新根的左半
        old_right, right = rec(m - k, n - k, rest, False)
        return node_hash(last, old_right), node_hash(last, right)

    old, new = rec(old_size, new_size, list(proof), True)
    return old == old_root and new == new_root
