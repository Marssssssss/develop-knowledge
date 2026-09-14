# HMAC-SHA256 — 基于哈希的消息认证码(MAC)

## 简介

- **HMAC**(Keyed-Hashing for Message Authentication,RFC 2104)是一种基于**迭代式加密哈希函数**构造的消息认证码;HMAC-SHA256 即用 SHA-256 作为底层哈希。
- 解决"用共享密钥认证消息完整性"的经典方案 —— 验证两点:**消息未被篡改** + **来自持有密钥的对端**。
- 关键概念:**MAC**(Message Authentication Code)、**密钥哈希构造**、**内填充 ipad(0x36×B)**、**外填充 opad(0x5C×B)**、**长度扩展攻击免疫**、**PRF**(伪随机函数)。
- 历史:HMAC 由 Mihir Bellare、Ran Canetti、Hugo Krawczyk 于 1996 年提出,被 IPSec、TLS 1.2(在 TLS 1.3 中 PRF 改 HKDF)、SSHv2、JSON Web Signature(H-S256)、AWS SigV4 等协议广泛采用。

## 原理详解

### 1. 算法公式(RFC 2104 §2)

```
HMAC(K, m) = H( (K ⊕ opad) ‖ H( (K ⊕ ipad) ‖ m ) )
```

- `H`:底层的迭代式加密哈希函数(SHA-256 → 输出 32 字节)
- `K`:共享密钥
- `m`:消息
- `ipad`:内填充,字节 `0x36` 重复 B 次
- `opad`:外填充,字节 `0x5C` 重复 B 次
- `B`:底层 H 的**块字节长度**,SHA-256 = **64 字节**
- `L`:H 的输出字节长度,SHA-256 = **32 字节**

### 2. 密钥准备与填充

| 条件 | 处理 |
|------|------|
| `K` 长度 < B(64)字节 | 末尾补 `0x00` 至 B 字节 |
| `K` 长度 = B | 直接使用 |
| `K` 长度 > B | `K ← H(K)`,取 L 字节再加 0x00 至 B 字节 |

> RFC 2104 推荐 K 长度 ≥ L(SHA-256 即 ≥ 32 字节),"小于 L 字节 strongly discouraged"。

### 3. 七步执行流程(RFC 2104 §2 原文)

```
(1) append zeros to the end of K to create a B byte string
    在 K 末尾补 0 至 B 字节
(2) XOR the B byte string with ipad (0x36 × B)
    与 ipad 异或
(3) append the stream of data 'text' to the B byte string from step (2)
    追加消息 m
(4) apply H to the stream generated in step (3)
    计算内层 H
(5) XOR the B byte string from step (1) with opad (0x5C × B)
    与 opad 异或
(6) append the H result from step (4) to the B byte string from step (5)
    追加内层 H 输出
(7) apply H to the stream generated in step (6) and output the result
    计算外层 H 即最终 MAC
```

### 4. 关键常数(SHA-256)

| 量 | 值 |
|----|----|
| 块字节长度 B | 64 |
| 输出字节长度 L | 32 |
| ipad | `b'\x36' * 64` |
| opad | `b'\x5C' * 64` |

### 5. 优化:预计算中间状态

可一次性预计算 `(K ⊕ ipad)` 和 `(K ⊕ opad)` 经 H 压缩函数处理后的内部状态,**省去两次 B 字节块处理**。短消息时尤其重要。OpenSSL/Go stdlib 都采用此优化。

### 6. 安全性质

- **构造与具体 H 解耦(hash-agnostic)** —— 替换 SHA-256 为 SHA-3/BLAKE2 不影响协议层
- **不修改底层 H 代码** —— 易实现 / 易审查
- **抗原像/抗碰撞 + 密钥恢复** 困难:NIST SP 800-107 评估 ≥ 256-bit 安全强度(K ≥ 256 bit)
- **抗长度扩展攻击** —— 直接 H(secret ‖ message) 易被攻击,HMAC 通过两层嵌套 + 已知长度 nonce 免疫
- **生日攻击边界**:`2^(L/2)` ≈ `2^128` 次 MAC 查询,远不可行

