# Ed25519 (EdDSA over Curve25519)

## 简介

- **Ed25519** 是 Bernstein 等人在 2011 年提出的爱德华兹曲线数字签名算法(EdDSA),由 RFC 8032 标准化,采用 **Curve25519 的扭曲爱德华兹形式 (a = -1, d = -121665/121666)**。
- 关键概念:
  - **私钥 (seed)**:32 字节随机数,经 SHA-512 + clamp 得到标量 a。
  - **公钥 A = [a]B**:32 字节编码的曲线点,B 是基点。
  - **签名 (R, s)**:64 字节 = 32B 的 R 点编码 + 32B 的 s 整数(小端)。
  - **SHA-512 域分离**:`dom2(F, C)` 字符串拼接在签名前,避免跨算法/上下文混淆攻击。
  - **确定性签名**:Ed25519 是确定性签名,salt = h[32:64] 直接从私钥 SHA-512 派生,**不依赖随机数**(避免 ECDSA nonce 重复失陷的问题)。
- 历史:Bernstein et al. 2011 "Twisted Edwards curves";Bernstein 2011 "Curve25519";RFC 8032 (2017) 标准化;TLS 1.3 默认签名算法;SSH 默认 `ssh-ed25519`;WireGuard、GnuPG、Signal Protocol 都用它。

## 原理详解

### Ed25519 密钥生成(RFC 8032 §5.1.5)

```
seed:  32B 随机数
h     = SHA-512(seed)              # 64B
a     = clamp(h[:32])              # 标量,见 clamp 规则
A     = [a]B                       # 公钥点
pk    = encode(A)                  # 32B 公钥编码
sk    = seed || pk                 # 64B 私钥(sk = seed 在前, pk 拼后)
```

**Clamp 规则**(对 h[:32] 的 256 bit):
- 清除 bit 0、1、2(让 a 是 8 的倍数)
- 清除 bit 255(最高位)
- 置位 bit 254(让 a 的阶 L-1 上界以下)
- **再对 a 取模 L**(基点阶 L = 2²⁵² + 27742...8493)

### Ed25519 签名(RFC 8032 §5.1.6)

```
h    = SHA-512(seed)              # 64B,前 32→scalar a,后 32→prefix
r    = SHA-512(prefix || M)  mod L  # 随机性来源,确定性但 ECDSA 安全
R    = [r]B
k    = SHA-512(encode(R) || pk || M)  mod L
s    = (r + k*a)  mod L
sig  = encode(R) || s.to_bytes(32, "little")   # 64B
```

**关键性质**:
- r 只依赖 prefix (= h[32:64]) 和 M,不引入新的随机数 → **确定性签名**;
- k 包含 R 和 A 和 M,绑定消息与公钥;
- a 和 r 都被 clamp / 模 L,避免 small-subgroup 攻击。

### Ed25519 验签(RFC 8032 §5.1.7)

```
解码 pk → A,解码 sig[:32] → R,解码 sig[32:] → s
k    = SHA-512(encode(R) || encode(A) || M)  mod L

# 等价简化的核心等式(PureEdDSA,complete 公式天然防 small subgroup)
[s]B == R + [k]A
```

> 完整规范要求 `[8][s]B == [8]R + [8][k]A` (cofactor mult),通过 `[8]` 把可能的小子群点拉到主群,进一步防攻击;但采用 complete addition formula 时简化公式即足够。

### 扭曲爱德华兹曲线(RFC 8032 §5.1.1)

```
曲线: -x² + y² = 1 + d·x²·y²    (mod p)
p    = 2^255 - 19
d    = -121665/121666  mod p
基点 B_y = 4/5 mod p, B_x 由 y 算出,x & 1 == 0
阶 L  = 2^252 + 27742317777372353535851937790883648493
```

**点加 complete 公式(RFC 8032 §5.1.4)**(X,Y,Z,T 扩展坐标,x=X/Z, y=Y/Z, xy=T/Z):

