# RSA-PSS (Probabilistic Signature Scheme)

## 简介

- **RSA-PSS**(RSA Probabilistic Signature Scheme)是 PKCS#1 v2.x(RFC 8017 §8.1)推荐的 RSA 签名方案,通过引入**随机盐**使签名具有概率性,从理论上证明了安全性可归约到「RSA 求逆困难性」(Bellare-Rogaway 1996),比古老的 RSASSA-PKCS1-v1_5 更安全。
- 关键概念:
  - **RSA 模数 n**:两个大素数 p, q 的乘积,通常 2048/3072/4096 bit。
  - **公钥指数 e**:通常 65537(= 2¹⁶+1),验证时计算 s^e mod n。
  - **私钥指数 d**:e 在 φ(n)=(p−1)(q−1) 下的逆元,签名时计算 m^d mod n。
  - **EMSA-PSS 编码**:把消息 hash + 随机盐拼成「填充 + 0x01 + 盐」格式,再用 MGF1 mask。
  - **MGF1**:基于 hash 的掩码生成器(RFC 8017 Appendix B.2.1),用迭代 `Hash(seed || counter)` 拼出任意长度掩码。
- 历史:Bellare & Rogaway 在 1996 年 EUROCRYPT 提出 PSS;RSA Labs 2002 年吸收进 PKCS#1 v2.1;2016 年 IETF 出版 RFC 8017(v2.2)正式标准化。
- 实际应用:TLS 1.3 的 RSA 模式(`rsa_pss_rsae_sha256` 等)、JWT(`alg=PS256`)、X.509 证书、Code Signing。

## 原理详解

### RSASSA-PSS 整体流程(RFC 8017 §8.1.1)

```
            M (消息)
              │
              ▼
        mHash = Hash(M)
              │
              ▼
        M' = 0x00..0x00 || mHash || salt         (8B 零 + hLen + sLen)
              │
              ▼
        H = Hash(M')                              §9.1.1 step 5
              │
              ▼
        DB = PS (0x00..0x00) || 0x01 || salt
              │
              ▼
        dbMask = MGF1(H, emLen − hLen − 1)         §9.1.1 step 7
              │
              ▼
        maskedDB = DB ⊕ dbMask
              │  (顶部 8·emLen − emBits 位清零)
              ▼
        EM = maskedDB || H || 0xbc
              │
              ▼
        m = OS2IP(EM)
              │
              ▼
        s = RSASP1(K, m) = m^d mod n               §5.2.1
              │
              ▼
        S = I2OSP(s, k)
```

### MGF1(RFC 8017 Appendix B.2.1)

```
T = "" || for counter = 0..⌈length/hLen⌉-1:
        T = T || Hash(seed || I2OSP(counter, 4))
return T[:length]
```

### RSASP1 / RSAVP1(RFC 8017 §5.2)

- **RSASP1(K, m)**:`m^d mod n`,仅私钥持有者可计算。
- **RSAVP1((n,e), s)**:`s^e mod n`,任何人可计算。
- 安全性依赖**大数分解困难**:对 n=2048 bit,BKZ + lattice 攻击理论 ~2¹⁰⁰ 量级操作,经典攻击仍不可行。

### 解码 / 验签(RFC 8017 §8.1.2 / §9.1.2)

1. 长度检查:`|S| == k`(签名长度等于 modulus 字节长度),否则直接 FAIL。
2. `s = OS2IP(S)`,`m = RSAVP1((n,e), s)`,`EM = I2OSP(m, emLen)`。
3. `EMSA-PSS-VERIFY(M, EM, emBits)`:
   - 检查尾部字节 = 0xbc;
   - 检查高位为 0(强制 `emBits ≤ 8·emLen`);
   - `dbMask = MGF1(H, emLen − hLen − 1)`,`DB = maskedDB ⊕ dbMask`;
   - `PS` 必须全是 `0x00`,然后 0x01;
   - 取 `salt`,重算 `H' = Hash(0x00..00 || mHash || salt)`;
   - **常数时间比较** `H' == H`。

