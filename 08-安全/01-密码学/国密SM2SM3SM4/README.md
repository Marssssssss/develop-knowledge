# 国密 SM2 / SM3 / SM4 原理级实现

## 1. 简介

把国密三件套从常数表开始实现一遍：SM3 哈希、SM4 分组密码、SM2 椭圆曲线签名。
所有常数**逐字节核对过上游实现源码**（不是凭记忆写）：

| 常数 | 来源 |
| --- | --- |
| SM3 IV `7380166f …`、P0/P1/FF/GG/EXPAND、`T_j` 基值 | OpenSSL `crypto/sm3/sm3.c` + `sm3_local.h` |
| SM4 S 盒、FK、CK | GmSSL `src/sm4.c`（CK/FK 与 OpenSSL `crypto/sm4/sm4.c` 一致） |
| sm2p256v1 的 p / a / b / G / n | OpenSSL `crypto/ec/ec_curve.c` 的 `_EC_sm2p256v1` |
| SM2 签名公式与 Z_A 结构 | OpenSSL `crypto/sm2/sm2_sign.c` |

自检命中官方测试向量：SM3(`abc`)、SM3(512-bit)、SM4 单分组 + 首末轮密钥。

## 2. SM3：与 SHA-256 同宗但三处不同

结构同样是 8 个 32 位寄存器、64 轮、256 位输出，但：

| 维度 | SHA-256 | SM3 |
| --- | --- | --- |
| 消息扩展 | `W_j` 直接从 16 个词递推，每轮只用 `W_j` | 先递推 68 个 `W_j`，再算 **`W'_j = W_j ⊕ W_{j+4}`**，轮函数同时用 `W_j` 与 `W'_j` |
| 布尔函数 | 2 个（Ch / Maj） | 4 个：`FF`/`GG` 在 j<16 都是异或，j≥16 换成 `FF1=(X&Y)\|((X\|Y)&Z)`、`GG1=Z^(X&(Y^Z))` |
| 前馈 | `H_i += 工作变量`（**加法**） | `V^{i+1} = ABCDEFGH ⊕ V^i`（**异或**） |

第三条是这次实现里唯一踩到的坑：写成加法时 `SM3("abc")` 直接对不上，
OpenSSL `sm3.c` 末尾的 `ctx->A ^= A;` 才是权威口径。

轮函数（GM/T 0004 §4.3，与 OpenSSL 的 `RND` 宏逐条对应）：

```
SS1 = ROTL(ROTL(A,12) + E + ROTL(T_j, j), 7)
SS2 = SS1 ⊕ ROTL(A,12)
TT1 = FF_j(A,B,C) + D + SS2 + W'_j
TT2 = GG_j(E,F,G) + H + SS1 + W_j
D←C  C←ROTL(B,9)  B←A  A←TT1
H←G  G←ROTL(F,19) F←E  E←P0(TT2)
```

`T_j` 的基值前 16 轮是 `79cc4519`、其后是 `7a879d8a`，**每轮还要再左移 j 位**
——OpenSSL 干脆把移好的常数硬编码进源码（第 16 轮是 `0x9D8A7A87 = ROTL(0x7a879d8a,16)`）。

## 3. SM4：非平衡 Feistel + 两个不同的线性层

- 分组/密钥都是 128 位，32 轮；每轮只更新 **1/4 状态**（非平衡 Feistel，不是 SPN）；
- 最后一轮后做**反序变换** `R(X32,X33,X34,X35) = (X35,X34,X33,X32)`；
- 解密**不改算法**，只把 32 个轮密钥**反序**使用（这也是本 demo 断言的一条）。

最容易错的一点：**密钥扩展和轮函数用的不是同一个线性变换**。

```
轮函数 T    : L(B)  = B ⊕ (B<<<2)  ⊕ (B<<<10) ⊕ (B<<<18) ⊕ (B<<<24)
密钥扩展 T' : L'(B) = B ⊕ (B<<<13) ⊕ (B<<<23)
```

两者都先过同一个 S 盒。本次实现第一版把 `T'` 写成了 `T`，
结果是轮密钥首字对不上官方向量（`F12186F9`）而被断言抓住。

密钥扩展：`K_0..K_3 = MK ⊕ FK`，`rk_i = K_{i+4} = K_i ⊕ T'(K_{i+1}⊕K_{i+2}⊕K_{i+3}⊕CK_i)`。
`CK_i` 是 32 个固定常数（本 demo 表格与 GmSSL/OpenSSL 一致）。

## 4. SM2：ECDSA 的表亲，但摘要里掺了身份

签名（OpenSSL `sm2_sign.c` 的口径）：

```
Z_A = SM3(ENTL || ID || a || b || xG || yG || xA || yA)
e   = SM3(Z_A || M)
kG  = (x1, y1)
r   = (e + x1) mod n
s   = (1+d)^-1 · (k - r·d) mod n
```

验签：

```
t = (r + s) mod n          （t = 0 直接拒绝）
(x1', y1') = sG + t·P_A
接受 ⟺ (e + x1') mod n == r
```

