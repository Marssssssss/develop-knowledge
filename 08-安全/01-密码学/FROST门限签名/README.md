# FROST：两轮门限签名为什么需要「绑定因子」

把 **RFC 9591（FROST: Flexible Round-Optimized Schnorr Threshold Signatures）** 的
Shamir 分片、拉格朗日插值、两轮协议、可识别中止拆成可断言的最小模型，
并回答一个具体的工程问题：**签名份额里那一项 `binding_nonce · ρ` 到底在防什么**。

## 事实来源

| 内容 | 位置 |
| --- | --- |
| 素数阶群的操作清单（Order / Identity / ScalarBaseMult / Serialize*） | RFC 9591 §3.1 |
| `nonce_generate = H3(random_bytes(32) ‖ SerializeScalar(secret))` | §4.1 |
| `derive_interpolating_value`（含两类 `invalid parameters`） | §4.2 |
| `encode_group_commitment_list` / `participants_from_commitment_list` / `binding_factor_for_participant` | §4.3 |
| `compute_binding_factors`：`ρ_input = PK_enc ‖ H4(msg) ‖ H5(承诺列表) ‖ id` | §4.4 |
| `compute_group_commitment`：`R = ∏(D_i · E_i^{ρ_i})` | §4.5 |
| `compute_challenge`：`H2(R_enc ‖ PK_enc ‖ msg)` | §4.6 |
| 两轮协议与 `z_i = hiding + binding·ρ_i + λ_i·sk_i·c` | §5.1 / §5.2 |
| 聚合与 `verify_signature_share`（可识别中止） | §5.3 |
| Shamir 分片 / 合并 / Horner 求值 | 附录 C.1、C.1.1 |
| Feldman VSS 承诺与校验 | 附录 C.2 |
| `H1..H5` 的域分隔串与「H2 特意不加域分隔以兼容 RFC 8032」 | §6.1 |

### 关于群的选择

RFC 9591 §3.1 只要求「素数阶群」，协议全文都写在这组抽象操作上。
本 demo 用 **Schnorr 群**（安全素数 `p = 2q+1` 的 `q` 阶子群）而不是 edwards25519，
这样群元素就是模 `p` 的整数、`ScalarMult` 就是模幂，不依赖任何第三方库也能完整跑通。
`p`、`q` 由脚本确定性搜出，自检里会用 Miller-Rabin 重新验证
（`p == 2q+1`、`g^q == 1`、`g ≠ 1`），不是手抄常数。

## 一、Shamir：为什么「取够 t 份」就能还原

`t` 份份额确定一条 `t-1` 次多项式，`f(0)` 就是秘密。还原时算的是
`f(0) = Σ y_i · λ_i`，其中 `λ_i = ∏_{j≠i} x_j / (x_j - x_i)`。

关键恒等式（自检里断言了）：**`Σ λ_i = 1`**。
只有系数和为 1，插值出来的常数项才正好是 `s`；这也解释了为什么少一份就会得到别的值。

## 二、绑定因子：FROST 与朴素门限 Schnorr 的分水岭

朴素门限 Schnorr 的份额是 `z_i = nonce_i + λ_i·sk_i·c`，其中 `R = ∏ g^{nonce_i}`
**与消息无关**。一旦某个参与者把同一组 nonce 用在两条消息上：

```text
z₁ = n + λ·sk·c₁
z₂ = n + λ·sk·c₂
⇒ sk = (z₁ - z₂) / (λ·(c₁ - c₂))
```

私钥份额**直接解出来**。本 demo 实跑确认：朴素方案下恢复值 `== sk_1`。

FROST 在每个份额里加了一项 `binding_nonce · ρ_i`，而 `ρ_i = H1(PK ‖ H4(msg) ‖ H5(承诺列表) ‖ i)`
把消息和全体承诺都卷进来了。于是：

- 同一个 nonce 对用在两条消息上，`ρ` 不同 → 上面那个减法消不掉 `e·(ρ₁ - ρ₂)`；
- 同一条消息换一组参与者，`H5(承诺列表)` 不同 → `ρ` 也不同。

自检里用**完全相同的恢复式子**去解 FROST 的两条签名，得到的值 `≠ sk_1`，断言失败即证明安全性成立。

> 注意 `ρ` 依赖的是**编码后的承诺列表**，所以列表顺序变了 `ρ` 就变。
> 这也是 §4.3 强调「承诺列表必须按 identifier 升序」的原因。

## 三、可识别中止

聚合签名验签失败时，协调者可以对每个份额单独跑 `verify_signature_share`：

```text
l = g^{z_i}
r = (D_i · E_i^{ρ_i}) · PK_i^{c·λ_i}
l == r  ?
```

本 demo 篡改参与者 2 的份额后，逐个校验得到 `[True, False, True]`——
**只有一个 False**，直接指认到具体节点，这正是「identifiable」的含义。

## 四、nonce 生成为什么要把私钥混进去

§4.1 的 `nonce_generate` 不是简单的随机数，而是
`H3(random_bytes(32) ‖ SerializeScalar(secret))`。
这样即使 RNG 被下毒、吐出可预测的值，nonce 里仍然混着长期私钥；
RFC 里给出的量化结论是：只要单方签名次数不超过 `2^64`，nonce 碰撞概率 ≤ `2^-128`。

## 运行

```bash
cd python && python main.py              # 演示：Shamir / 两轮协议 / nonce 复用对照
cd python && python selfcheck_frost.py   # 断言版（40 条）
cd go && go run group.go frost.go main.go
```

## 自检覆盖的坑

1. **随机行为必须用固定字节钉死**。本 demo 的 `nonce_generate` 接受一个 `rand` 参数，
   自检传固定值；否则「端到端验签通过」只是这一轮运气好。
2. **「少于门限」要断言聚合签名无效**，而不是断言「抛异常」——RFC 并没有规定这里必须报错，
   它只是要求协调者在发布前验签。
3. **`H2` 没有域分隔前缀**（§6.1 明说为了兼容 RFC 8032）。照抄 `H1/H3` 的写法加前缀，
   验签式会对不上。
4. **反序列化必须拒绝单位元与子群外的元素**（§3.1），否则恶意节点可以拿小子群元素做攻击。
5. 群承诺是 `∏(D_i · E_i^{ρ_i})`，**先乘 binding 项再乘 hiding**；顺序搞反在乘法群记号下
   结果相同，但改成非交换群（如某些曲线实现）就会错。
