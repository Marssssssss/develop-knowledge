"""证书透明度（CT）Merkle 树审计：RFC 9162 §2.1 的逐行实现。

实现内容：
  - §2.1.1 MTH（Merkle Tree Hash）递归定义
  - §2.1.2 由完整条目列表用栈验证树头
  - §2.1.3.1/2 PATH() 生成与 §2.1.3.2 包含性证明验证
  - §2.1.4.1/2 PROOF()/SUBPROOF() 生成与一致性证明验证
  - §4.9/§4.10/§4.12 的 TreeHeadDataV2 / STH / InclusionProofDataV2 结构编码

§2.1.5 给出的 7 叶树是本模块的主要测试向量。
"""

import hashlib

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


def HASH(data):
    """日志的哈希算法（§4.1 的 log parameter），本 demo 固定用 SHA-256。"""
    return hashlib.sha256(data).digest()


def HASH_SIZE():
    return len(HASH(b""))


# ---------------------------------------------------------------- §2.1.1

def largest_power_of_two_below(n):
    """k 是严格小于 n 的最大 2 的幂：k < n <= 2k。"""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def leaf_hash(d):
    return HASH(LEAF_PREFIX + d)


def mth(entries):
    """MTH(D_n)。

    MTH({})      = HASH()
    MTH({d[0]})  = HASH(0x00 || d[0])
    MTH(D_n)     = HASH(0x01 || MTH(D[0:k]) || MTH(D[k:n])),  n > 1
    """
    if not entries:
        return HASH(b"")
    if len(entries) == 1:
        return leaf_hash(entries[0])
    k = largest_power_of_two_below(len(entries))
    return HASH(NODE_PREFIX + mth(entries[:k]) + mth(entries[k:]))


# ---------------------------------------------------------------- §2.1.2

def mth_from_entries(entries):
    """用栈增量算出树头（§2.1.2 的四步算法）。"""
    stack = []
    for i, e in enumerate(entries):
        stack.append(leaf_hash(e))
        merge_count = 0
        while (i >> merge_count) & 1 == 1:
            merge_count += 1
        for _ in range(merge_count):
            right = stack.pop()
            left = stack.pop()
            stack.append(HASH(NODE_PREFIX + left + right))
    while len(stack) > 1:
        right = stack.pop()
        left = stack.pop()
        stack.append(HASH(NODE_PREFIX + left + right))
    return stack[0] if stack else HASH(b"")


# ---------------------------------------------------------------- §2.1.3.1

def path(m, entries):
    """PATH(m, D_n)：第 (m+1) 个输入的包含性证明。

    PATH(0, {d[0]}) = {}
    PATH(m, D_n) = PATH(m, D[0:k]) : MTH(D[k:n])        m < k
    PATH(m, D_n) = PATH(m-k, D[k:n]) : MTH(D[0:k])      m >= k
    """
    n = len(entries)
    if n == 1:
        return []
    k = largest_power_of_two_below(n)
    if m < k:
        return path(m, entries[:k]) + [mth(entries[k:])]
    return path(m - k, entries[k:]) + [mth(entries[:k])]


# ---------------------------------------------------------------- §2.1.3.2

def verify_inclusion(leaf_index, tree_size, proof, leaf_data, root_hash):
    """§2.1.3.2 的六步验证。返回 (是否通过, 失败原因)。"""
    if leaf_index >= tree_size:
        return False, "leaf_index >= tree_size"
    fn, sn = leaf_index, tree_size - 1
    r = leaf_hash(leaf_data)
    for p in proof:
        if sn == 0:
            return False, "sn == 0 但证明还没走完"
        if fn & 1 or fn == sn:
            r = HASH(NODE_PREFIX + p + r)
            if not (fn & 1):
                while fn != 0 and not (fn & 1):
                    fn >>= 1
                    sn >>= 1
        else:
            r = HASH(NODE_PREFIX + r + p)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        return False, "走完证明后 sn != 0"
    return (r == root_hash), ("根哈希不匹配" if r != root_hash else "")


# ---------------------------------------------------------------- §2.1.4.1

def subproof(m, entries, b):
    """SUBPROOF(m, D_n, b)。"""
    n = len(entries)
    if m == n:
        return [] if b else [mth(entries)]
    k = largest_power_of_two_below(n)
    if m <= k:
        return subproof(m, entries[:k], b) + [mth(entries[k:])]
    return subproof(m - k, entries[k:], False) + [mth(entries[:k])]


def proof(m, entries):
    """PROOF(m, D_n) = SUBPROOF(m, D_n, true)。"""
    return subproof(m, entries, True)


# ---------------------------------------------------------------- §2.1.4.2

def verify_consistency(first, second, consistency_path, first_hash, second_hash):
    """§2.1.4.2 的七步验证。返回 (是否通过, 失败原因)。"""
    if not consistency_path:
        return False, "consistency_path 为空"
    path_list = list(consistency_path)
    if first & (first - 1) == 0 and first != 0:
        path_list = [first_hash] + path_list
    fn, sn = first - 1, second - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = path_list[0]
    for c in path_list[1:]:
        if sn == 0:
            return False, "sn == 0 但证明还没走完"
        if fn & 1 or fn == sn:
            fr = HASH(NODE_PREFIX + c + fr)
            sr = HASH(NODE_PREFIX + c + sr)
            if not (fn & 1):
                while fn != 0 and not (fn & 1):
                    fn >>= 1
                    sn >>= 1
        else:
            sr = HASH(NODE_PREFIX + sr + c)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        return False, "走完证明后 sn != 0"
    if fr != first_hash:
        return False, "fr != first_hash"
    if sr != second_hash:
        return False, "sr != second_hash"
    return True, ""


# ---------------------------------------------------------------- §4.9-4.12

def encode_vector(opaque, max_len=255):
    """TLS 表示法里 opaque X<min..max> 的长度前缀。"""
    assert len(opaque) <= max_len
    return bytes([len(opaque)]) + opaque


def encode_tree_head(timestamp, tree_size, root_hash, sth_extensions=b""):
    """TreeHeadDataV2（§4.9）：uint64 timestamp + uint64 tree_size +
    NodeHash<32..2^8-1> + Extension sth_extensions<0..2^16-1>。"""
    out = timestamp.to_bytes(8, "big")
    out += tree_size.to_bytes(8, "big")
    out += encode_vector(root_hash)
    out += len(sth_extensions).to_bytes(2, "big") + sth_extensions
    return out


def extensions_valid(pairs):
    """§4.9/§4.10：同类型不得重复，且必须按 extension_type 升序排列。"""
    types = [t for t, _ in pairs]
    return len(set(types)) == len(types) and types == sorted(types)


def encode_inclusion_proof(log_id, tree_size, leaf_index, inclusion_path):
    """InclusionProofDataV2（§4.12）。"""
    out = encode_vector(log_id)
    out += tree_size.to_bytes(8, "big")
    out += leaf_index.to_bytes(8, "big")
    out += len(inclusion_path).to_bytes(2, "big")
    for node in inclusion_path:
        out += node
    return out


def proof_length_bound(n):
    """§2.1.4.1 末句：一致性证明的节点数上界 ceil(log2(n)) + 1。"""
    if n <= 1:
        return 1
    k = 0
    while (1 << k) < n:
        k += 1
    return k + 1
