# BLS 聚合签名（含 rogue-key 攻击与三种防护）

## 1. 简介

BLS 签名（Boneh–Lynn–Shacham，2001）的三个特点都来自**双线性配对**：

- 签名就是**一个群元素**（G1 上一个点），比 ECDSA 的 (r, s) 还短；
- n 个签名可以**压成一个点**：`σ = σ₁ + σ₂ + … + σₙ`；
- 验签只需要 `n+1` 次配对，而不是 n 次单签验证。

代价是验签比 ECDSA 慢一到两个数量级，而且**聚合天然带一个致命陷阱**：rogue-key 攻击。
本 demo 把攻击和三种标准防护都跑了一遍。

## 2. 为什么是玩具曲线

生产用 BLS12-381（嵌入次数 12、群阶 254 位），Miller 循环有几百次 F_p 运算，
作为 demo 全是数值噪声。这里换成**嵌入次数 2 的超奇异曲线**：

```
E: y² = x³ + x  over F_p,  p = 16252507 ≡ 3 (mod 4)
⇒ 超奇异，#E = p + 1 = 4·r，r = 4063127 为素数
F_p² = F_p[i]/(i²+1)        （p ≡ 3 mod 4 ⇒ -1 非二次剩余）
```

配对用**改造 Tate 配对 + 畸变映射**：

```
ψ(x, y) = (-x, i·y)            把 E(F_p) 的点搬到 E(F_p²)，让两个自变量“不同源”
e(P, Q) = f_{r,P}(ψ(Q))^((p²-1)/r)      最终幂 = (p-1)·h
```

Miller 循环、切线/弦线求值、最终幂的写法与 BLS12-381 **完全同构**，只是数字小到能手算。
自检里先钉住配对的四条性质：非退化、`e(G,G)^r = 1`、双线性 `e(aG,bG) = e(G,G)^{ab}`、
第一自变量可加，再往上搭 BLS。

> 本曲线**不安全**（r 只有 22 位，可被暴力求解离散对数），只用于把算法讲清楚。

## 3. 签名与验签（IETF draft-irtf-cfrg-bls-signature 口径）

```
CoreSign(SK, m) :  Q = hash_to_point(m)
                   R = SK · Q
                   return R

CoreVerify(PK, m, σ):  C1 = pairing(Q = H(m),  xP = PK)
                       C2 = pairing(R = σ,     P = G)
                       return C1 == C2
```

验签等价式就是双线性的直接展开：

```
e(σ, G) = e(sk·H(m), G) = e(H(m), G)^sk = e(H(m), sk·G) = e(H(m), PK)
```

注意**签名在 G1、公钥在 G2 还是反过来**由 ciphersuite 决定；这里的玩具曲线是
对称设定（Type-1），生产实现（BLS12-381）是 Type-3，两个群不可互换。

`hash_to_point` 本 demo 用 try-and-increment，**乘共因子 h 清到 r 阶子群**；
真实实现必须走 RFC 9380 的 `hash_to_curve`（含 `clear_cofactor`），否则会出现小子群攻击。

## 4. 聚合

```
Aggregate(σ₁..σₙ)        : σ = σ₁ + … + σₙ                    （还是一个点）
CoreAggregateVerify      : e(σ, G) == Π e(H(mᵢ), PKᵢ)
```

推导只有一步：`e(Σσᵢ, G) = Π e(σᵢ, G) = Π e(H(mᵢ), PKᵢ)`。
所以 n 个签名、n 把公钥、n 条消息，验证成本是 **n+1 次配对**（本 demo 里再加右侧 n 次）。

关键收益：**签名长度与参与方数量无关**。自检里把 3 个和 5 个签名聚合后都断言
「结果仍然是单个椭圆曲线点」。

## 5. Rogue-key 攻击

`CoreAggregateVerify` 只在乎**公钥之和**。攻击者于是可以注册一个「凭空捏造」的公钥：

```
已知受害者公钥 PK_v，攻击者自选 sk_a，注册：
    PK_rogue = PK_a − PK_v          （它并不需要知道 sk_v）

对同一条消息 m 伪造「两个人一起签的」聚合签名：
    σ = sk_a · H(m)

验证：e(σ, G) = e(H(m), PK_a) = e(H(m), PK_v + PK_rogue) = 右侧连乘  ✓
```

自检里两条断言把这件事说死了：

| 检查 | 结果 |
| --- | --- |
| `core_aggregate_verify([PK_v, PK_rogue], [m, m], σ)` | **通过**（攻击成立） |
| `aggregate_verify([PK_v, PK_rogue], [m, m], σ)` | **拒绝**（Basic scheme 生效） |

## 6. 三种防护（draft-irtf-cfrg-bls-signature §3）

