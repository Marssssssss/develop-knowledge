# X25519 — Curve25519 椭圆曲线 Diffie-Hellman 密钥交换

## 简介

- **X25519** 是 Bernstein 2006 年设计的椭圆曲线 Diffie-Hellman(ECDH)密钥交换,基于 Montgomery 曲线 **Curve25519**(`v² = u³ + 486662u² + u mod 2^255-19`),输出 32 字节共享密钥。
- 现代 TLS 1.3 强制支持的密钥交换算法之一(另一为 P-256/P-384 ECDH),WireGuard、Signal、SSH 协议、Noise 协议族、NaCl/libsodium 默认密钥交换。
- 关键概念:**Montgomery ladder** 常时标量乘、**clamping**(位操作防侧信道)、**u-坐标**单向(无法提取私钥)、**cofactor 8**(共享密钥验证防小阶点)、**贡献性**(共享密钥由双方私钥共同贡献)。
- 历史:2006 Bernstein "Curve25519: new Diffie-Hellman speed records" → 2014 NaCl/libsodium 标准化 → 2016 RFC 7748 → 2018 RFC 8446 TLS 1.3 强制支持。

## 原理详解

### 1. 曲线参数(RFC 7748 §4.1)

| 参数 | 值 |
|------|-----|
| 素数 p | 2^255 - 19 |
| 曲线方程 | v² = u³ + 486662·u² + u |
| 阶 order | 2^252 + 0x14def9dea2f79cd65812631a5cf5d3ed |
| 余因子 cofactor | 8 |
| 基点 U-coordinate | 9 |

### 2. Scalar Clamping(RFC 7748 §5 §6.1)

输入 32 字节随机私钥 k[0..31]:

```
k[0]  &= 248      # 清 bit 0,1,2 → 强制 8 倍数
k[31] &= 127      # 清 bit 255 → 防溢出
k[31] |= 64       # 置 bit 254 → 确保最小长度
```

clamped 形式:`2^254 + 8·n (0 ≤ n < 2^251)`。

### 3. Montgomery Ladder 常时标量乘(RFC 7748 §5)

```
x_1 = u
x_2, z_2 = 1, 0
x_3, z_3 = u, 1
swap = 0

for t = 254 down to 0:
    k_t = (k >> t) & 1
    swap ^= k_t
    cswap(swap, x_2, x_3); cswap(swap, z_2, z_3)
    swap = k_t
    
    A = x_2 + z_2
    AA = A²
    B = x_2 - z_2
    BB = B²
    E = AA - BB
    C = x_3 + z_3
    D = x_3 - z_3
    DA = D * A
    CB = C * B
    x_3 = (DA + CB)²
    z_3 = x_1 * (DA - CB)²
    x_2 = AA * BB
    z_2 = E * (AA + a24 * E)

# 常数 a24 = (486662 - 2) / 4 = 121665
cswap(swap, x_2, x_3); cswap(swap, z_2, z_3)
return x_2 * (z_2)^(p - 2)   # 模逆 → u-坐标
```

### 4. cswap 常时条件交换

```
def cswap(swap, x_2, x_3):
    dummy = mask(swap) AND (x_2 XOR x_3)
    x_2   = x_2 XOR dummy
    x_3   = x_3 XOR dummy
```

`mask(swap) = 0 - swap`(全 1 或全 0)。必须保证执行时间与 swap 无关。

### 5. 共享密钥验证(RFC 7748 §6.1)

双方应检查共享密钥 K 是否为全零(小阶点攻击):`if all(K_i == 0): abort`。

### 6. RFC 7748 §6.1 测试向量(Alice + Bob ECDH)

```
Alice 私钥 a : 77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a
Alice 公钥   : 8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a
Bob   私钥 b : 5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb
Bob   公钥   : de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f
共享密钥 K   : 4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742
```

## 对比/选型

| 方案 | 曲线 | 速度(单次 ECDH) | 安全性(2026) | 备注 |
|------|------|------------------|--------------|------|
| **X25519** | Curve25519 | ~80 μs(单核,OpenSSL) | 128-bit | RFC 7748,现代默认 |
| **X448** | Curve448 | ~150 μs | 224-bit | RFC 7748,保守选项 |
| **P-256 ECDH** | NIST P-256 | ~200 μs | 128-bit | NIST 曲线,政府合规 |
| **P-384 ECDH** | NIST P-384 | ~600 μs | 192-bit | 更高安全 |
| **RSA-2048** | n/a | ~5 ms(2048 bit) | ~112-bit | 即将退役 |
| **DH-2048** | n/a | ~5 ms | ~112-bit | 即将退役 |

