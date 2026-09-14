# ChaCha20-Poly1305 — AEAD 流加密 + 一次性认证

## 简介

- **ChaCha20-Poly1305** 是 RFC 8439 定义的 AEAD(Authenticated Encryption with Associated Data)构造,结合 Bernstein 2008 年设计的 **ChaCha20 流密码**和 **Poly1305 一次性 MAC**。
- TLS 1.3 强制支持的 AEAD 之一(另一为 AES-GCM),WireGuard、SIP/TLS over UDP、QUIC、SSH 协议、Age 加密工具默认算法。
- 关键概念:**流密码**(stream cipher)、**Quarter Round**、**20 轮双字轮**(10 column + 10 diagonal)、**认证加密**(AEAD)、**Poly1305 素数 2^130-5**、**clamp r 清除特定位**、**Nonce 唯一性**。
- 历史:2008 Bernstein ChaCha20 → 2013 RFC 7539(原版)→ 2017 DJB 修正 nonce 处理 → 2018 RFC 8439 修订 → 2021 TLS 1.3 RFC 8446 强制支持。

## 原理详解

### 1. ChaCha20 Quarter Round(QR)

定义在 32 位无符号字上:

```
a += b;  d ^= a;  d <<<= 16;
c += d;  b ^= c;  b <<<= 12;
a += b;  d ^= a;  d <<<= 8;
c += d;  b ^= c;  b <<<= 7;
```

操作:`+=` 模 2^32 加法、`^=` XOR、`<<<=` 循环左移。

### 2. ChaCha20 状态矩阵(4×4 × 32-bit 字)

```
 0   1   2   3
 4   5   6   7
 8   9  10  11
12  13  14  15
```

ChaCha20 初始化:

```
 cccccccc  cccccccc  cccccccc  cccccccc     # 常数 "expand 32-byte k"
 kkkkkkkk  kkkkkkkk  kkkkkkkk  kkkkkkkk     # 密钥 256 bit
 kkkkkkkk  kkkkkkkk  kkkkkkkk  kkkkkkkk
 bbbbbbbb  nnnnnnnn  nnnnnnnn  nnnnnnnn     # 32-bit block counter + 96-bit nonce
```

### 3. 20 轮块函数

每轮 = 4 个 quarter-round(80 QR):

```
column round:
  QR(0,  4,  8, 12)
  QR(1,  5,  9, 13)
  QR(2,  6, 10, 14)
  QR(3,  7, 11, 15)
diagonal round:
  QR(0,  5, 10, 15)
  QR(1,  6, 11, 12)
  QR(2,  7,  8, 13)
  QR(3,  4,  9, 14)
```

10 个 column + 10 个 diagonal = 80 QR。state += initial(模 2^32) → serialize 小端 → 64 字节 keystream。

### 4. Poly1305(r = 128-bit,s = 128-bit 一次性 key)

- 素数 P = 2^130 - 5 = 0x3fffffffffffffffffffffffffffffffb
- 累加:Acc = 0
- 对每 16 字节块 n(末尾追加 0x01):`Acc = (Acc + n); Acc = (r * Acc) mod P`
- 短块同样追加 0x01 字节(最低有效字节)
- 输出:`tag = (Acc + s) mod 2^128`
- **Clamp r(关键!)**:`r &= 0x0ffffffc0ffffffc0ffffffc0fffffff`(清 r 各 limb 高 4 位 + 低 2 位)

### 5. AEAD 构造(RFC 8439 §2.8)

```
加密流程:
1. poly1305_key = chacha20_block(key, counter=0, nonce)[:32]
2. r = clamp(poly1305_key[0..15])
3. s = poly1305_key[16..31]
4. ciphertext = chacha20_encrypt(key, counter=1, nonce, plaintext)
5. mac_data = AAD || pad16(AAD) || ciphertext || pad16(ciphertext) ||
              len(AAD) le 8B || len(ciphertext) le 8B
6. tag = poly1305(mac_data, r, s)
7. 输出:ciphertext + tag(16 字节)
```

