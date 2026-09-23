# ML-DSA 后量子签名：格上的「取整 + 提示位」

把 **FIPS 204（Module-Lattice-Based Digital Signature Standard）** 的几条核心机制拆成可断言的最小模型：
为什么模数要选 `Q = 8380417`、为什么签名里除了 `z` 和 `c` 还要额外塞一段 **hint**、
以及「丢弃低位」这件事是怎么在验签时被补回来的。

## 事实来源

所有常量、公式、分支都来自下列**实际读过**的材料，未在官方没写定量处编造数值：

| 内容 | 位置 |
| --- | --- |
| 三档参数 `k/l/η/τ/β/γ₁/γ₂/ω` 与 `Q=8380417`、`D=13`、`ROOT_OF_UNITY=1753` | `pq-crystals/dilithium` `ref/params.h:6-46`（master 分支） |
| `POLYT1/T0/Z/W1/ETA_PACKEDBYTES` 与 `CRYPTO_PUBLICKEY/BYTES` | `ref/params.h:50-78` |
| `zetas[256]` 预计算表 | `ref/ntt.c:6-37` |
| `ntt()` / `invntt_tomont()`（`f = 41978` 收尾） | `ref/ntt.c:47`、`ref/ntt.c:80` |
| `montgomery_reduce`（`QINV = 58728449`）、`reduce32`、`caddq` | `ref/reduce.c:17/37/59`、`ref/reduce.h:8` |
| `power2round` / `decompose` / `make_hint` / `use_hint` | `ref/rounding.c:17/39/67/84` |
| `rej_eta`（η=2 丢 nibble 15、η=4 丢 9..15） | `ref/poly.c` `rej_eta` |
| `poly_challenge`（Fisher-Yates 变体，恰好 τ 个 ±1） | `ref/poly.c` `poly_challenge` |
| `poly_chknorm`（`bound > (Q-1)/8` 直接返回 1） | `ref/poly.c` `poly_chknorm` |
| 签名主循环三处 `chknorm` 拒绝与 `make_hint` 调用点 | `ref/sign.c` `crypto_sign_signature_internal` |
| Power2Round / Decompose / HighBits / LowBits / MakeHint / UseHint / NTT / BitRev8 的算法编号与文字说明 | FIPS 204 `NIST.FIPS.204.pdf` Algorithm 35–43 与 §7.4、§7.5（PDF 已下载并按 ToUnicode 映射抽取正文） |

> 参考实现是 Dilithium 轮次 3 的官方代码；FIPS 204 定稿的三档参数与之逐项一致
> （ML-DSA-44/65/87 ↔ `DILITHIUM_MODE` 2/3/5），公开密钥 1312/1952/2592、签名 2420/3309/4627 均可复算。

## 一、`Q = 8380417` 的三个用途

`Q - 1 = 8380416 = 2^13 × 1023`：

1. **`2^13` 支撑 Power2Round**：公钥里的 `t1 = t >> 13`，`t0 = t - t1·2^13`，
   于是 `t0` 落在 `(-2^12, 2^12]`，只需 13 位；
2. **`512 | Q-1` 让 `x^256+1` 完全分裂**：`ROOT_OF_UNITY = 1753` 是 512 次本原根，
   NTT 才能跑满 8 层、把 256 次多项式乘法降到 256 次逐点乘；
3. **`Q ≡ 1 (mod 4)`** 之类的小性质让 Montgomery 归约的 `QINV = 58728449` 存在（`Q·QINV ≡ 1 mod 2^32`）。

## 二、两种「丢弃低位」

| | Power2Round（Algorithm 35） | Decompose（Algorithm 36） |
| --- | --- | --- |
| 丢弃位数 | 固定 `D = 13` | `γ₂`，只有 `(Q-1)/88 = 95232` 与 `(Q-1)/32 = 261888` 两种 |
| 用途 | 压缩公钥 `t` | 压缩 `w`，签名里只留 `w1` |
| 高位基数 | `2^13 = 8192` | `2γ₂`（190464 或 523776） |
| 高位个数 | 由 `t1` 值域定 | **44**（γ₂=(Q-1)/88）或 **16**（γ₂=(Q-1)/32） |

`decompose` 的两个分支写法很不一样，但都是「先粗取整再修正」：

```c
a1  = (a + 127) >> 7;
#if GAMMA2 == (Q-1)/32
  a1  = (a1*1025 + (1 << 21)) >> 22;   // 乘 1025/2^22 ≈ 1/(2γ₂) 的定点近似
  a1 &= 15;                            // 超过 16 的那档直接绕回 0
#elif GAMMA2 == (Q-1)/88
  a1  = (a1*11275 + (1 << 23)) >> 24;
  a1 ^= ((43 - a1) >> 31) & a1;        // a1 > 43 时整项清零（等价于 &= 43 的变体）
#endif
```

`a1 ^= ((43 - a1) >> 31) & a1` 这一步容易看漏：算术右移在 `a1 > 43` 时给出全 1 掩码，
于是 `a1 ^ a1 = 0`——这正是 FIPS 204 说的「若取整会返回 `(Q-1)/α`，则改为返回 `0`」。

## 三、hint：这段字节为什么非有不可