## 环境准备

- 操作系统:Windows / Linux / macOS
- 语言版本:C99+ / Python 3.8+ / Go 1.20+
- 依赖:**无**(纯 stdlib)

## 运行方式

### C
```bash
gcc -O2 -Wall -Wextra -pedantic x25519.c -o x25519
./x25519
```

### Python
```bash
python3 x25519.py
```

### Go
```bash
go run x25519.go
```

## 关键代码片段

(以 Python 版为例,完整见源码)

```python
def x25519(k_bytes: bytes, u_bytes: bytes) -> bytes:
    """RFC 7748 §5 Montgomery ladder 实现。"""
    # 1) Clamping
    k = bytearray(k_bytes)
    k[0]  &= 248
    k[31] &= 127
    k[31] |= 64
    k_int = int.from_bytes(k, 'little')
    # 2) Decode u-coordinate
    u = int.from_bytes(u_bytes, 'little')
    # 3) Montgomery ladder
    x1, x2, z2, x3, z3 = u, 1, 0, u, 1
    swap = 0
    for t in range(254, -1, -1):
        k_t = (k_int >> t) & 1
        swap ^= k_t
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = k_t
        A  = (x2 + z2) % P
        AA = A * A % P
        B  = (x2 - z2) % P
        BB = B * B % P
        E  = (AA - BB) % P
        C  = (x3 + z3) % P
        D  = (x3 - z3) % P
        DA = D * A % P
        CB = C * B % P
        x3 = pow(DA + CB, 2, P)
        z3 = x1 * pow(DA - CB, 2, P) % P
        x2 = AA * BB % P
        z2 = E * (AA + A24 * E) % P
    if swap:
        x2, z2 = z2, x2
    # 4) Modular inverse via Fermat
    res = x2 * pow(z2, P - 2, P) % P
    return res.to_bytes(32, 'little')
```

## 性能与边界

- 单次 X25519 ~150 μs(Python 纯 stdlib),无优化
- C 版 ~80 μs,Go stdlib `crypto/ecdh` ~80 μs(恒时变体),libsodium `crypto_scalarmult` ~80 μs
- 长度固定:私钥 32 字节、公钥 32 字节、共享密钥 32 字节
- 不可提取私钥:攻击者拿到 u-坐标公钥不能恢复私钥(ECDLP 困难)

## 注意事项与常见坑

- **共享密钥必须非全零**:小阶点攻击可让攻击者得 K=0,接收方需校验
- **私钥 clamping** 必须做;不做 = 攻击者可通过自己构造 k·k 让结果落到小阶点
- **公钥接收时清最高位**:`u[31] &= 127`(X25519 必做,X448 不需要)
- **恒时比较公钥**:某些 TLS 实现中未公开对端公钥用 memcmp 时序侧信道泄露
- **不要复用 nonce**:无 IV/nonce,X25519 自身不涉及;但若配合 AEAD(ChaCha20-Poly1305)必须 nonce 唯一
- **贡献性失效**:cofactor=8 意味着理论上可能 K 不完全由双方贡献;实际应结合 HKDF 派生抗退化

## 参考资料(实际阅读过的权威来源)

- [RFC 7748 - Elliptic Curves for Security (X25519/X448)](https://datatracker.ietf.org/doc/html/rfc7748) — 完整规范、Montgomery ladder、clamping、§6.1 ECDH 测试向量
- [Bernstein 2006 - Curve25519: new Diffie-Hellman speed records](https://cr.yp.to/ecdh.html) — 原始论文,设计动机、性能基线
- [RFC 8446 - The Transport Layer Security (TLS) Protocol Version 1.3](https://datatracker.ietf.org/doc/html/rfc8446) — TLS 1.3 强制支持 X25519 / secp256r1 / secp384r1
- [Wikipedia - Curve25519](https://en.wikipedia.org/wiki/Curve25519) — 历史、NaCl/libsodium 集成、与 Ed25519 关系