## 对比/选型

| 方案 | 速度 | 密钥管理 | 长度扩展免疫 | 备注 |
|------|------|---------|-------------|------|
| `H(secret ‖ m)` | 快 | 易泄露 | **否** | 不应直接使用 |
| HMAC-SHA256 | 中 | 安全 | **是** | 工业标准 |
| HMAC-SHA512 | 中 | 安全 | 是 | 64-bit 机上略快 |
| CMAC-AES | 快 | 安全 | 是(结构不同) | NIST SP 800-38B |
| GMAC | 极快 | 安全 | 是 | 需 nonce 唯一 |
| Poly1305 | 极快 | 一次性 key | 是 | 常与 ChaCha20 配对 |
| BLAKE2 keyed mode | 极快 | 安全 | 是 | RFC 7693 |

## 环境准备

- 操作系统:Windows / Linux / macOS
- 语言版本:C99+ / Python 3.8+ / Go 1.20+
- 依赖:**无**(纯 stdlib)

## 运行方式

### C
```bash
gcc -O2 -Wall -Wextra -pedantic hmac.c -o hmac
./hmac
```

### Python
```bash
python3 hmac.py
```

### Go
```bash
go run hmac.go
```

## 关键代码片段

(以 Python 版为例,完整见源码)

```python
def hmac_sha256(key: bytes, msg: bytes) -> bytes:
    # 1) Key prep
    if len(key) > B:
        key = sha256(key)
    key = key.ljust(B, b'\x00')              # pad to B bytes
    # 2) ipad / opad
    ipad = bytes(k ^ 0x36 for k in key)
    opad = bytes(k ^ 0x5C for k in key)
    # 3) HMAC(K, m) = H(opad || H(ipad || m))
    inner = sha256(ipad + msg)
    return sha256(opad + inner)
```

## 性能与边界

- 单次 HMAC ≈ 2 次 SHA-256 ≈ 1.5 μs(Python 纯 stdlib)
- 推荐密钥长度 ≥ 32 字节(256 bit)
- 输出长度固定 32 字节,**不可截断**(NIST SP 800-107)
- 常数时间比较 `hmac.compare_digest`(Python) / `crypto/hmac.Equal`(Go) / `CRYPTO_memcmp`(OpenSSL)防时序攻击

## 注意事项与常见坑

- **常时比较**:验证 HMAC 必须用 `compare_digest` 之类常数时间比较,**禁止** `==` / `memcmp`(时序侧信道)
- **长度扩展免疫**:HMAC-SHA256 不像 `H(secret ‖ m)` 那样可被长度扩展攻击(Lengthextension),因两层 H 嵌套 + 已知 K 长度
- **密钥重用**:同一密钥不应同时用于多个协议(如同时 AES-CBC + HMAC),否则可能引入密钥-密文交互攻击
- **空消息 / 空密钥**:空密钥 = 0x00×B;空消息 = 内层 H 只算 ipad + final pad,RFC 4231 test case 1 验证
- **跨语言对照**:`hmac(key, msg, sha256).hexdigest()` Python、Go `hmac.New(sha256.New, key, sha256.New)`、C OpenSSL `HMAC()` API 应一致

## 参考资料(实际阅读过的权威来源)

- [RFC 2104 - HMAC: Keyed-Hashing for Message Authentication](https://datatracker.ietf.org/doc/html/rfc2104) — HMAC 完整规范:七步流程、ipad/opad、安全分析、截断建议
- [RFC 4231 - Identifiers and Test Vectors for HMAC-SHA-224/256/384/512 and HMAC-RIPEMD-128/160](https://datatracker.ietf.org/doc/html/rfc4231) — HMAC-SHA256/384/512 测试向量(Case 1-7)
- [NIST SP 800-107 Rev.1 - Recommendation for Applications Using Approved Hash Algorithms](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-107.pdf) — HMAC 在联邦机构的安全使用建议
- [Wikipedia - HMAC](https://en.wikipedia.org/wiki/HMAC) — 历史背景、NIST/IETF 标准化历程、安全性归纳