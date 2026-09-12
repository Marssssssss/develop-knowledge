# AES-GCM (Galois/Counter Mode)

## 简介

- AES-GCM 是 **认证加密 (AEAD)** 的工业标准:一次调用同时给出**机密性** (AES-CTR) 与**完整性/真实性** (GHASH tag),被 TLS 1.2/1.3、IPsec、QUIC、磁盘加密 (LUKS/dm-crypt)、WPA3 等广泛采用。
- 关键概念:
  - **AES-128/192/256**:FIPS 197 的对称分组密码,块大小恒为 128 bit。
  - **CTR (Counter) 模式**:把块密码转成流密码,块计数器序列加密后与明文异或,加解密完全相同。
  - **GHASH**:在 GF(2¹²⁸) 上的通用哈希,约简多项式 x¹²⁸+x⁷+x²+x+1;把 AAD、密文、长度打包成一条 128-bit 链。
  - **认证 tag**:默认 128 bit(可截到 32/64/96/104/112/120 bit,NIST SP 800-38D Appendix C 给出短 tag 安全约束)。
  - **IV/Nonce**:NIST 推荐 96 bit(12 字节)deterministic IV,J₀ = IV || 0x00000001;其它长度需先 GHASH 一次,代价更高。
- 历史:GCM 由 David McGrew 与 John Viega 设计,2004 年公开;NIST 于 2007 年 SP 800-38D 标准化,取代 CCM 成为高速网络协议首选。

## 原理详解

### AEAD 加密流程(NIST §7.1)

```
               96-bit IV
                  │
                  ▼
              J0 = IV || 0x00000001           §6.5 / §8.2.1
                  │
        ┌─────────┼─────────────┐
        │         │             │
        ▼         ▼             ▼
   H = E_K(0^128)  C = GCTR_K(J0+1, P)        §6.4 / §6.5
   (子密钥)       │   (CTR 加密)
                 ▼
   S = GHASH_H(A || C || lenA·8 || lenC·8)    §6.4
                 │
                 ▼
   T = GCTR_K(J0, S)[:t]                       §7.1 step 5
                 │
   输出 = C ‖ T
```

### GCTR (CTR 加密, NIST §6.5)

```
J0+1 → E_K → ks1 ─┬─ XOR P1 → C1
J0+2 → E_K → ks2 ─┬─ XOR P2 → C2
J0+3 → E_K → ks3 ─┬─ XOR P3 → C3  (最后一块可能 <16B,密钥流截断)
```

### GHASH (通用哈希, NIST §6.4)

把 AAD、CT 切 16B 块(末尾补零),再追加 64-bit lenA||lenC 形成一条链:

```
Y0 = 0
Yi = (Yi-1 XOR Xi) · H    (i = 1..m+n+1)
```

这里的 `·` 是 GF(2¹²⁸) 上的乘法,约简多项式 **x¹²⁸ + x⁷ + x² + x + 1**(`0xe1 << 120`)。

### 解密与篡改检测(NIST §7.2)

1. 用 AAD/IV/CT 重新计算 S、GCTR 期望 tag T';
2. **常量时间比较** T' 与收到的 T(Go stdlib / OpenSSL 都用 `CRYPTO_memcmp` 之类);
3. 不等 → 抛 `ErrAuth` / 返回错误,**且不返回任何明文**(防止 oracle 攻击);
4. 相等 → CTR 解密得明文。

## 对比 / 选型

| 模式 | 并行加密 | 并行认证 | IV 长度 | 性能(Intel AES-NI) | 抗误用 | 典型场景 |
| --- | --- | --- | --- | --- | --- | --- |
| **AES-GCM** | ✅ | ✅ (Karatsuba 加速) | 96 bit 推荐 | ~5–10 GB/s | IV 重用 → 立即失陷 | TLS、QUIC、磁盘加密 |
| AES-CCM | ❌ | ❌ | 56/64/72/80/96/112/128 | 较慢 | 较弱 | 802.11i、低功耗 IoT |
| AES-GCM-SIV | ✅ | ✅ | 任意 (nonce-misuse resistant) | 略慢于 GCM | 重用 IV 仍仅泄露重复 | 磁盘加密 (RFC 8452) |
| ChaCha20-Poly1305 | ✅ | ✅ | 96 bit | 无 AES-NI 时更快 | 同 GCM | 软件 TLS、WireGuard |
| AES-CBC + HMAC | ❌ (CBC 链式) | ✅ | IV 96 bit | 慢、易错 | padding oracle 风险 | 老协议 (TLS 1.2) |

> 生产首选:**AES-128-GCM (AES-NI 环境)** 或 **ChaCha20-Poly1305 (无 AES-NI)**。

## 环境准备

- 操作系统:Windows / Linux / macOS
- 语言版本:Python ≥ 3.8、Go ≥ 1.18
- 依赖:**无第三方依赖**(Python 用 stdlib;Go 用 `crypto/aes` + `crypto/cipher`)

## 运行方式

### Python(自实现 AES-128 + GCM)

```bash
cd python
python3 main.py
```

输出应包含:
- `FIPS197 B.1 加密/解密 ✓`
- `TC1..TC4 ct/tag ✓`
- `密文位翻转/AAD 改动/tag 改动 被检测 ✓`

### Go(标准库 crypto/cipher.NewGCM)

```bash
cd go
go run main.go
```

同样跑 4 个 NIST 测试用例 + 篡改检测。

## 关键代码片段

### GHASH 乘法(从 `python/gcm.py` 截取)

