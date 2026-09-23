# 证书透明化：Merkle 审计与 SCT / STH（RFC 6962）

CT 要解决的是**"CA 背着你签了证书"**这一类问题：日志（log）对每张证书发一张
**SCT（Signed Certificate Timestamp）**，而 SCT 只有在日志真的把证书写进了
**只增不改**的 Merkle 树时才拿得出包含证明。本 demo 实现 RFC 6962 的树、两种证明与
SCT/STH 的 TLS 编码。

## 1. Merkle Tree Hash（§2.1）

```text
MTH({})      = SHA-256()                                   ← 空树的根是空串的哈希，不是全零
MTH({d0})    = SHA-256(0x00 ‖ d0)                          ← 叶子前缀 0x00
MTH(D[n])    = SHA-256(0x01 ‖ MTH(D[0:k]) ‖ MTH(D[k:n]))   ← 节点前缀 0x01，k = 小于 n 的最大 2 的幂
```

规范明确写了为什么要有两个前缀：**域分隔是抗第二原像的必要条件**——没有它，
攻击者可以把一个内部节点的哈希冒充成某个叶子的哈希。
树**不必是满的**：n 不是 2 的幂时不平衡，但形状由 n 唯一决定。

## 2. 审计路径（§2.1.1）与非满树的坑

`PATH(m, D[n])` 递归定义：`m < k` 时取左半的路径再拼上 `MTH(D[k:n])`，
否则取 `PATH(m-k, D[k:n])` 再拼上 `MTH(D[0:k])`。

**验证时不要套用"看叶子索引第 i 位是 0 还是 1 决定左右"那条流行口诀** —— 它只在满树时成立。
RFC 6962 §2.1.3 的 7 叶示例里，`d6` 的审计路径只有 2 个节点（`[i, k]`），
若按口诀把当前值永远放左边就会拼反；demo 里用 n=3、m=2 这条最小反例钉住了它
（`root_from_audit_path` 照递归定义写，口诀版算出的根与真根不同）。

官方示例的结构断言：`d0/d3/d4` 的路长 3，`d6` 的路长 2，四者都能复算出同一个根。

## 3. 一致性证明（§2.1.2）：append-only 的证据

```text
PROOF(m, D[n]) = SUBPROOF(m, D[n], true)
SUBPROOF(m, D[m], true)  = {}                        ← 旧根已知，不用给
SUBPROOF(m, D[m], false) = {MTH(D[m])}               ← 旧根未知，得给出来
m <= k: SUBPROOF(m, D[0:k], b)   : MTH(D[k:n])
m >  k: SUBPROOF(m-k, D[k:n], false) : MTH(D[0:k])
```

节点数上界是 `ceil(log2(n)) + 1` —— 与树有多大**无关**。
它让"日志给 A 看一棵树、给 B 看另一棵"必然被检出，这是 CT 信任模型的支点。
RFC 只定义**怎么构造**证明，验证算法留给实现；demo 的 `verify_consistency`
按构造的逆过程同时复算旧根与新根，并用"与直接算出的 MTH 相等"钉住。

## 4. SCT 与 STH（§3.2 / §3.5）

| 字段 | 编码 | 易错点 |
| --- | --- | --- |
| `LogID` | `opaque key_id[32]` | 是**日志公钥 DER（SubjectPublicKeyInfo）的 SHA-256**，不是域名哈希、不是证书哈希 |
| `timestamp` | `uint64` | NTP 时间，**单位是毫秒** |
| `ASN.1Cert` | `opaque <1..2^24-1>` | **3 字节**长度前缀，下界是 1 不是 0 |
| `CtExtensions` | `opaque <0..2^16-1>` | **2 字节**长度前缀，下界是 0 |
| `digitally-signed` | `hash_alg ‖ sig_alg ‖ <2 字节长度> ‖ signature` | 长度前缀是 2 字节 |

被签的内容也不同，这是又一层域分隔：

```text
SCT: version ‖ certificate_timestamp(0) ‖ timestamp ‖ entry_type ‖ signed_entry ‖ extensions
STH: version ‖ tree_hash(1)             ‖ timestamp ‖ tree_size ‖ sha256_root_hash   ← 正好 50 字节
```

（`enum { certificate_timestamp(0), tree_hash(1) }` 与 `enum { v1(0) }` 的取值来自规范原文。）

## 5. 运行

```bash
python python/selfcheck_ctree.py   # 385 条断言全绿
python python/main.py
go run go/ct.go go/main.go
```

自检覆盖：空树根的官方十六进制值、前缀与域分隔、7 叶示例的四个路径长度、
**n = 1..22 全尺寸下每个叶子的路径都复算出根**、伪造叶子/伪造根均失败、
非满树口诀反例、七组 `(old,new)` 的一致性证明与篡改失败、LogID 与两类变长字段、
SCT 解析往返与尾部多余字节报错、precert 的 `issuer_key_hash` 长度校验、STH 50 字节定长。

## 6. 参考资料（实读）

- RFC 6962（Certificate Transparency，55048 B）：https://www.rfc-editor.org/rfc/rfc6962.txt
  —— §2.1 MTH 定义与"域分隔是为抗第二原像"的原话、§2.1.1 PATH、§2.1.2 PROOF/SUBPROOF
  与"节点数上界 ceil(log2(n))+1"、§2.1.3 七叶示例、§3.2 SCT 结构与枚举取值、§3.5 STH
- transparency-dev/merkle `rfc6962/rfc6962.go`（2991 B）：`RFC6962LeafHashPrefix = 0`、
  `RFC6962NodeHashPrefix = 1`、`EmptyRoot()` 对 SHA-256 取 `sha256.Sum256(nil)`
  —— https://github.com/transparency-dev/merkle/blob/main/rfc6962/rfc6962.go
- transparency-dev/merkle `proof/proof.go`（13093 B）：`Consistency` 要求 `0 < size1 <= size2`，
  且"从空树出发的一致性证明没有意义" —— 本 demo 的入参校验照此实现

> 口径：一致性证明的**验证**算法 RFC 6962 未定义，本 demo 按构造过程的逆过程实现并在 README 标明；
> 证书解析（ASN.1、吊销、SCT 列表的 X.509v3 扩展 OID）不在本 demo 范围内。
> 与 `02-网络安全/证书透明度Merkle审计/` 是同一主题的两次切入：那一篇以 **RFC 9162（CT v2）**
> 为主，本 demo 回到 **RFC 6962** 的原始定义，并把 SCT/STH 的字节级编码补齐，两者互补不重复。
