# Argon2id — 抗 GPU/ASIC 的内存硬密码哈希函数

## 简介

- **Argon2** 是 Biryukov、Bogatov、Khovratovich 2015 年设计的密码哈希函数,**Password Hashing Competition (PHC) 2015 冠军**。三个变体:`Argon2d`(数据依赖,挖矿友好)、`Argon2i`(数据独立,密码友好)、`Argon2id`(前 2 个迭代用 Argon2i + 后续用 Argon2d,默认推荐)。
- 抗 GPU/ASIC 攻击的**内存硬**(memory-hard)函数:可调参数 m(内存)、t(迭代次数)、p(并行度),通过消耗大量内存让定制硬件失去并行优势。
- 应用:用户密码哈希、加密货币 PoW、密钥派生(KDF)、硬盘加密 KDF。
- 关键概念:**Memory-hard function**、**BLAKE2b 内部哈希**、**填充矩阵 B[i][j]**、**lanes 与 slices(SL=4)**、**索引规则 Argon2d/i/id**、**ROMix 抗时间-空间折衷**。
- 历史:2015 PHC 胜出 → 2017 IETF 草案 → 2020 RFC 9106 标准化(v1.3,v=0x13)。

## 原理详解

### 1. 三种变体(RFC 9106 §3)

| 变体 | 类型值 y | 内存访问 | 用途 |
|------|---------|---------|------|
| Argon2d | 0 | 数据依赖 | 加密货币 PoW |
| Argon2i | 1 | 数据独立 | 密码哈希 |
| **Argon2id** | **2** | **混合** | **默认推荐** |

Argon2id 混合规则:**前 1/2 个 pass 的前 2 slice 用 Argon2i,其余用 Argon2d**(RFC 9106 §3.4.1.3):

```
if pass == 0 and slice in {0, 1}:  use Argon2i
else:                              use Argon2d
```

### 2. 输入参数

| 符号 | 含义 | 取值范围 |
|------|------|---------|
| P | 密码 | ≤ 2^32-1 字节 |
| S | Salt(每个密码应唯一) | ≤ 2^32-1 字节,推荐 16 字节 |
| p | 并行度(lanes) | 1..2^24-1 |
| T | Tag 长度 | 4..2^32-1 字节,典型 32 |
| m | 内存 KiB | ≥ 8p |
| t | 迭代次数(passes) | 1..2^32-1 |
| v | 版本号 | 0x13 |
| K | Secret(可选,密钥) | ≤ 2^32-1 |
| X | Associated Data(可选) | ≤ 2^32-1 |

### 3. H_0 初始化(BLAKE2b)

```
H_0 = BLAKE2b(LE32(p) || LE32(T) || LE32(m) || LE32(t) ||
              LE32(v) || LE32(y) || LE32(len(P)) || P ||
              LE32(len(S)) || S || LE32(len(K)) || K ||
              LE32(len(X)) || X)
```

输出 64 字节。

### 4. 填充矩阵 B[i][j]

- 维度:p lanes × q = m'/(4p) columns,每块 1024 字节
- `m' = 4 * p * floor(m / (4p))`
- 起始块:`B[i][0] = H'^1024(H_0 || LE32(0) || LE32(i))`
- 第二块:`B[i][1] = H'^1024(H_0 || LE32(1) || LE32(i))`
- 后续块:`B[i][j] = G(B[i][j-1], B[l][z])` 第 1 段
- 多 pass:`B[i][j] = G(B[i][j-1], B[l][z]) XOR B[i][j]`
- 最终块:`C = B[0][q-1] XOR B[1][q-1] XOR ... XOR B[p-1][q-1]`
- 输出:`Tag = H'^T(C)`

### 5. 压缩函数 G

```
G(X, Y) = Z XOR R
其中 R = X XOR Y,经过:
  - 8 行 P 变换 → Q
  - 8 列 P 变换 → Z
```

### 6. 置换 P(BLAKE2b 圆函数变体)

64 位无符号运算:

```
P(a, b, c, d):
    a = (a + b + 2*trunc(a)*trunc(b)) mod 2^64
    d = (d XOR a) >>> 32
    c = (c + d + 2*trunc(c)*trunc(d)) mod 2^64
    b = (b XOR c) >>> 24
(再执行一轮,旋转量为 16 和 63)
```

64-bit 乘法增加电路深度 = 增加 ASIC 成本。

### 7. 索引规则(Argon2d vs i)

Argon2d(数据依赖):
```
J_1 = int32(extract(B[i][j-1], 0))
J_2 = int32(extract(B[i][j-1], 1))
```

Argon2i(数据独立):
```
Z = LE64(r) || LE64(l) || LE64(sl) || LE64(m') || LE64(t) || LE64(y)
生成 j_1, j_2 通过 Z + 计数器 → G → 切 8 字节对
```