```python
def _ghash_mul(x, y):
    """GF(2^128) 上的乘法(NIST SP 800-38D §6.3),约简多项式 0xe1<<120。"""
    R = 0xe1 << 120
    z = 0
    v = y
    for _ in range(128):
        if x & 1:
            z ^= v
        x >>= 1
        v = (v >> 1) ^ R if v & 1 else v >> 1
    return z
```

### GCTR 加密(从 `python/gcm.py` 截取)

```python
def _gctr(cipher, icb, data):
    """NIST §6.5 GCTR_K:每块加 1 后 AES-encrypt,与明文块异或。"""
    out = bytearray()
    cb = icb
    n = (len(data) + 15) // 16
    for i in range(n - 1):
        ks = cipher.encrypt_block(cb.to_bytes(16, "big"))
        out += bytes(a ^ b for a, b in zip(ks, data[i*16:(i+1)*16]))
        cb = _inc32(cb)
    # 末块 <16B 时截断密钥流
    ks = cipher.encrypt_block(cb.to_bytes(16, "big"))
    last = data[(n - 1) * 16:]
    out += bytes(a ^ b for a, b in zip(ks[:len(last)], last))
    return bytes(out)
```

### AEAD 加密(从 `python/gcm.py` 截取)

```python
def encrypt(self, iv, plaintext, aad=b""):
    assert len(iv) == 12
    j0 = int.from_bytes(iv + b"\x00\x00\x00\x01", "big")   # §8.2.1
    ct  = _gctr(self._cipher, _inc32(j0), plaintext)        # §7.1 step 3
    s   = _ghash(self._h, aad, ct)                          # §7.1 step 4
    tag = _gctr(self._cipher, j0, s.to_bytes(16, "big"))[:16]  # §7.1 step 5
    return ct, tag
```

## 性能与边界

- **时间复杂度**:O(|P| + |A|),每 16 字节 1 次 AES + 1 次 GF(2¹²⁸) 乘法;Intel AES-NI 单核 ~5–10 GB/s,AES-NI + CLMUL GHASH 可达 ~10 GB/s。
- **空间复杂度**:O(1) 流式实现,只需 ~256B 状态。
- **IV 重用是灾难**:
  - 两次 `(K, IV)` 加密的密文 → 直接恢复明文异或(GCM 论文 Bleichenbacher 2016 证明);
  - NIST 强制要求 `1 个密钥下 IV 不重复`(SP 800-38D §8)。
- **tag 长度**:
  - **128 bit** 默认,安全度 ≈ 128 bit;
  - **32/64/96/104/112/120 bit** 短 tag 有理论攻击(NIST Appendix C:96 bit tag 在 2³² 消息后 forgery 概率 ~2⁻³²);
  - 短 tag 仅在性能/带宽极度敏感场景 (IoT) 使用,且必须限制消息数。
- **消息长度上限**:SP 800-38D §8.3 限制单密钥下 2³²–2⁶⁴ 次调用之间(以 GHASH 内 counter 长度为准)。

## 注意事项与常见坑

| 坑 | 现象 | 原因 | 规避 |
| --- | --- | --- | --- |
| IV 重用 | 立即失陷,认证 tag 也破 | CTR 计数器序列碰撞,GHASH 子密钥相同 | 用 `crypto/rand` + counter;TLS 1.3 隐式 12B IV |
| AAD 空但仍写入 `""` | 与完全不写 aad 等价 | `len(A)=0` 时长度块也是 0 | 严格统一 nil vs b"" 的语义 |
| Python `int.from_bytes` 大小端 | bit 反序 | GHASH 内部是「大端位序」(MSB 先行) | 自实现时**强制** big-endian,与 NIST 参考实现一致 |
| Go `aead.Seal` 把 tag 拼到密文末尾 | 调用方忘记切片 | stdlib 设计选择 | 取 `ct := out[:n]; tag := out[n:]` |
| 非 12B IV | 性能骤降 | 需先 GHASH 计算 J₀,多一次完整 hash | 强制 12B(96 bit)deterministic IV |
| tag 长度 < 16B | forgery 概率上升 | NIST Appendix C:96 bit tag 在 2³² 消息后不可忽略 | 默认 128 bit;短 tag 限制消息数 |

## 参考资料

- [NIST SP 800-38D — Recommendation for Block Cipher Modes of Operation: Galois/Counter Mode (GCM) and GMAC](https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-38d.pdf) — GCM/GMAC 规范,§6.4 GHASH、§7.1/§7.2 AE、Appendix B 测试向量(本 demo 直接采用 TC1–TC4)
- [NIST SP 800-38A — Block Cipher Modes (CTR 模式定义)](https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-38a.pdf) — CTR 模式基础
- [FIPS 197 — Advanced Encryption Standard](https://csrc.nist.gov/pubs/fips/197/final) — AES 算法的 S-box、密钥扩展、轮函数定义
- [McGrew & Viega, "The Galois/Counter Mode of Operation (GCM)", 2004](https://csrc.nist.gov/groups/ST/toolkit/BCM/documents/proposedmodes/gcm/gcm-revised-spec.pdf) — GCM 原论文
- [Go 标准库 crypto/cipher](https://pkg.go.dev/crypto/cipher) — `NewGCM` 实现的 API 文档(本 demo 的 Go 版本)
- [RFC 8452 — AES-GCM-SIV (nonce-misuse resistant 变体)](https://www.rfc-editor.org/rfc/rfc8452) — 当 IV 重用不可避免时的备选方案