## 对比 / 选型

| 签名方案 | 类型 | 抗选择消息攻击 | 签名长度 | 公钥长度 | 私钥性能 | 验证性能 | 现状 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **RSA-PSS** | 概率 (salt) | ✅ 严格证明 | k (= modulus) | k | 慢 (RSA-priv) | 慢 (RSA-pub) | 推荐 |
| RSASSA-PKCS1-v1_5 | 确定性 | ⚠️ 历史攻击 (Bleichenbacher) | k | k | 同上 | 同上 | 兼容老协议,TLS 1.3 已移除 |
| RSA-FDH (Full Domain Hash) | 概率 | ✅ 证明但较弱 | k | k | 同上 | 同上 | 学术,无广泛部署 |
| ECDSA (P-256) | 确定性 (k 随机) | ⚠️ nonce 重复失陷 | 64B | 32B | 快 (scalar mul) | 快 | 主流替代 |
| Ed25519 | 确定性 (无 nonce) | ✅ | 64B | 32B | **极快** | **极快** | 现代首选 |
| SM2 (中国商用) | 概率 | ✅ | 64B | 32B | 中 | 中 | 国密合规场景 |

> 生产建议:**Ed25519 / ECDSA-P256 / RSA-PSS-2048+(不重用)**;**避免** PKCS#1 v1.5(在 TLS 1.3 中已被剔除)。

## 环境准备

- 操作系统:Windows / Linux / macOS
- 语言版本:Python ≥ 3.8、Go ≥ 1.18
- 依赖:**无第三方依赖**(Python 用 stdlib `hashlib`/`secrets`/`math.gcd`;Go 用 `crypto/rsa` + `crypto/sha256`)

## 运行方式

### Python(自实现 RSA 密钥生成 + EMSA-PSS + 签名验签)

```bash
cd python
python3 main.py
```

输出应包含:
- 「正常验签通过 ✓」
- 「篡改消息/签名被检测 ✓」
- 「两次签名 salt 不同(PSS 概率性质) ✓」

### Go(标准库 crypto/rsa + SignPSS / VerifyPSS)

```bash
cd go
go run main.go
```

同样跑 4 个验证项 + 跨语言互操作骨架。

## 关键代码片段

### EMSA-PSS 编码(从 `python/main.py` 截取)

```python
def emsa_pss_encode(msg, em_bits, params):
    em_len = (em_bits + 7) // 8
    h = params.hash_func(msg).digest()
    salt = secrets.token_bytes(params.s_len)
    m_prime = b"\x00" * 8 + h + salt
    h2 = params.hash_func(m_prime).digest()
    ps_len = em_len - params.s_len - params.h_len - 2
    db = b"\x00" * ps_len + b"\x01" + salt
    db_mask = mgf1(h2, em_len - params.h_len - 1, params.mgf1_hash)
    masked_db = bytes(a ^ b for a, b in zip(db, db_mask))
    # 顶部 (8*em_len - em_bits) 位清零(RFC 8017 §9.1.1 step 9)
    bits_to_zero = 8 * em_len - em_bits
    if bits_to_zero > 0:
        masked_db = bytes([masked_db[0] & ((1 << (8 - bits_to_zero)) - 1)]) + masked_db[1:]
    return masked_db + h2 + bytes([params.trailer])  # trailer = 0xbc
```

### RSA 私钥生成(简化 Miller-Rabin, `python/main.py` 截取)

```python
def _is_prime(n, rounds=16):
    """确定性 Miller-Rabin,16 轮误判率 < 4^-16。"""
    if n < 2: return False
    d, r = n - 1, 0
    while d % 2 == 0: d //= 2; r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1): continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1: break
        else:
            return False
    return True
```

### Go:SignPSS(opts, RFC 8017 默认参数)

```go
opts := &rsa.PSSOptions{
    SaltLength: rsa.PSSSaltLengthAuto, // = hLen
    Hash:       crypto.SHA256,
}
sig, err := rsa.SignPSS(rand.Reader, priv, crypto.SHA256, hashed[:], opts)
```

