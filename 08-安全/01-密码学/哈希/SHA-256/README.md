# SHA-256 — 安全哈希算法(Hash Function)

## 简介

- **SHA-256**(Secure Hash Algorithm 256-bit)由 NSA 设计、NIST 于 FIPS 180-4 标准化,是 SHA-2 家族的代表成员,输出 256 位(32 字节)摘要。
- 应用:数字签名(DSA/ECDSA/RSA-PSS)、HMAC 构造、TLS 1.2/1.3 握手 transcript、TLS Finished PRF、Bitcoin/Ethereum 工作量证明、区块链交易 ID。
- 关键概念:**抗碰撞性**(collision resistance)、**抗原像性**(preimage resistance)、**Merkle-Damgård 构造**、**压缩函数**、**雪崩效应**(avalanche effect)、**长度扩展攻击**(length extension)。
- 历史:1993 年 SHA-0 → 1995 年 SHA-1(160 位,FIPS 180-1)→ 2001 年 SHA-2(FIPS 180-2,新增 SHA-224/256/384/512)→ 2015 年 FIPS 180-4 微调 → 2023 年 NIST 退役 SHA-1,2024 年起 SHA-256 仍是主流安全哈希。

## 原理详解

### 1. 整体流程

```
输入消息 M(任意长度 < 2^64 bit)
   ↓
填充(Padding): M || 0x80 || 0x00... || len(M) big-endian 64-bit
   ↓
分块(64 字节 / 512 bit 一块)
   ↓
迭代压缩:对每块调用 SHA-256 压缩函数,更新 H[0..7]
   ↓
输出摘要 = H[0]||H[1]||...||H[7](32 字节,大端)
```

### 2. 初始哈希值 H(0)

由前 8 个素数(2,3,5,7,11,13,17,19)平方根的小数部分前 32 位构成:

```
H[0] = 0x6A09E667  H[1] = 0xBB67AE85  H[2] = 0x3C6EF372  H[3] = 0xA54FF53A
H[4] = 0x510E527F  H[5] = 0x9B05688C  H[6] = 0x1F83D9AB  H[7] = 0x5BE0CD19
```

### 3. 轮常量 K[64]

由前 64 个素数立方根的小数部分前 32 位构成(完整 64 项常量见源码)。

### 4. 消息调度 W[t]

对每块 16 字输入 M[0..15],扩展为 64 字:

```
W[t] = M[t]                                       for t = 0..15
W[t] = σ1(W[t-2]) + W[t-7] + σ0(W[t-15]) + W[t-16] for t = 16..63
```

- σ0(x) = ROTR^7(x) ⊕ ROTR^18(x) ⊕ SHR^3(x)
- σ1(x) = ROTR^17(x) ⊕ ROTR^19(x) ⊕ SHR^10(x)

### 5. 压缩函数(64 轮)

```
T1 = h + Σ1(e) + Ch(e,f,g) + K[t] + W[t]
T2 = Σ0(a) + Maj(a,b,c)
h = g; g = f; f = e; e = d + T1
d = c; c = b; b = a; a = T1 + T2
```

- Ch(x,y,z) = (x∧y) ⊕ (¬x∧z)  — 比特选择
- Maj(x,y,z) = (x∧y) ⊕ (x∧z) ⊕ (y∧z)  — 多数
- Σ0(x) = ROTR^2(x) ⊕ ROTR^13(x) ⊕ ROTR^22(x)
- Σ1(x) = ROTR^6(x) ⊕ ROTR^11(x) ⊕ ROTR^25(x)

每块结束: H[i] = H[i-1] + (a,b,c,d,e,f,g,h)(各分量按 32 位加)。

### 6. 填充规则

```
[原消息 M]
[1 位 = 0x80 后随]
[K 个 0 位,K 满足 (L + 1 + K) ≡ 448 mod 512]
[64 位 big-endian 原始消息长度 L(bit 数)]
```

总长度 = 512 的倍数。

## 对比/选型

| 算法 | 输出长度 | 块大小 | 安全性(2026) | 备注 |
|---|---|---|---|---|
| MD5 | 128 bit | 512 bit | **已攻破** | 2004 王小云碰撞攻击,不可用 |
| SHA-1 | 160 bit | 512 bit | **已攻破** | SHAttered 2017 实际碰撞,Google 退役 |
| SHA-256 | 256 bit | 512 bit | **安全** | 比特币/Bitcoin Cash 工作量证明 |
| SHA-384 | 384 bit | 1024 bit | 安全 | SHA-256 略变体 |
| SHA-512 | 512 bit | 1024 bit | 安全 | 64 位机上略快于 SHA-256 |
| SHA-3/Keccak | 224-512 bit | sponge | 安全 | FIPS 202,完全不同结构,抗长度扩展 |
| BLAKE2 | 256/512 bit | 64-byte 树状 | 安全 | 软件极快,Argon2/RFC 9106 用作 H |

