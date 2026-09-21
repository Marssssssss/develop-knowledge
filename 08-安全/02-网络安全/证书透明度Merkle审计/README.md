# 证书透明度（CT）Merkle 树审计

## 简介

CA 会误发证书：配错、被入侵、被胁迫。传统上这类错误只有受害域自己能发现，而且往往很久以后才发现。证书透明度的思路是把**每一张**证书都公开写进只能追加（append-only）的日志，让域所有者、CA 和浏览器都能审计。

RFC 9162《Certificate Transparency Version 2.0》把日志定义成一棵 Merkle 树，并提供两类可公开验证的密码学证明：

- **包含性证明**（§2.1.3）：证明某个条目确实在这棵树里 —— 用来回答「这张证书有没有被记录」；
- **一致性证明**（§2.1.4）：证明新树是旧树的**纯追加** —— 用来回答「日志有没有偷偷改写/删除历史」。

本 demo 把 §2.1 的四组算法（MTH 定义、栈式树头验证、包含性证明的生成与验证、一致性证明的生成与验证）逐行实现，并用 §2.1.5 给出的 7 叶树做测试向量。

## 原理详解

### 1. Merkle Tree Hash（§2.1.1）

```
MTH({})      = HASH()                               空列表
MTH({d[0]})  = HASH(0x00 || d[0])                   叶子
MTH(D_n)     = HASH(0x01 || MTH(D[0:k]) || MTH(D[k:n]))   n > 1
```

其中 `k` 是**严格小于 n 的最大 2 的幂**（`k < n <= 2k`）。叶子前缀 `0x00`、节点前缀 `0x01` 是**域分隔**，RFC 明说这是为了获得第二原像抗性 —— 少了它，攻击者可以把一个内部节点当成叶子重新提交。

由此可推出两个重要性质：

- 树的形状**完全由叶子数决定**，不要求 n 是 2 的幂（对比 [CrosbyWallach] 的 history tree，两者对非满树的处理不同）；
- 7 个叶子的树是 `H(01 || MTH(D[0:4]) || MTH(D[4:7]))`，右子树再拆成 `H(01 || MTH(D[4:6]) || MTH(D[6:7]))`。

### 2. 栈式验证（§2.1.2）

手里有完整条目列表时不必递归，用栈一遍算完：压入叶子哈希后，把 `i` 的**最低位连续 1 的个数**当作合并次数，每次弹两个压一个。这个「连续 1」的计数就是「当前子树能往上合并多少层」的直接刻画。本 demo 对 n=0..20 把栈算法与递归定义逐一对拍，全部一致。

### 3. 包含性证明（§2.1.3）

`PATH(m, D_n)` 是"从叶子走到根所缺的兄弟节点"：

```
PATH(0, {d[0]}) = {}
PATH(m, D_n) = PATH(m,     D[0:k]) : MTH(D[k:n])    m <  k
PATH(m, D_n) = PATH(m - k, D[k:n]) : MTH(D[0:k])    m >= k
```

验证（§2.1.3.2）不递归，靠两个游标 `fn`（从 leaf_index 往根走）和 `sn`（从 tree_size-1 往根走）：

- `LSB(fn)` 为 1 **或** `fn == sn` → 当前节点是右孩子，证明里的节点放左边：`r = HASH(0x01 || p || r)`；
- 否则是左孩子：`r = HASH(0x01 || r || p)`；
- `fn == sn` 这个额外条件是处理**非满树的最右路径**——那条路径上的节点没有兄弟，游标会被额外右移。

最后 `sn` 必须为 0 且算出的 `r` 等于根。

### 4. 一致性证明（§2.1.4）

`PROOF(m, D_n) = SUBPROOF(m, D_n, true)`，布尔参数 `b` 表示「D[0:m] 构成的子树是否是完整子树、其哈希是否已已知」：

