# WPA3-SAE（Dragonfly）密码元素推导与提交 / 确认交换

WPA3-Personal 把 WPA2 的四次握手换成了 **SAE（Simultaneous Authentication of Equals）**，
底层是 Dan Harkins 提出的 **Dragonfly** 密钥交换（RFC 7664）。
本 demo 用 **2048 位 MODP 群**实现 Dragonfly 的完整三段：**密码元素（PE）推导 → 提交交换 → 确认交换**，
并覆盖 RFC 里所有 MUST 级别的校验（标量范围、元素合法性、反射攻击、mask 销毁）。

同一套协议结构另有 **ECC 群**版本（`q` 是曲线阶、`element-op` 是点加），
两侧只换 §2.1/§2.2 的算子。这里选 MODP 是因为它**只依赖大整数模幂**，
不用写点运算与求平方根，能在同样篇幅里把三段的代数关系讲清楚。

## 原理详解

### 1. 密码元素：把口令确定性地映射成群元素

Dragonfly 的核心是：**双方用口令各自算出同一个群元素 PE，然后做 Diffie-Hellman**。
PE 由「狩猎与啄食」（hunting and pecking，RFC 7664 §3.2.2 的 Figure 2）得到：

```
base   = H(max(A,B) | min(A,B) | password | counter)        # 身份排序 → 双方视角一致
seed   = (KDF-(len(p)+64)(base, "Dragonfly Hunting And Pecking") mod (p-1)) + 1
temp   = seed^((p-1)/q) mod p
若 temp > 1 且尚未命中 → PE = temp
counter += 1，直到 (已命中 且 counter > k)
```

三个容易看漏的点：

- **身份用 max/min 排序**再拼接，所以 A 算 `H(bob|alice|…)`、B 也算同一个串 —— 这是 Dragonfly
  能做成「对等」（无固定 client/server）的关键，也是它能抵抗离线字典攻击的前提。
- **`KDF-n` 里多取 64 位**（`n = len(p) + 64`）再 mod (p-1)，是为了降低模约减带来的偏置。
- **`k` 不是「最多试 k 次」，而是「至少跑 k 轮」**：伪代码的循环条件是
  `while ((found == 0) || (counter <= k))`，命中后仍继续空转，**掩盖真实迭代数**（抗侧信道）。

在 2048 位安全素数群（`q = (p-1)/2`）里，「啄食」指数 `(p-1)/q` 恰好等于 **2** ——
任何非零 seed 的平方都落在子群里，所以**第一轮几乎必然命中**，`k` 纯粹是抗时序侧信道。
本 demo 的自检把这条等式写成了断言（`PE == seed^2 mod p`）。

### 2. 提交交换：代数上一层就能看出共享密钥为什么相同

```
1 < private < q          1 < mask < q
scalar  = (private + mask) mod q
Element = inverse(scalar-op(mask, PE)) = PE^(-mask) mod p
```

代入即可验证：`Element · PE^scalar = PE^(-mask) · PE^(private+mask) = PE^private`。
交换后各自计算

```
ss = scalar-op(private, element-op(peer-Element, scalar-op(peer-scalar, PE)))
   = (Peer-Element · PE^peer-scalar)^private
   = (PE^private' )^private          # 结构对称 → 双方必得同一个值
kck | mk = KDF-(2·len(p))(ss, "Dragonfly Key Derivation")   # 各 len(p) 位
```

自检里的「A 类断言」直接验证这个等式，而不是硬编码一个「应该等于」的魔数。

### 3. 确认交换：顺序是「发送方在前」

```
confirm = H(kck | scalar | peer-scalar | Element | Peer-Element | <sender-id>)
```

RFC 原文（§3.4）："The order of the scalars and elements are: scalars before elements,
and **sender's value before recipient's value**."

因为是**对等**交换，同一个 `H` 的输入在两个方向上顺序相反 —— 这意味着
**「拿自己的 confirm 去 verify 自己」必然失败**。自己期望收到的那个值，
标量与元素的先后要反过来，`<sender-id>` 也要用对端的标识。本 demo 三语言都把这个
镜像关系单独实现成 `expectedPeerConfirm` / `expected_peer_confirm`。

### 4. 三条 MUST（都是「漏了就出事」）