## 性能与边界

- **签名时间**:RSA-2048 在单核 ~50–100 us,Python 实现比 Go 慢 10–50 倍(语言层面 + bignum 实现差异);**生产请用 OpenSSL/libcrypto**。
- **验签时间**:比签名快 ~3–5 倍(只需一次 `s^e mod n`,e=65537 = 17 次平方+1 乘)。
- **签名长度 = modulus 字节长度**:RSA-2048 → 256 B,RSA-4096 → 512 B。**与消息长度无关**(对比 ECDSA/Ed25519 的 64B 固定)。
- **模数位数**:
  - **RSA-1024** ⚠️ 已不安全,NIST SP 800-131A (2024) 标 deprecated;
  - **RSA-2048** 当前推荐,128-bit 安全级别;
  - **RSA-3072** 2030 年后建议;
  - **RSA-4096** 高敏感场景(CA root)。
- **盐长度 sLen**:RFC 8017 §9.1 推荐 `sLen = hLen`(32 字节 SHA-256);可降到 0 但损失 salt 抗性。
- **emBits**:必须 ≤ modulus bit 数 − 1(留最高位用于 `OS2IP` 时不被识别为负数);RFC 8017 推荐 `emBits = modBits − 1`。

## 注意事项与常见坑

| 坑 | 现象 | 原因 | 规避 |
| --- | --- | --- | --- |
| 公钥指数 e=3 | Bleichenbacher 立方根攻击 | 太小,e=3 时 m^e 可能 < n | **强制 e = 65537**(FIPS 186-5) |
| 用 RSA-1024 | 公开可分解 | Lenstra 2017 演示 1024-bit 在学术机构可破 | 用 2048+ |
| salt 用 deterministic (counter) | 失去概率性质 | 两次同 msg 签名相同 | 用 `secrets.token_bytes` 或硬件 RNG |
| 复用同一对 (n, e) 的 salt | 攻击者拿到两份即可恢复私钥 | 攻击者联立方程求 d | 严格保证每次签名 salt 独立 |
| `emBits = modBits` | OS2IP 解释成负数 | 高位为 1 时变负 | **emBits = modBits − 1** |
| Python RSA 私钥导入 DER | `rsa` 第三方库易踩坑 | 第三方库格式多变 | **用 stdlib `cryptography` 或 PKCS#8/PKIX 序列化** |

## 参考资料

- [RFC 8017 — PKCS #1: RSA Cryptography Specifications Version 2.2](https://www.rfc-editor.org/rfc/rfc8017) — RSA-PSS / RSASSA-PKCS1-v1_5 / MGF1 完整规范(本 demo 直接参考 §8.1、§9.1、Appendix B.2.1)
- [FIPS 186-5 — Digital Signature Standard (DSS)](https://csrc.nist.gov/pubs/fips/186/final) — 规定 RSA modulus ≥ 2048、e = 65537 的合规要求
- [Bellare & Rogaway, "The Exact Security of Digital Signatures — How to Sign with RSA and Rabin", 1996 EUROCRYPT](https://cseweb.ucsd.edu/~mihir/papers/exact-rsa.html) — RSA-PSS 概率签名方案的原始安全证明
- [NIST SP 800-131A Rev.2 — Transitioning the Use of Cryptographic Algorithms](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-131Ar2.pdf) — RSA-1024 deprecation 时间表
- [Go 标准库 crypto/rsa](https://pkg.go.dev/crypto/rsa) — `SignPSS` / `VerifyPSS` API(本 demo 的 Go 版本)
- [RFC 8017 §B.2.1 MGF1](https://www.rfc-editor.org/rfc/rfc8017#appendix-B.2.1) — MGF1 完整定义
- [Bleichenbacher, "Forging RSA Signatures with Probabilistic Signature Scheme", 2004](https://www.bioinf.org/jmk/sec/bleichenbacher.html) — PKCS#1 v1.5 历史攻击的经典参考