**Nonce 不可重用** —— 同一 key + nonce 加密两消息暴露 XOR。

## 对比/选型

| 算法 | 软件 MB/s | 硬件加速 | 备注 |
|------|-----------|----------|------|
| **ChaCha20-Poly1305** | ~500 | 部分 ARM (Armv8) | 移动端首选 |
| AES-128-GCM | ~150(无 NI) / ~900(有 NI) | 主流 x86 AES-NI | 桌面/服务器首选 |
| AES-256-GCM | ~130(无 NI) / ~700(有 NI) | 同上 | 政府/合规 |
| AES-128-CCM | ~100 | AES-NI | TLS 1.2 fallback |

## 环境准备

- 操作系统:Windows / Linux / macOS
- 语言版本:C99+ / Python 3.8+ / Go 1.20+
- 依赖:**无**(纯 stdlib)

## 运行方式

### C
```bash
gcc -O2 -Wall -Wextra -pedantic chacha20_poly1305.c -o chacha20_poly1305
./chacha20_poly1305
```

### Python
```bash
python3 chacha20_poly1305.py
```

### Go
```bash
go run chacha20_poly1305.go
```

## 关键代码片段

(以 Python 版为例,完整见源码)

```python
def chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    """20 轮 ChaCha20 块函数,64 字节 keystream。"""
    state = list(struct.unpack('<4I', b'expand 32-byte k'))
    state += list(struct.unpack('<8I', key))
    state.append(counter)
    state += list(struct.unpack('<3I', nonce))
    initial = list(state)
    for _ in range(10):  # 10 double-rounds = 20 single rounds
        for a, b, c, d in [(0,4,8,12),(1,5,9,13),(2,6,10,14),(3,7,11,15)]:
            qr(state, a, b, c, d)
        for a, b, c, d in [(0,5,10,15),(1,6,11,12),(2,7,8,13),(3,4,9,14)]:
            qr(state, a, b, c, d)
    return struct.pack('<16I', *[(x+y) & MASK for x,y in zip(state, initial)])
```

## 性能与边界

- 单核 ~500 MB/s(Python 纯 stdlib ~10 MB/s,C/Go stdlib ~500 MB/s)
- 64 字节 keystream / 块;块计数器 32 bit → 单 key 最大加密 2^32 × 64 字节 = **256 GB**
- 96-bit nonce → 同一 key 下唯一值
- Poly1305 tag 16 字节,**不可截断**(RFC 8439 §4 MUST NOT)

## 注意事项与常见坑

- **Nonce 唯一性**:同一 key 下重复 nonce → 两消息明文 XOR 暴露 + Poly1305 撞 key,**致命**
- **Poly1305 r clamp** 必须做:不 clamp 可能构造出 r=0,s tag 退化为常数
- **Poly1305 常时比较 tag**:必须用常数时间函数比较,**禁止 memcmp**(时序侧信道)
- **ChaCha20 state 排序**:小端字节序,常量 "expand 32-byte k" 必须严格按 4 个 32-bit 字 0x61707865/0x3320646e/0x79622d32/0x6b206574
- **第 12 字是 counter**:AEAD 中第 0 块给 Poly1305 key,后续 1..N 给 keystream

## 参考资料(实际阅读过的权威来源)

- [RFC 8439 - ChaCha20 and Poly1305 for IETF Protocols](https://datatracker.ietf.org/doc/html/rfc8439) — 完整规范、§2.4.2 ChaCha20 测试向量、§2.5.2 Poly1305 测试、§2.6/§2.8 AEAD 测试
- [RFC 8446 - TLS 1.3](https://datatracker.ietf.org/doc/html/rfc8446) — 强制支持的 AEAD 之一
- [Bernstein 2008 - ChaCha, a variant of Salsa20](https://cr.yp.to/chacha.html) — ChaCha20 原始论文
- [Wikipedia - ChaCha20-Poly1305](https://en.wikipedia.org/wiki/ChaCha20-Poly1305) — 历史、特性、应用