```
A = X1*X2
B = Y1*Y2
C = d*T1*T2
D = Z1*Z2
E = (X1+Y1)*(X2+Y2) - A - B
F = D - C
G = D + C
H = B + A        # 因为 a = -1, H = B - a*A = B + A
X3 = E*F
Y3 = G*H
T3 = E*H
Z3 = F*G
```

## 对比 / 选型

| 签名方案 | 类型 | 公钥/签名长度 | 签名速度 | 验签速度 | 抗时序 | 抗 nonce 重用 | 现状 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Ed25519** | 确定性 | 32B / 64B | **极快** | **极快** | ✅(complete 公式) | ✅(无 nonce) | **现代首选** |
| ECDSA-P256 | 确定性(k 随机) | 64B / 64B | 快 | 快 | ⚠️(nonce 重复失陷) | ⚠️ | 兼容 TLS 1.2 |
| RSA-PSS-2048 | 概率 | 256B / 256B | 慢(私钥) | 慢 | ✅ | ✅(salt) | 老协议 / JWT PS256 |
| SM2 | 概率 | 64B / 64B | 中 | 中 | ✅ | ⚠️ | 国密合规 |
| Schnorr (BIP-340) | 确定性 | 32B / 64B | 快 | 快 | ✅ | ✅ | Bitcoin Taproot |
| Ed448 | 确定性 | 57B / 114B | 较 Ed25519 慢 | 同 | ✅ | ✅ | 高安全(224-bit) |

> **生产建议**:优先 **Ed25519**;老协议/合规场景用 **RSA-PSS** 或 **ECDSA-P256**;避免 ECDSA nonce 重用(Sony PS3 惨案、RFC 6979 是补救)。

## 环境准备

- 操作系统:Windows / Linux / macOS
- 语言版本:Python ≥ 3.8、Go ≥ 1.18
- 依赖:**无第三方依赖**(Python 自实现 SHA-512 + 标量乘;Go 用 `crypto/ed25519` stdlib)

## 运行方式

### Python(自实现 Curve25519 twisted Edwards + SHA-512)

```bash
cd python
python3 main.py
```

输出应包含:
- `公钥/签名与 RFC §7.1 Test 1 一致 ✓`
- `自验签通过、篡改消息/签名被检测 ✓`
- `公钥与 §7.1 Test 2 一致 ✓`

> ⚠️ Python 实现按 bit-by-bit Montgomery ladder 实现 scalar mult,单次签名需 ~3-10 秒(取决于 SHA-512 + 大整数乘法)。

### Go(标准库 crypto/ed25519)

```bash
cd go
go run main.go
```

同样验证 RFC §7.1 Test 1 + 随机密钥 + 跨语言互操作骨架。

## 关键代码片段

### SHA-512 + clamp 标量(从 `python/main.py` 截取)

```python
def _scalar_clamp(h_bytes: bytes) -> int:
    """§5.1.5:清除 bit 0/1/2,置 bit 254,清 bit 255,然后 mod L。"""
    a = int.from_bytes(h_bytes, "little")
    a &= ~7                       # clear bit 0,1,2
    a &= (1 << 255) - 1           # clear bit 255
    a |= 1 << 254                 # set bit 254
    return a % L                  # RFC 8032 注 1:必须 < L
```

### Montgomery ladder scalar mult(`python/main.py` 截取)

```python
def __rmul__(self, n):
    """标量乘:Montgomery ladder,常数时间,O(n) 点加。
    不变量:r0 = [k]P, r1 = [k+1]P,k 是已处理位的 prefix。
      b = 0:r0 = r0+r0, r1 = r0+r1
      b = 1:r0 = r0+r1, r1 = r1+r1
    """
    r0, r1 = ZERO, self
    for i in range(n.bit_length() - 1, -1, -1):
        if (n >> i) & 1:
            r0, r1 = r0 + r1, r1 + r1
        else:
            r0, r1 = r0 + r0, r0 + r1
    return r0
```

### Go:GenerateKey + Sign + Verify

```go
pub, priv, _ := ed25519.GenerateKey(rand.Reader)
sig, _      := ed25519.Sign(priv, msg)
ok          := ed25519.Verify(pub, msg, sig)
```

## 性能与边界