与 ECDSA 的三点实质差异：

1. **r 里掺了消息摘要**。ECDSA 的 `r = x1` 与消息无关，SM2 的 `r = e + x1` 与消息绑定。
2. **s 的系数是 `(1+d)^-1` 而非 `k^-1`**，所以验签时把公钥乘 `t = r+s` 而不是 `r/s`。
3. **摘要前置了 Z_A**，把用户身份（ID）一起绑进签名。同一把私钥换 ID 会签出不同结果，
   验签双方 ID 不一致直接失败——本 demo 用 `ID="ALICE123"` 的用例钉住这条。

### 随机数 k 重用：SM2 的泄漏信号与 ECDSA 不同

ECDSA 复用 k 时两个签名的 `r` **相同**（一眼可辨）。SM2 因为 `r = e + x1`，
复用 k 时 `r` **不相等**，但差值恰好等于两条消息的摘要之差：

```
r2 - r1 = e2 - e1                                  （可观测，等于泄漏信号）
d = (s1 - s2) · (r2 - r1 - s1 + s2)^-1 mod n        （私钥直接可解）
```

本 demo 用两条订单消息复现了完整反解，断言 `recovered == d`。
所以「r 不相同就说明没复用 k」在国密语境下是**错误的安全直觉**。

另外 `d = n-1` 时 `(1+d) ≡ 0 (mod n)` 没有逆元，签名式退化——这是 SM2 特有的边界。

## 5. 文件与运行

```
python sm234.py                      # 24 条断言（SM3/SM4 官方向量 + SM2 签验与 k 重用反解）
go run sm234.go sm2_model.go         # 13 条断言
```

`sm2_model.go` 是 SM2 的曲线与点运算模型，与 `sm234.go` 同属 `package main`。

## 6. 关键代码

SM3 的 `W'_j`（与 SHA-256 的分水岭）：

```python
for j in range(16, 68):
    w.append(_p1(w[j-16] ^ w[j-9] ^ _rotl(w[j-3], 15)) ^ _rotl(w[j-13], 7) ^ w[j-6])
wp = [w[j] ^ w[j+4] for j in range(64)]     # 轮函数用 wp[j]，不是 w[j] 复用
```

SM4 的解密等价性（Go 版）：

```go
rk := sm4RoundKeys(mk)
for i, j := 0, 31; i < j; i, j = i+1, j-1 {
    rk[i], rk[j] = rk[j], rk[i]   // 只反序轮密钥，算法结构一行不改
}
```

SM2 验签的核心一行：

```python
pt = _pad_add(_mul(s), _mul(t, pub))     # t = (r+s) mod n，不是 r/s
return pt is not None and (e + pt[0]) % N == r
```

## 7. 性能与边界

- 三份实现都是**教学用的朴素实现**：点乘用二进制展开、模逆用扩展欧几里得，
  不做 NAF/窗口优化，也不做侧信道防护（时序不恒定）。**不可用于生产**。
- SM4 只实现了单分组 ECB 语义，没有 CBC/CTR/GCM 模式；真实使用必须配模式与填充。
- SM3 的 `W'_j` 需要 68 个词的空间，比 SHA-256 的 64 个略多。
- SM2 的参数选择：`a = p - 3` 让点加倍少一次乘法，这是国密与 NIST P-256 的相同取舍。

## 8. 注意事项与常见坑

1. **SM3 前馈写成加法**：结果完全错误且不易察觉，务必用官方向量回验。
2. **SM4 密钥扩展误用轮函数的 L**：轮密钥首字立刻对不上 `F12186F9`。
3. **忘记末轮反序变换 R**：密文四个字整体反序，解密能还原但与其它实现不兼容。
4. **SM2 的 Z_A 漏掉 ENTL 或用错 ID 长度**：ENTL 是**比特长度**（ID 字节数 × 8），且只占 2 字节。
5. **把 SM2 当 ECDSA 验签**：`t = r+s` 而不是 `r/s`，这是最常见的移植错误。
6. **认为「r 不同 ⇒ k 没复用」**：见 §4，SM2 的复用泄漏藏在差值里。
7. **S 盒来源**：国密 S 盒与 AES S 盒不同，不要从 AES 表推导或复用。

## 9. 参考资料

- GM/T 0004《SM3 密码杂凑算法》/ GM/T 0002《SM4 分组密码算法》/ GM/T 0003《SM2 椭圆曲线公钥密码算法》
- OpenSSL `crypto/sm3/sm3.c`、`crypto/sm3/sm3_local.h`、`crypto/sm4/sm4.c`、`crypto/sm2/sm2_sign.c`、`crypto/ec/ec_curve.c`
  （经 jsDelivr 镜像读取：`https://cdn.jsdelivr.net/gh/openssl/openssl@master/<path>`）
- GmSSL `src/sm4.c`（S 盒原始表）：`https://cdn.jsdelivr.net/gh/guanzhi/GmSSL@master/src/sm4.c`
- RFC 8998《TLS 1.3 with SM Cipher Suites》：`https://www.rfc-editor.org/rfc/rfc8998.txt`