```
m == n 且 b      -> {}              已知，无需提交
m == n 且 !b     -> {MTH(D_m)}      必须提交整棵子树的哈希
m <= k           -> SUBPROOF(m,     D[0:k], b    ) : MTH(D[k:n])
m >  k           -> SUBPROOF(m - k, D[k:n], false) : MTH(D[0:k])
```

注意 `m > k` 分支**强制把 b 置为 false**：一旦跨过 k，右子树就不是完整子树了，它的哈希必须被显式提交。

验证（§2.1.4.2）同时算出 `fr`（旧树根）和 `sr`（新树根），只有两个都对上才算通过。若 `first` 恰是 2 的幂，要先把 `first_hash` 补到证明最前面 —— 这就是 `PROOF(4, D7) = [l]` 只有一个节点却能验证通过的原因。

节点数上界是 `ceil(log2(n)) + 1`（7 叶树 → 4）。

### 5. §2.1.5 的测试向量

7 叶树结构（RFC 原图）：

```
               root
              /    \
             k      l
            / \    / \
           g   h  i   j
          / \ /\ /\   |
         a  b c d e  f d6
         |  | | | |  |
        d0 d1 d2 d3 d4 d5
```

| 证明 | RFC 给出 | 本 demo 生成 |
| --- | --- | --- |
| `PATH(0, D7)` | `[b, h, l]` | 一致 |
| `PATH(3, D7)` | `[c, g, l]` | 一致 |
| `PATH(4, D7)` | `[f, j, k]` | 一致 |
| `PATH(6, D7)` | `[i, k]` | 一致 |
| `PROOF(3, D7)` | `[c, d, g, l]` | 一致 |
| `PROOF(4, D7)` | `[l]` | 一致 |
| `PROOF(6, D7)` | `[i, j, k]` | 一致 |

其中 `hash0 = MTH(D[0:3]) = H(01||g||c)`、`hash1 = MTH(D[0:4]) = k`、`hash2 = MTH(D[0:6]) = H(01||k||i)`，与图中增量构建的三步一一对应。

### 6. 签名的对象（§4.8-§4.12）

- **SCT**（§4.8）：日志对 `x509_entry_v2` / `precert_entry_v2` 这个 TransItem 签名，含 `log_id`、`timestamp`、`sct_extensions`、`signature`。
- **STH**（§4.10）：对 `TreeHeadDataV2`（`timestamp` + `tree_size` + `root_hash` + `sth_extensions`）签名。**时间戳参与签名**，所以同一个根在不同时刻会产出不同的 STH；日志在一个 MMD 周期内没有新条目时，也要用新时间戳重签同一个根。
- 两类扩展都必须**按 extension_type 升序且不得重复**（§4.9/§4.10），本 demo 把这个约束做成了可测的判定函数。

## 对比表

| 维度 | 包含性证明 | 一致性证明 |
| --- | --- | --- |
| 回答的问题 | 这个条目在树里吗 | 新树是不是旧树的纯追加 |
| 生成 | `PATH(m, D_n)` | `SUBPROOF(m, D_n, true)` |
| 验证结束条件 | `sn == 0` 且 `r == root_hash` | `sn == 0` 且 `fr == first_hash` 且 `sr == second_hash` |
| 7 叶树典型长度 | 3（d0/d3/d4）～2（d6） | 1～4 |
| 长度上界 | 树高 | `ceil(log2(n)) + 1` |

## 环境

- Python 3.13（仅标准库 `hashlib`）
- Go 1.22（仅标准库 `crypto/sha256`）
- 不涉及网络：日志的提交/查询接口（§5）不在本 demo 范围内

## 运行方式

```bash
cd python && python selfcheck_ctmerkle.py   # 675 条断言，全部实跑
cd python && python main.py                 # 打印 §2.1.5 的向量对照
cd go     && go run .
```

## 关键代码