## 环境准备

- 操作系统:Windows / Linux / macOS
- 语言版本:C99+ / Python 3.8+ / Go 1.20+
- 依赖:**无**(纯 stdlib;Go 走人工代码审查,本机无 Go 工具链)

## 运行方式

### C
```bash
gcc -O2 -Wall -Wextra -pedantic sha256.c -o sha256
./sha256
```

### Python
```bash
python3 sha256.py
```

### Go
```bash
go run sha256.go
```

## 关键代码片段

(以 Python 版为例,完整见源码)

```python
# 1. 填充
def pad(msg: bytes) -> bytes:
    L = len(msg) * 8                       # bit 数
    msg += b'\x80'                          # 追加 1 位
    while (len(msg) * 8) % 512 != 448:      # 对齐到 448 mod 512
        msg += b'\x00'
    return msg + L.to_bytes(8, 'big')       # 64-bit BE 长度

# 2. 64 轮压缩
for block in chunks(padded, 64):
    W = list(struct.unpack('>16I', block)) + [0] * 48
    for t in range(16, 64):
        W[t] = (ssig1(W[t-2]) + W[t-7] + ssig0(W[t-15]) + W[t-16]) & MASK
    a,b,c,d,e,f,g,h = H                    # 加载当前状态
    for t in range(64):
        T1 = (h + BSIG1(e) + Ch(e,f,g) + K[t] + W[t]) & MASK
        T2 = (BSIG0(a) + Maj(a,b,c)) & MASK
        h,g,f,e,d,c,b,a = g,f,e,(d+T1)&MASK, c,b,a,(T1+T2)&MASK
    H = [(x+y) & MASK for x,y in zip(H, (a,b,c,d,e,f,g,h))]
```

## 性能与边界

- 64 字节(单块)输入 = 1 轮填充 + 1 轮压缩 = 280 ns(Python 纯 stdlib),**未走 C 扩展**
- 64 MB 输入(2^20 块)≈ 25 s(Python 纯 stdlib);C 版 ~150 MB/s;Go runtime SHA-256 ~300 MB/s(AVX2)
- 长度上限 2^64 - 1 bit(=2^61 字节 ≈ 2 EB)
- 时间复杂度 O(n),空间 O(1)(仅 8×32-bit 状态)

## 注意事项与常见坑

- **大端序**:SHA-256 状态字、消息字、长度字段都是大端;实现错成小端 = 输出完全错误
- **模 2^32 加法**:T1、T2、H 累加都需 & 0xFFFFFFFF,**否则 Python 自动大整数 OK,C/Go 必须 mask**
- **长度扩展攻击(Lengthextension)**:SHA-256 基于 Merkle-Damgård,知道 H(M) 与 len(M) 可计算 H(M || pad || suffix),**不能直接用于 secret 认证**——必须用 HMAC(NIST SP 800-107 推荐)
- **填充末尾 0x80**:不是整字节 = 0x80,而是 1 bit,**ASCII 'a' = 0x61 不是 0x80**,否则长度少 1 bit
- **跨平台 int 溢出**:Python 自动大整数友好;C 32-bit `uint32_t` + 显式 mask;Go `uint32` 加法自动取模(Go 1.x 规范)
- **与 OpenSSL 对照**:`echo -n "abc" | openssl dgst -sha256` 应输出 `ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad`

## 参考资料(实际阅读过的权威来源)

- [RFC 6234 - US Secure Hash Algorithms (SHA and SHA-based HMAC and HKDF)](https://datatracker.ietf.org/doc/html/rfc6234) — 完整 SHA-256/384/512 算法、测试向量、参考 C 实现
- [FIPS 180-4 Secure Hash Standard (SHS)](https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.180-4.pdf) — NIST 官方规范,定义 SHA-256/384/512 + SHA-512/256
- [NIST SP 800-107 Rev. 1 - Recommendation for Applications Using Approved Hash Algorithms](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-107.pdf) — 联邦机构应用 SHA-256 的安全建议(含长度扩展攻击警告)
- [Wikipedia - SHA-2](https://en.wikipedia.org/wiki/SHA-2) — 历史背景、攻击现状、Bitcoin 挖矿双重 SHA-256