# Shamir 秘密共享与阈值恢复（素数域 / GF(2^8) / SLIP-0039）

Shamir 1979 的方案只有一句话：**秘密是 t−1 次多项式的常数项，份额是多项式上的点**。
但「在什么域里做多项式」这一件事，让工程实现分成了三条路。本 demo 把三条各写一遍。

| | 素数域 GF(p) | GF(2^8)（Vault） | SLIP-0039 |
| --- | --- | --- | --- |
| 秘密藏在哪 | 常数项 `f(0)` | 常数项 `f(0)`，每字节一条 | `x = 255` 处的取值 |
| 份额的 x | `1..n`（禁用 0） | 随机取自 `[1,255]`，挂在份额末尾 | 索引 `0..N−1` |
| 份额大小 | 一个大整数对 | 与秘密等长 **+1 字节** | 与秘密等长（再编成助记词） |
| 份数上限 | 仅受域大小限制（p−1） | 255 | **16** |
| 能否自检拼错 | 不能 | 不能 | 能（摘要 D + RS1024 校验和） |

## 1. 素数域：原教旨版本

`p = 2^31 − 1`（梅森素数，且 `(p−1)²` 仍在 int64 内，便于逐行转写 Go）。
`s ∈ GF(p)`，随机取 `t−1` 个非零系数，`f(0) = s`，份额 `(i, f(i))`，
还原用拉格朗日插值在 `x = 0` 处求值，除法取 `a^(p−2)`（费马小定理）。

**"少于 t 份则零信息"不是口号，可以构造出来**（`prime.forge_share_for_secret`）：
手上有 `t−1` 个点时，对**任意**目标秘密 `s'`，都存在唯一的第 `t` 个份额使插值命中 `s'`。
既然每个候选秘密都对应一个合法份额，`t−1` 份就没有排除任何可能性 ——
这比"看起来像随机数"强得多，是信息论意义上的安全。demo 里对 `0 / 42 / 1 / p−1`
四个目标各证一次。

## 2. GF(2^8)：HashiCorp Vault 的做法

Vault 的 unseal key 就走这套（`shamir/shamir.go`），四个实现细节：

1. **域运算**：不可约多项式取 Rijndael 多项式 `x⁸+x⁴+x³+x+1`（低 8 位 `0x1B`，与 AES 同一个域）。
   乘法是位串行的：
   ```go
   r = (-(b >> i & 1) & a) ^ (-(r >> 7) & 0x1B) ^ (r + r)   // i 从 7 到 0
   ```
   Go 里 `-1` 作用在 uint8 上是 255，所以 `(-bit)&a` 就是"条件成立则取 a"。
   求逆不查表，连乘 11 次得到 `a^254 = a⁻¹`（域的阶是 255）。
   自检用 `0x57·0x83 = 0xC1`（FIPS 197 的域常量）钉住实现。
2. **每字节一条多项式**：GF(256) 只有 256 个元素，一条多项式的常数项只能承载 1 字节，
   所以 n 字节的秘密要拆成 n 条独立多项式。
3. **份额布局 `{y₁…yₙ, x}`**：x 只存一份、放在末尾（`ShareOverhead = 1`），
   且取 `Perm(255)` 后 **+1**，即 `x ∈ [1,255]` —— `x = 0` 会直接把秘密交出去。
4. **`evaluate` 对 `x = 0` 特判**直接返回常数项（不是算出来的），`Combine` 会查重复 x
   （`duplicate part detected`），因为重复 x 会让 `div` 的分母为 0。

## 3. SLIP-0039：把秘密放到 x = 255

SatoshiLabs 的助记词备份标准，同一条多项式上取**三个特殊点**：

```text
x = 255  →  秘密 S
x = 254  →  摘要 D = HMAC-SHA256(key=R, msg=S)[:4] ‖ R   （R 是 n−4 字节随机数）
x = 0..N−1 → 各份额
```

`SplitSecret(T, N, S)`：先随机 `D`，再随机 `y₁…y_{T−2}`（占 `x = 0…T−3`），
连同 `(254, D)`、`(255, S)` 共 T 个点定出 `(T−1)` 次多项式，其余份额由插值算出。
`T = 1` 直接退化成"每人一份明文"。

**D 的作用**：恢复时同时插值出 S 与 D，用 D 里的 R 重算 HMAC 比对。
于是**拿错份额、拼错助记词能被明确检出**，而不是吐出一串看起来正常的垃圾 ——
这一点素数域版和 Vault 版都做不到（自检里"篡改 1 字节 → digest mismatch"就是这条）。

助记词本身用 **RS1024** 校验和：GF(1024)（本原多项式 `x¹⁰+x³+1`）上的 3 字 Reed-Solomon，
保证检出任意 ≤3 个词的错误，>3 个词的漏检概率 < 10⁻⁹。
定制串（`"shamir"`）会先喂进 polymod 参与计算，所以换 cs 后同一份数据校验必失败。

限制：`N ≤ 16`、秘密**至少 128 位且长度是 16 位的倍数**。
规范还建议：单组 `T/N` 场景下应当"先拆 1 个组份额、再把它拆成 N 个成员份额"，
而不是建 N 个 1-of-1 组。

## 4. 运行

```bash
python python/selfcheck_shamir.py   # 389 条断言全绿
python python/main.py               # 三套方案同题对照
go run go/prime.go go/gf256go.go go/slip39go.go go/main.go
```

自检覆盖：素数域全部 `C(5,3)` 组合还原 + 伪造份额命中任意目标秘密 + `Σ L_i(0) = 1`；
GF(256) 求逆**全枚举 255 个非零元素**、乘法交换律与单位元、`0x57·0x83 = 0xC1`、
Vault 的全部入参校验（threshold<2 / parts>255 / 空秘密 / 重复 x / 长度不一致）；
SLIP-0039 的全部 `C(5,3)` 组合、T=1 退化、长度与 N 的边界、D 的结构自证、
RS1024 生成→校验往返与 1~3 个词错误必被检出。

## 5. 参考资料（实读）

- HashiCorp Vault `shamir/shamir.go`（main 分支，6510 B）：`https://github.com/hashicorp/vault/blob/main/shamir/shamir.go`
- SLIP-0039（SatoshiLabs，master 分支，43071 B）：`https://github.com/satoshilabs/slips/blob/master/slip-0039.md`
  —— §Sharing a secret / SplitSecret / RecoverSecret / Checksum / 设计原理附录（域选择的取舍）
- Shamir, "How to Share a Secret", CACM 1979（原始方案；本 demo 的素数域版即其直接实现）
- FIPS 197（AES）§3.2/§4.1/§4.2：Rijndael 不可约多项式与 GF(2^8) 乘法

> 口径说明：Vault 的 `Split` 用 `math/rand.Perm(255)` 取 x，demo 用 `rand.sample(range(1,256), parts)`
> 等价替代（同样保证 x ∈ [1,255] 且互异）；Vault 注释承认 `div` 在 `a = 0` 分支"leaks some timing
> information"，demo 照抄其行为并在 README 与注释中标注。