```python
def verify_inclusion(leaf_index, tree_size, proof, leaf_data, root_hash):
    if leaf_index >= tree_size:
        return False, "leaf_index >= tree_size"
    fn, sn = leaf_index, tree_size - 1
    r = leaf_hash(leaf_data)
    for p in proof:
        if sn == 0:
            return False, "sn == 0 但证明还没走完"
        if fn & 1 or fn == sn:              # 右孩子，或非满树的最右路径
            r = HASH(NODE_PREFIX + p + r)
            if not (fn & 1):
                while fn != 0 and not (fn & 1):
                    fn >>= 1
                    sn >>= 1
        else:                                # 左孩子
            r = HASH(NODE_PREFIX + r + p)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        return False, "走完证明后 sn != 0"
    return (r == root_hash), ("根哈希不匹配" if r != root_hash else "")
```

## 性能与边界

- **MTH 是递归的**，深度 `O(log n)`、节点数 `O(n)`；实现里 `entries[:k]` 会复制切片，超大日志应改用下标区间而不是切片。
- **`PATH` 与 `SUBPROOF` 同样是递归 + 切片**，生产实现（如 certificate-transparency-go）用迭代 + 缓存中间节点哈希，避免重复计算。
- **一致性证明 `PROOF(m, D_n)` 要求 `0 < m < n`**；`m == n` 时 `PROOF` 是空列表，而 §2.1.4.2 第 1 步明确规定空的一致性证明**必须判失败**——两个"空"的含义不同，别混。
- **n=0 的树**：`MTH({}) = HASH()`，此时既没有包含性证明也没有一致性证明，§2.1.4.2 的 `first > 0` 前提不成立。

## 注意事项与常见坑

1. **`k` 是严格小于 n，不是小于等于**：`n=4` 时 `k=2`（不是 4），`n=8` 时 `k=4`。写成 `<=` 会让满二叉树的根算成 `HASH(0x01 || MTH(全树) || MTH(空))`，结果完全错误却不会报错。
2. **叶子和节点的前缀不同**：`0x00` / `0x01`。这是第二原像抗性的必要条件，千万别统一成不前缀或统一成一个值。
3. **验证包含性证明时 `fn == sn` 这个条件不能省**：它专门处理非满树最右路径上"没有兄弟"的节点。省掉它，`PATH(6, D7) = [i, k]` 这类最右叶子的证明会验证失败。
4. **一致性证明里 `first` 是 2 的幂要补 `first_hash`**：`PROOF(4, D7) = [l]` 只有 1 个节点，验证时内部会先把它变成 `[k, l]`（因为 `first=4` 是 2 的幂）。不补的话第一步就会拿 `l` 当 `fr`，直接对不上。
5. **`m > k` 分支要把 `b` 置 false**：这是 `PROOF(3, D7)` 里 `[c, d, g]` 那一段的来源；如果把 `b` 一路传下去，最内层就不会提交 `MTH(D[0:1])`，证明会少节点而验证失败。
6. **结构编码里的长度前缀位数不一样**：`NodeHash<32..2^8-1>` 是 **1 字节**长度（上限 255），而 `sth_extensions<0..2^16-1>` 是 **2 字节**。把两者都写成 2 字节会让 TreeHeadDataV2 多 1 个字节，STH 签名将对不上。
7. **本 demo 只覆盖 §2.1 的树算法**，不涉及 §5 的 HTTP 接口、§7 的证书扩展与 §8 的客户端验证流程；`log_id` 在 demo 里用 32 字节占位，真实值是日志公钥的 SHA-256。

## 参考资料

- RFC 9162《Certificate Transparency Version 2.0》§2.1.1（MTH 定义与域分隔）、§2.1.2（栈式树头验证）、§2.1.3.1/§2.1.3.2（包含性证明的生成与验证）、§2.1.4.1/§2.1.4.2（一致性证明的生成与验证）、§2.1.5（7 叶树示例）、§4.7-§4.12（叶子/SCT/STH/一致性证明/包含性证明的结构）—— https://www.rfc-editor.org/rfc/rfc9162.txt
- RFC 6962《Certificate Transparency》§2（初版 Merkle 树定义，§2.1.5 的图即源于此）—— https://www.rfc-editor.org/rfc/rfc6962.txt