| 规范要求 | 漏掉的后果 |
| --- | --- |
| `1 < private, mask < q`；`scalar < 2` 必须重来 | 退化标量可让代数关系塌缩，泄漏信息 |
| mask 用完**立即销毁** | mask 是唯一能把 Element 反推回 PE（= 口令）的值 |
| 对端 scalar 与 Element 与自己**完全相同** → 中止 | 反射攻击：攻击者把消息原样弹回，诱使双方互相认证 |

## 对比

| 方案 | 口令的离线字典攻击 | 前向保密 | 抗被动窃听 | 交换轮数 |
| --- | --- | --- | --- | --- |
| WPA2-PSK 四次握手 | 抓到手摇即可离线爆破（PMKID / EAPOL） | 无 | 有（但握手可破） | 4 |
| **SAE / Dragonfly（本 demo）** | 每次猜测都必须在**线**交互，无法离线爆破 | 有 | 有 | 2（Commit + Confirm） |
| WPA3 部署中的 SAE | 同上，实际多用于**-H2E**（hash-to-element）替代狩猎与啄食 | 有 | 有 | 2 |

注：本篇实现的是 RFC 7664 的 MODP 版本；实际 WPA3-Personal 部署主要跑 **ECC 群**，
且已普遍改用 **Hash-to-Element**（避免狩猎与啄食的迭代时序特征）。

## 环境与运行

| 语言 | 版本要求 | 运行 |
| --- | --- | --- |
| Python | 3.8+（只用标准库） | `cd python && python sae_test.py` |
| C | C99 + OpenSSL 3.0+（`libcrypto`） | `cc -O2 -Wall -o sae_demo c/sae_demo.c -lcrypto && ./sae_demo` |
| Go | 1.18+（只用标准库） | `go run ./go` |

三个实现都**不引入第三方依赖**（C 侧只用 OpenSSL 的 `BIGNUM`，因为 2048 位模幂没必要自己攒）。
自检规模（运行时断言数，含循环展开）：Python **66** 项、C **73** 项、Go **68** 项，
覆盖群参数、PE 等式、提交代数、共享密钥、确认镜像、非法输入与反射攻击、敏感值销毁。

## 关键代码

派生密码元素（Python）：

```python
hi, lo = max(id_a, id_b), min(id_a, id_b)          # 身份排序 → 双方 PE 一致
base = h(hi + lo + password + nonce + bytes([counter]))
seed = (kdf(PWE_LABEL, base, P.bit_length() + 64) % (P - 1)) + 1
temp = scalar_op((P - 1) // Q, seed)               # 安全素数下就是 seed^2 mod p
if temp > 1 and pe is None:
    pe = temp
iterations += 1
if pe is not None and iterations >= k:             # 至少 k 轮，掩盖真实迭代数
    return pe, iterations
```

确认值必须带方向（Python）：

```python
def confirm(self) -> bytes:                         # 自己是发送方：自己的值在前
    return self._confirm_value(self.scalar, self.peer_scalar,
                               self.element, self.peer_element, self.name)

def expected_peer_confirm(self) -> bytes:           # 发送方是对端 → 顺序镜像
    return self._confirm_value(self.peer_scalar, self.scalar,
                               self.peer_element, self.element, self.peer_name)
```

## 跨语言一致性是怎么保证的

RFC 7664 把两件事留给上层协议：**单向函数 `H`** 与 **`KDF-n` 的构造**。这两处一旦不同，
三个实现算出的 PE / kck / confirm 就会各不相同，而且失败时完全没有报错线索。本 demo 的约定：

- `H = SHA-256`
- `KDF-n = HKDF-SHA256`（RFC 5869）：`PRK = HMAC-SHA256(32 字节全零盐, ikm)`，
  `T(i) = HMAC-SHA256(PRK, T(i-1) | info | i)`，`info` 取上述标签

并额外用**固定标量向量**把三边绑死：`password / alice / bob`、`k = 40` 下
PE、Element、ss、kck、两端 confirm 的 **SHA-256 摘要**写死在三份源码里
（比 sha256 摘要而非 2048 位常量，源码才读得下去）。任何一处的 `H` / KDF / 序列化宽度 /
拼接顺序被改动，三边立刻对不上。

## 性能边界

- **狩猎与啄食是唯一的性能热点**：每轮一次 2048 位模幂。`k = 40` → 约 40 次模幂
  （`k` 是抗侧信道的**固定代价**，与是否早命中无关）。