| 方案 | 做法 | demo 里的断言 |
| --- | --- | --- |
| Basic scheme | `AggregateVerify` 前置检查：**消息必须互不相同** | 重复消息直接返回 INVALID，上表第二行 |
| Message augmentation | 签的是 `PK ‖ m`，不同签名者的被签串天然不同 | 同一条 rogue 伪造在增强方案下**不再成立** |
| Proof of possession | 注册公钥时附带 `σ_pop = sk·H("POP"‖PK)` | 攻击者用 `sk_a` 造的 PoP 对 `PK_rogue` **无效** |

PoP 为什么能挡住：攻击者构造得出 `PK_rogue`，但**算不出对应的 `sk_rogue = sk_a − sk_v`**
（`sk_v` 未知），所以无法签出能通过 `e(σ_pop, G) = e(H(POP‖PK), PK)` 的证明。
Basic scheme 与 message augmentation 是**从验证式上消除歧义**，PoP 是**从注册环节要求知情**。

## 7. 文件与运行

```
python selfcheck_bls.py            # 28 条断言
go run bls_aggregate.go            # 20 条断言
```

`bls_aggregate.py` 是模型（曲线 / 配对 / BLS），`selfcheck_bls.py` 是自检。

## 8. 关键代码

Miller 循环的一次倍点（Python 版）：

```python
lam = (3*xT*xT + 1) * _inv(2*yT) % P_MOD
l = ((Q[1][0] - yT - lam*(Q[0][0] - xT)) % P_MOD,
     (Q[1][1] - lam*Q[0][1]) % P_MOD)      # 切线在 Q 处的取值
T2 = pt_add(T, T)
v = ((Q[0][0] - T2[0]) % P_MOD, Q[0][1])   # 垂直线
f = f2_mul(f2_mul(f2_mul(f, f), l), f2_inv(v))   # f ← f²·l/v
```

两个必须显式处理的边界：`T` 是 2-挠点（切线退化）与 `T + A = O`（只剩垂直线）。
这两处漏掉时不会报错，只会让配对值悄悄变错 —— 本 demo 第一次跑就栽在后者。

聚合验证就一行：

```python
lhs = pairing(sig, G)
rhs = prod(pairing(hash_to_point(m), pk) for pk, m in zip(pks, msgs))
```

## 9. 性能与边界

- 配对是**最贵的运算**：一次 Miller 循环 = 22 轮倍点（本曲线 r 是 22 位），
  BLS12-381 的 r 是 256 位，单次要数毫秒。
- 聚合验签是 `n+1` 次配对 + n 次 `hash_to_curve`；
  **配对可以批量做**，但哈希到曲线的成本随 n 线性增长，是实际的瓶颈。
- 签名长度：G1 点压缩后 32/48 字节（BLS12-381），与 n 无关。
- 玩具曲线的 r 只有 22 位：**可以暴力求 sk**，绝不可用于真实系统。
- Type-3 配对下 G1/G2 不可互换，本 demo 的对称设定掩盖了这个约束。

## 10. 注意事项与常见坑

1. **直接用 `CoreAggregateVerify` 而不用 `AggregateVerify`**：rogue-key 攻击直接成立（§5 已复现）。
2. **hash-to-point 忘记乘共因子**：落点不在 r 阶子群，会产生小子群伪造。
3. **Miller 循环漏掉垂直线 `v` 或退化分支**：配对值错但不报错，必须先验双线性再往上搭。
4. **把 G1/G2 当可互换**：真实 BLS12-381 是 Type-3，签名与公钥所在群由 ciphersuite 固定。
5. **以为「聚合后还是能追责到单个人」**：聚合签名只证明「这些公钥的持有者都签了」，
   要追责必须额外约定（如增强方案把 PK 写进消息）。
6. **PoP 与消息内容解耦**：PoP 只证明「持有私钥」，不证明「认同某条消息」，别混用。

## 11. 参考资料

- Dan Boneh, Ben Lynn, Hovav Shacham, *Short Signatures from the Weil Pairing*（Asiacrypt 2001）
  — https://www.iacr.org/archive/asiacrypt2001/22480516.pdf
- IETF draft `draft-irtf-cfrg-bls-signature-05` §2.6 CoreSign、§2.7 CoreVerify、§2.8 Aggregate、
  §2.9 CoreAggregateVerify、§3.1 Basic scheme、§3.2 Message augmentation、§3.3 Proof of possession
  — https://www.ietf.org/archive/id/draft-irtf-cfrg-bls-signature-05.txt
- RFC 9380《Hashing to Elliptic Curves》（hash_to_curve 的标准化做法）
  — https://www.rfc-editor.org/rfc/rfc9380.txt