签名方拿到 `w = A·y`，记 `w1 = HighBits(w)`、`w0 = LowBits(w)`；
但真正要发出去的是 `w0' = w0 - c·s2 + c·t0`（减掉掩码、加上低位修正）。
`w0'` 的绝对值**可以超过 `γ₂`**，此时验签方若直接对 `V = w1·2γ₂ + w0'` 取 HighBits，会落到隔壁档。

于是签名方多发一个比特：`h = MakeHint(w0', w1)`，验签方用 `UseHint(V, h)` 还原 `w1`。

本 demo 实测（ML-DSA-44，`w1 = 7`）：

```text
  w0'=       0  hint=0  HighBits(V)= 7  UseHint(V,hint)= 7
  w0'=   47616  hint=0  HighBits(V)= 7  UseHint(V,hint)= 7
  w0'=   95232  hint=0  HighBits(V)= 7  UseHint(V,hint)= 7
  w0'=   95242  hint=1  HighBits(V)= 8  UseHint(V,hint)= 7  <- 直接用 HighBits 会错
  w0'=  -95242  hint=1  HighBits(V)= 6  UseHint(V,hint)= 7  <- 直接用 HighBits 会错
```

`MakeHint` 的判据是**不对称**的（FIPS 204 Algorithm 39）：

```c
if(a0 > GAMMA2 || a0 < -GAMMA2 || (a0 == -GAMMA2 && a1 != 0)) return 1;
```

`a0 == +γ₂` 不置位，而 `a0 == -γ₂` 且 `a1 != 0` 要置位——因为 `-γ₂` 那一侧正是
「减一档」和「本档」的分界，`a1 == 0` 时（也就是 `Q-1` 附近的那个特殊档）再减就绕回 43 了。

**一个被拒绝条件挡住的角落**：`w0' == -2γ₂` 恰好命中时，`V` 的 `a0` 为 0，
`UseHint` 的 `if (a0 > 0)` 取否分支、返回 `a1 - 1`，会比正确值少 2。
参考实现同样如此。签名主循环里 `chknorm(w0, GAMMA2 - BETA)` 与 `chknorm(c·t0, GAMMA2)`
保证了 `|w0'| < 2γ₂ - β`，所以这个点**不可达**，不影响正确性。

## 四、2^-32 与 2^32 的抵消

`pointwise_montgomery` 每次乘法带一个 `2^-32`，`invntt_tomont` 收尾乘 `2^32`，
两者正好抵消，所以「NTT 域逐点乘 + 逆变换」等于负循环卷积**本身**：

```text
invntt_tomont(ntt(f))            == f · 2^32   (mod Q)
invntt_tomont(ntt(f) ⊙₂₋₃₂ ntt(g)) == f · g     (mod Q)
```

自检里用朴素 O(n²) 负循环卷积（`x^256 == -1`）做了整环对照，256 项全一致。

## 五、打包位宽速查

| 对象 | 位宽 | ML-DSA-44 | ML-DSA-65 | ML-DSA-87 |
| --- | --- | --- | --- | --- |
| `t1` | 10 | 320 B | 320 B | 320 B |
| `t0` | 13 | 416 B | 416 B | 416 B |
| `s1`/`s2` | 3 (η=2) / 4 (η=4) | 96 B | 128 B | 96 B |
| `z` | 18 (γ₁=2^17) / 20 (γ₁=2^19) | 576 B | 640 B | 640 B |
| `w1` | 6 (γ₂=(Q-1)/88) / 4 | 192 B | 128 B | 128 B |
| hint | — | 84 B | 61 B | 83 B |
| **签名合计** | | **2420 B** | **3309 B** | **4627 B** |

hint 段恒为 `ω + k` 字节：前面记录「哪些位置是 1」的下标，末尾 `k` 个字节记录每行用掉的槽数。
`ω` 是允许的 1 的个数上限，超过就重新采样——所以签名长度是**定长**的。

## 运行

```bash
cd python && python main.py             # 演示：参数表 / 分解 / hint / NTT / 采样 / 打包
cd python && python selfcheck_mldsa.py  # 断言版（43327 条）
cd go && go run params.go ring.go main.go
```

Go 侧覆盖不依赖 XOF 的整数部分（参数、Montgomery、NTT、分解与 hint、位打包）；
SHAKE128/256 相关采样只在 Python 侧用标准库 `hashlib` 实现。

## 自检覆盖的坑

1. **`montgomery_reduce` 返回的是 `(-Q, Q)` 有符号代表元**，不是 `[0, Q)`。
   断言时要写成 `(got - want) % Q == 0`，直接比相等会全红。
2. **带 `pointwise_montgomery` 的 NTT 乘法结果不带 `2^32`**，与单纯的 `invntt_tomont(ntt(f))` 差一个因子。
3. **`chknorm` 在 `bound > (Q-1)/8` 时恒为真**——这是源码里的守卫，不是「范数很大」。
4. **负控是必需的**：只断言「带 hint 能对」毫无信息量，必须同时断言「存在不带 hint 就错的用例」。
5. 抄 `zetas` 表时**漏 2 项**不会报错、只会让 NTT 静默算错——本 demo 用脚本逐项比对原始 `.c` 才发现。
