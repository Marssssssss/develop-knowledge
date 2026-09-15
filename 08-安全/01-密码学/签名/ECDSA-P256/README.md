# ECDSA(P-256) + RFC 6979 确定性签名

## 简介

ECDSA 是 TLS 证书、JWT ES256、Bitcoin(为 secp256k1)等广泛使用的椭圆曲线签名算法。标准 ECDSA 要求每次签名生成随机数 k——**k 的重用或偏置是 ECDSA 最经典的事故来源**(2010 年 Sony PS3 固定 k 泄私钥、2013 年 Android Bitcoin 钱包弱随机数被盗)。RFC 6979 用 HMAC-DRBG 从私钥与消息**确定性地**推导 k, 彻底消除随机数依赖。

本 demo 从零实现 P-256 点运算 + ECDSA 签名/验证 + RFC 6979 确定性 k, 逐字段通过 RFC 6979 A.2.5 官方测试向量。

## 原理详解

### 1. 曲线与点运算

- 曲线 `y² = x³ + ax + b (mod p)`, P-256 参数取自 SEC2 v2 §2.4.2(可验证随机, seed `C49D3608…`): a = p-3, p = 2^256-2^224+2^192+2^96-1, 基点 G 的阶 n 为素数, 余因子 h=1。
- **点加法**: 割线斜率 λ=(y2-y1)/(x2-x1) mod p, 交第三点翻折; **倍点**: 切线斜率 λ=(3x²+a)/(2y)。
- **标量乘** k·G: double-and-add(本 demo 用仿射坐标, 每步一次模逆; 生产实现用 Jacobian 坐标免模逆)。

### 2. 签名与验证

```
签名(私钥 x, 消息 m):
  e = H(m) 折算到 n 长度后 mod n
  k ← 随机(标准) 或 RFC 6979 确定
  R = k·G;  r = R.x mod n;  s = k⁻¹(e + r·x) mod n    → (r, s)

验证(公钥 Q = x·G):
  w = s⁻¹ mod n;  u1 = e·w;  u2 = r·w
  R' = u1·G + u2·Q;  验证 R'.x mod n == r
```

### 3. RFC 6979: HMAC-DRBG 生成确定性 k

```
h1 = H(m);  bx = int2octets(x) ‖ bits2octets(h1)   ← bits2octets = (h1 mod n) 定长 32 字节
V = 0x01×32;  K = 0x00×32
K = HMAC(K, V‖0x00‖bx);  V = HMAC(K,V)
K = HMAC(K, V‖0x01‖bx);  V = HMAC(K,V)
loop:
  V = HMAC(K,V);  T = V(恰 32 字节 = qlen=256)
  k = bits2int(T)          ← 截断到 qlen 位
  若 1 ≤ k ≤ n-1 且 r ≠ 0: 采用
  否则 K = HMAC(K, V‖0x00); V = HMAC(K,V); 重试
```

要点: **k 与 n 是比较而非取模**——直接 mod n 会引入偏置, 危害安全性; 拒绝采样保证均匀。

### 4. 为什么确定性 k 是对的

- 同一 (私钥, 消息) 永远产出同一签名 → 可复现测试、审计友好;
- 私钥与消息双输入经 HMAC 混合, 不同消息的 k 互不相关;
- 副作用: 签名可作"存在性证明"(同签名=同消息+同密钥), 这也是 EdDSA(Ed25519)从设计层面就采用的结构(见同目录 Ed25519 demo)。

## 对比

| 维度 | ECDSA + RFC 6979 | Ed25519 |
| --- | --- | --- |
| 随机性 | 构造确定性(外挂 DRBG) | 原生确定性(Nonce=H(h‖M)) |
| 点运算 | 仿射/雅可比, 需模逆 | 完整 twisted Edwards, 无分支 |
| 实现难度 | 高(验证方程易错) | 低 |
| 性能 | 慢(每步模逆) | 快 |
| 生态 | TLS/JWT 最广 | 现代(SSH/Signal/年龄) |

## 环境

- Python 3.8+(纯 stdlib); Go 1.20+(math/big); C99 + `__int128`(GCC/Clang)

## 运行方式

```bash
python ecdsa.py                                   # 5 组自测, 含 RFC 6979 A.2.5 全向量
go run ecdsa.go                                   # 同上
cc ecdsa.c bn256.c hmacsha256.c ecdsa_test.c -o ecdsa && ./ecdsa   # C 版手写 256-bit 大数
```

## 关键代码

- `rfc6979_k`/`drbg_*`: HMAC-DRBG, bx 的组装是易错点(int2octets(x) 32 字节 + bits2octets(h1) 32 字节, 后者是 h1 mod n 而非 h1 本身);
- `pt_add`: 仿射点加, **结果先写入局部 x3/y3 再拷出**——本次开发确实抓到了 r 与 p1 别名导致读到新值的 bug;
- C 版 `bn256.c`: 移位-比较-相减式模归约(慢但显然正确) + 二元扩展欧几里得模逆; 常量与向量全部运行时从 hex 解析, 不手抄 limb。

## 性能边界

- Python 版单次签名 ~50 ms(仿射+模逆); C 版 ~1-2 ms(未优化); 生产库(GMSSL/OpenSSL)用 Jacobian 坐标+窗口法快 10-100 倍;
- P-256 安全强度 128 bit(与 RSA-3072 相当)。

## 注意事项与常见坑

1. **k 复用 = 私钥泄露**: 两次签名 k 相同 → x = (s1-s2)/(r1-r2) mod n。ECDSA 的历史事故几乎全是它。
2. **bits2octets ≠ 哈希截断**: 是 h1 mod n 的定长编码; 写成 h1 原值在 A.2.5 向量立刻现形。
3. **e 的折算**: H(m) 为 256 bit 时 e = h1 mod n(单次条件减); 哈希更长时先截到 qlen 再 mod。
4. **malleability**: (r, n-s) 也是合法签名 → 契约里比对签名原文前先规范化或禁用高低 s(比特币 BIP62 强制低 s)。
5. 公钥未验证在曲线上就做验证 = 无效曲线攻击面; verify 必须先检查 Q 在曲线上(本 demo demo3 覆盖)。
6. 仿射实现每步模逆仅供教学; 生产必用 Jacobian/窗口法。

## 参考资料(实际读过)

1. RFC 6979(确定性 ECDSA, 算法 + A.2.5 P-256/SHA-256 测试向量): https://www.rfc-editor.org/rfc/rfc6979.html
2. SEC2 v2 §2.4.2(secp256r1 曲线参数 p/a/b/G/n/h 与可验证随机 seed): https://www.secg.org/sec2-v2.pdf
3. FIPS 186-5(ECDSA 规范): https://csrc.nist.gov/pubs/fips/186-5/final
4. RFC 2104(HMAC, C 版 DRBG 基件): https://www.rfc-editor.org/rfc/rfc2104.html