参考块映射:
```
l = J_2 mod p
W = (当前 lane 末 3 段 + 当前段已完成) - {B[i][j-1]}
   或(参考 lane 末 3 段)
x  = J_1^2 / 2^32
y  = (|W| * x) / 2^32
z  = W[|W| - 1 - y]
```

### 8. 推荐参数(RFC 9106 §7.4)

| 场景 | t | p | m | 时间(2 GHz) |
|------|---|---|---|---------------|
| 第一推荐(默认) | 1 | 4 | 2 GiB | 0.5s |
| 第二推荐(内存受限) | 3 | 4 | 64 MiB | ~1s |
| 后端认证 | 1 | 8 | 4 GiB | 0.5s |
| 硬盘加密 KDF | 1 | 4 | 6 GiB | 3s |

## 对比/选型

| 算法 | 内存硬 | 抗 ASIC | 抗 GPU | 标准 |
|------|--------|---------|--------|------|
| **Argon2id** | **✓✓✓** | ✓✓ | ✓✓ | RFC 9106 |
| Argon2d | ✓✓✓ | ✓✓ | ✓✓ | RFC 9106 |
| Argon2i | ✓✓✓ | ✓✓ | ✓✓ | RFC 9106 |
| **bcrypt** | ✓ | ✓ | ✓ | N/A |
| **scrypt** | ✓✓ | ✓ | ✓ | RFC 7914 |
| PBKDF2-HMAC-SHA256 | ✗ | ✗ | ✗ | RFC 8018 |
| Catena / Lyra2 / Balloon | ✓✓✓ | ✓✓ | ✓✓ | 候选 |

## 环境准备

- 操作系统:Windows / Linux / macOS
- 语言版本:C99+ / Python 3.8+ / Go 1.20+
- 依赖:**无**(纯 stdlib;实际部署用 libsodium/argon2-cffi)

## 运行方式

### C
```bash
gcc -O2 -Wall -Wextra -pedantic argon2.c -o argon2
./argon2
```

### Python
```bash
python3 argon2.py
```

### Go
```bash
go run argon2.go
```

## 关键代码片段

(以 Python 版为例,完整见源码)

```python
def argon2id(password, salt, t=3, m=32, p=4, taglen=32):
    # 1) H_0 = BLAKE2b(params + password + salt + secret + ad)
    # 2) B[i][0] = H'^1024(H_0 || LE32(0) || LE32(i))
    # 3) B[i][1] = H'^1024(H_0 || LE32(1) || LE32(i))
    # 4) For each pass:
    #    For each lane i:
    #      For each slice sl:
    #        For each column j:
    #          if pass==0 and sl in {0,1}: use Argon2i index
    #          else: use Argon2d index
    #          B[i][j] = G(B[i][j-1], B[l][z]) XOR (B[i][j] if pass>0)
    # 5) C = XOR of all B[i][q-1]
    # 6) tag = H'^T(C)
    ...
```

## 性能与边界

- 参数敏感:memory m 是主要成本,t×m 决定总计算量
- 安全参数:FIRST_RECOMMENDED(m=2^21, t=1, p=4)
- 内存硬意味着 GPU/ASIC 难以并行破解
- 长度限制:密码 / salt / secret / ad 都 ≤ 2^32-1 字节

## 注意事项与常见坑

- **Argon2id** vs **Argon2d** vs **Argon2i**:默认 Argon2id,密码场景;Argon2i 易受侧信道(数据独立但可预测);Argon2d 用于 PoW
- **密码 + salt 必备**;secret(密钥)与 associated data 可选但增强安全性
- **memory-cost**:必须 ≥ 8p
- **time-cost**:password 哈希 ≥ 1,Argon2id 1 pass 通常足够;密码场景不要 t=0
- **side-channel**:Argon2i 防侧信道,Argon2d 不防;Argon2id 混合前 2 slice 用 Argon2i
- **libsodium**:实际部署用 `crypto_pwhash_str` API(PHC 字符串格式 `$argon2id$v=19$m=65536,t=2,p=4$<salt>$<hash>`)

## 参考资料(实际阅读过的权威来源)

- [RFC 9106 - Argon2 Memory-Hard Function for Password Hashing and Proof-of-Work Applications](https://datatracker.ietf.org/doc/html/rfc9106) — 完整规范、变体、参数、测试向量(§5)
- [Argon2 原始论文 Biryukov et al. 2015](https://www.password-hashing.net/submissions/specs/Argon2-v3.pdf) — 设计动机、安全分析、抗时间-空间折衷攻击
- [Password Hashing Competition](https://www.password-hashing.net/) — 2015 PHC,Argon2 胜出
- [libsodium docs crypto_pwhash](https://libsodium.gitbook.io/doc/password_hashing/default_phf) — 生产环境 API 推荐