- 本机实测（Python 3.13）：单个 PE 推导约 **0.1 秒**量级；C 的 `BIGNUM` 与 Go 的
  `math/big` 会快一到两个数量级。
- 自检里「只关心校验是否被触发」的用例统一用 `k = 1`，避免 40 倍的无谓开销。
- 真实部署不用 MODP 2048 位：WPA3 用 P-256/P-384 的 ECC 群，且改用 Hash-to-Element，
  **没有迭代循环**，这才让 SAE 能在低算力 AP/STA 上跑。

## 注意事项与常见坑

1. **`verify` 的角色镜像**：本 demo 的 Python 版第一版就写成了「拿对端确认值与自己的
   `confirm()` 比较」，错密码用例通过、正确密码用例反而失败 —— 因为**自己发出的**和
   **期望收到的**是两个不同的值。这是最容易犯、也最难靠直觉发现的错。
2. **`k` 是下限不是上限**：命中后必须继续跑满 `k` 轮。写成「最多试 k 次就放弃」会在
   罕见输入下直接失败，也会把真实迭代数暴露成时序特征。
3. **`temp > 1` 而不是 `temp >= 1`**：RFC 的判据是**严格大于 1**。写成 `>=` 会把
   `temp == 1`（seed 是 ±1 的退化情形）收进 PE，得到阶为 2 的元素。
4. **元素合法性必须真的检验**：只检查「非零」会让攻击者提交一个小子群元素
   （如 `p-1`，其阶为 2）来把共享密钥锁死到极少几个取值。必须验 `e^q mod p == 1`。
5. **反射判定要看一对值**：只有 scalar 与 Element **同时**相同才是反射攻击。
   只比其一会把「合法的不同提交」误判成攻击，导致正常握手被中断。
6. **序列化必须定宽**：`kck | scalar | peer-scalar | Element | Peer-Element | sender`
   里凡是变长编码，都可能让两组不同的值拼出同一个字节串 —— 这是确认值可被伪造的经典入口。
   同理 `ss` 作为 KDF 输入也要按 `len(p)` 定宽。
7. **确认值比对要常数时间**：`memcmp` / 提前 return 会泄漏前缀匹配长度，等于给在线爆破
   装了个计数器（本 demo 的 C 版用 `sae_ct_eq`、Go 版用 `subtle.ConstantTimeCompare`）。
8. **敏感值销毁在各语言里能力不同**：C 用 `BN_clear_free`（真的覆写内存），
   Go 的 `big.Int` **没有清零 API**，只能置 `nil` 交给 GC —— 语义上不是等价的，
   不能照抄 C 的结论说「已安全擦除」。Python 的 `int` 更无法控制。
9. **`nonce` 参数是本 demo 的扩展**：RFC 7664 §3.2 的 MODP 伪代码里 `base` 不含 nonce
   （WPA3 的实际 PRF 会把 SSID 等揉进去）。Python 版留了可选 `nonce=b""`，
   默认值下与 C/Go 完全一致；跨语言对照时**不要**传非空 nonce。
   另外 RFC §3.2 说「若采用其它映射方法，秘密串 SHOULD 包含双方身份」——
   身份进了 PE，就意味着**换标识等于换口令**，接入协议侧必须保证标识在双方视图里一致。
10. **RFC 伪代码的变量命名有歧义**：Figure 2 里 `temp` 与 `seed` 被复用了两次
    （`temp = KDF-n(seed, …)` 里的 "seed" 其实是 `base`）。照抄变量名极易写错赋值顺序，
    本实现按语义重命名成 `base / seed / temp`。

## 参考资料

- [RFC 7664 — Dragonfly Key Exchange](https://www.rfc-editor.org/rfc/rfc7664.txt)
  （§2.2 有限域群与合法元素判据、§3.2.2 狩猎与啄食伪代码、§3.3 提交交换与三条 MUST、§3.4 确认值顺序）
- [RFC 3526 — More Modular Exponential (MODP) Diffie-Hellman groups for IKE](https://www.rfc-editor.org/rfc/rfc3526.txt)
  （§3 的 2048-bit MODP Group，id 14：本 demo 的 `p` 与 `G = 2` 逐字符取自此处）
- [RFC 5869 — HKDF](https://www.rfc-editor.org/rfc/rfc5869.txt)（本 demo 中 `KDF-n` 的构造口径）
