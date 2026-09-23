# SPAKE2 口令认证密钥交换（RFC 9382）

SPAKE2 是 **PAKE（Password-Authenticated Key Exchange）**：双方只共享一个低熵口令 `pw`，
却能协商出高熵共享密钥 `Ke`，且**攻击者无法离线枚举口令**——它能做的只是每轮协议
试一个口令（在线猜测）。本 demo 实现 RFC 9382 的 `P256-SHA256-HKDF-HMAC` 套件，
并用 RFC 9382 §附录 B 的**四组官方向量**逐字节校验。

## 1. 协议本体（RFC 9382 §3.3）

设 `P` 是素数阶子群的生成元，`M`、`N` 是该子群内两个**离散对数未知**的元素，
`w = MHF(pw) mod p`（本 demo 直接给 w，MHF 由协议另行规定）：

```text
A:  x ←$ [0,p);  X = x*P;  pA = w*M + X        ──pA──▶
B:  y ←$ [0,p);  Y = y*P;  pB = w*N + Y        ◀──pB──
A:  K = h*x*(pB - w*N)
B:  K = h*y*(pA - w*M)          ← 双方算出同一个 K
```

`h` 是余因子。乘 `h` 不是顺手写的：它把结果映到商群里的唯一代表，
**阻断小子群限制攻击**（small subgroup confinement）。P-256 的 `h = 1`，
所以本 demo 里这一乘是恒等的，但代码仍照规范做，换成余因子 > 1 的群才不会漏。

## 2. 转录 TT 与密钥调度（§3.3 / §4）

```text
TT = len(A)||A || len(B)||B || len(pA)||pA || len(pB)||pB || len(K)||K || len(w)||w
Ke || Ka      = Hash(TT)                                  # |Ke| = |Ka| = 16 B（SHA-256）
KcA || KcB    = KDF(Ka, nil, "ConfirmationKeys" || AAD)   # 各 16 B
cA = MAC(KcA, TT)     cB = MAC(KcB, TT)
```

四个最容易写反的地方：

1. **`len(S)` 是 8 字节小端**（§3.2 明文规定）。写成大端则四组官方向量全灭。
2. **`w` 编码为大端、左侧零填充到 `p` 的字节长度**（32 字节），与实际数值大小无关。
   这是**故意的**：定长编码避免 `w` 的字节长度泄漏口令信息（§3.3 明说防计时攻击）。
   因此 `len(w)` 对该群是常量，规范说"为一致性我们仍然带上"。
3. **`A` / `B` 的顺序在两侧必须一致**。B 侧构造 TT 时要写 `(A, B, pA, pB, …)`，
   即**先对方再自己、先对方消息再自己消息**——`Party.finish()` 里由 `role` 分支处理。
4. **`cA` 与 `cB` 用的是不同密钥**：`KcA ≠ KcB`，且 `Kc_self` / `Kc_peer` 的切分
   在 A、B 两侧相反（A 取前 16 字节为自身确认密钥，B 取后 16 字节）。

## 3. M 与 N 从哪来（§6 + 附录 A）

不是随便挑的点。RFC 9382 给出 P-256 的常量（**压缩点**编码）：

```text
M = 02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f
N = 03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49
seed: 1.2.840.10045.3.1.7 point generation seed (M) / (N)
```

生成算法（附录 A）：对 seed 反复 SHA-256 迭代、按曲线规则修前缀字节（`canon_pointstr`
对 SEC1 曲线强制 `(s[0] & 1) | 2`）、尝试解码，直到落在群上且不是单位元。
所以 **`M`、`N` 的离散对数没人知道**——这是安全性证明的前提（§7）。
自定义曲线应改用 RFC 9380 的 `hash_to_curve("M SPAKE2 seed OID x")`。

## 4. 安全语义（§7，demo 里都做了断言/演示）

| 要点 | 说明 |
| --- | --- |
| 群成员检查 | 收到的 `pA`/`pB` **MUST** 验证在群上，否则有攻击 |
| x/y 不可复用 | 复用时 `pA(w₁) − pA(w₂) = (w₁−w₂)·M`，随机量被消掉 |
| 不支持 augmentation | 服务端必须存**口令等价物**（要增强型 PAKE 用 OPAQUE） |
| 身份缺失的风险 | 空身份只允许在"身份隐含"的场景使用，否则可能遭受 unknown key-share |
| 常量时间 | 点乘时长不得依赖输入；`w` 定长编码也是为此 |

**离线字典攻击演示**（`python/main.py` 第 5 节）：冒充 B 的一方拿到 `pA` 与 `cA` 后，
可以对每个候选 `w' = w+1, w+2, …` 本地重放 B 侧计算并比对 `MAC(KcA', TT') == cA`。
这说明 **PAKE 的安全性只能用"每轮一次在线猜测"刻画**——离线枚举是允许的，
限制攻击者的是必须每猜一次就交互一次。

## 5. 运行

```bash
python python/selfcheck_spake2.py     # 665 条断言（RFC 9382 附录 B 四组向量 + P-256 群律自证）
python python/main.py                 # 协议全流程 + 字典攻击演示
go run go/spake2.go go/p256.go        # Go 实现（离线分块，无本机工具链时仅人工审查）
```

自检覆盖：P-256 参数自证（`p = 2^256 − 2^224 + 2^192 + 2^96 − 1`、`a = p−3`、`h = 1`、
`n*G = O`）、群运算律（标量加法分配律与乘法结合律、逐项比对）、SEC1 编解码往返、
**四组官方向量的 pA / pB / K / TT / HASH(TT) / Ke / Ka / KcA / KcB / cA / cB 全字段比对**、
身份缺失（`A=""` 与 `B=""`）两种退化情形、AAD 参与 `ConfirmationKeys` 的影响、
x 复用导致随机量相消的成对对照、字典攻击命中与非命中的成对构造。

## 6. 参考资料（实读）

- RFC 9382（SPAKE2，2023）：https://www.rfc-editor.org/rfc/rfc9382.txt —— §3.2 记号与 `len()` 小端、
  §3.3 协议与 TT、§4 密钥调度、§6 套件表与 M/N 常量、§7 安全考虑、附录 A 点生成、附录 B 测试向量
- RFC 5869（HKDF）、RFC 2104（HMAC）、RFC 8032（Ed25519 编码）、SEC 1（点编码）

> 口径说明：`w` 的推导（MHF 与 mod p）不在 RFC 9382 范围内，本 demo 直接接受 `w` 整数，
> 与附录 B 向量一致；`H` 取 P-256 的余因子 1，故"乘 h"在本套件下可验证为恒等。