- **签名时间**:Go stdlib ~10-30 μs(scalar mult ≈ 256 次扩展点加);Python 自实现 ~3-10 秒(纯 Python 大整数慢 100-300 倍)。
- **验签时间**:Go ~30-60 μs;Python ~3-10 秒。
- **公钥/签名长度**:32B / 64B,远小于 RSA-PSS(256B / 256B)。
- **安全等级**:~128-bit(等价于 RSA-3072)。
- **签名次数上限**:基于 L ≈ 2²⁵²,生日攻击 2¹²⁶ 次后才有可忽略概率的 forge;**实际生产可视为无限**。
- **消息长度**:无内置上限(适合流式 / 大文件);Ed25519ph(prehash)变体使用 SHA-512,适合 ≥ 256B 的消息。

## 注意事项与常见坑

| 坑 | 现象 | 原因 | 规避 |
| --- | --- | --- | --- |
| 标量未 mod L | 给出错公钥 | clamp 后 bit 254 置位,标量可能 > L | **强制 `a % L`**(RFC 8032 注 1) |
| Decode 时把 `x²` 当成 `u`(y²-1) | "not on curve" | Tonelli-Shanks 修复条件应是 `x² == x²_计算` | **保存中间 `x_sq`**(本 demo 的 `Point.decode` bug 历史) |
| Montgomery ladder 写错 update 顺序 | 2*B = 0 之类的灾难 | `r0, r1 = r1, r0 + r1` 把 r0 改成 r1 错位 | **写正确 ladder**:r0+r1/r1+r1 (bit=1) 或 r0+r0/r0+r1 (bit=0) |
| 把签名当成 ECDSA nonce 重复检查 | 完全无关 | Ed25519 无 nonce | **别做 nonce 校验**;确定性签名性质本身就是安全保证 |
| Ed25519ctx/ Ed25519ph 混用 | 跨算法攻击 (PureEdDSA 替代为 hash 模式) | 缺域分离 dom2 | 用 `crypto/ed25519` 的 `Options{Hash: crypto.SHA512, Context: ...}` |
| 把 public key 作为私钥使用 | 灾难 | Ed25519 公钥公开 | **公钥单向不可逆**,无侧信道泄漏(complete 公式 + 标量抹除) |
| 跨语言互操作私钥未 PKCS#8 序列化 | 字节序错乱 | 不同语言默认编码不同 | 用 `golang.org/x/crypto` 或 `cryptography.hazmat` 的 PKCS#8/PEM 序列化 |

## 参考资料

- [RFC 8032 — Edwards-Curve Digital Signature Algorithm (EdDSA)](https://www.rfc-editor.org/rfc/rfc8032) — Ed25519/Ed448 完整规范,§5.1.5 KeyGen、§5.1.6 Sign、§5.1.7 Verify(本 demo 直接采用 §7.1 Test 1/2)
- [RFC 7748 — Elliptic Curves for Security (Curve25519 / Curve448)](https://www.rfc-editor.org/rfc/rfc7748) — Montgomery 形式的 Curve25519
- [Bernstein et al., "Twisted Edwards Curves", 2008 Africacrypt](https://eprint.iacr.org/2008/013) — 扭曲爱德华兹曲线理论(签名运算性能比传统 Edwards 快)
- [Bernstein, "Curve25519: new Diffie-Hellman speed records", 2006](https://cr.yp.to/ecdh.html) — Curve25519 原论文(Montgomery ladder / ECDH)
- [Go 标准库 crypto/ed25519](https://pkg.go.dev/crypto/ed25519) — `GenerateKey`/`Sign`/`Verify` API(本 demo 的 Go 版本)
- [draft-irtf-cfrg-eddsa-08](https://datatracker.ietf.org/doc/draft-irtf-cfrg-eddsa/08) — RFC 8032 的最新草稿(本 demo 的算法步骤与之一致)
- [ED25519-TEST-VECTORS (draft-josefsson-eddsa-ed25519)](https://ed25519.cr.yp.to/python/sign.input) — RFC §7.1 测试向量的原始来源(Seed/PK/Sig 三元组)