# SHA-3 / Keccak(FIPS 202)

## 简介

SHA-3 是 NIST 于 2015 年在 FIPS 202 中标准化的第三代安全哈希标准, 基于 Keccak 团队的 **Keccak 海绵构造**。它与 SHA-2 完全不同的结构(替换而非升级): 状态 1600 bit, 由 5×5 个 64-bit lane 组成, 经 24 轮 `Keccak-f[1600]` 置换; **吸收(absorb)- 挤出(squeeze)**两阶段工作, 哈希与 XOF(任意长输出)统一在同一框架下。

本 demo 从零实现 `Keccak-f[1600]` 五步轮函数与海绵填充, 不调用任何哈希库(Python 侧仅用 hashlib 做交叉验证), 覆盖 SHA3-224/256/384/512 与 SHAKE128/256。

## 原理详解

### 1. 海绵构造(sponge)

- 状态 S: 5×5 lanes × 64 bit = 1600 bit。
- **rate r**(每轮吸收/挤出的字节数)与 **capacity c** 满足 r + c = 1600。
- 吸收: 明文按 r 字节分块, 逐块 XOR 进状态前 r 字节, 每块后做一次 `Keccak-f[1600]`。
- 挤出: 取状态前 r 字节, 需要更多输出就再置换再取 —— XOF 即"要多少挤多少"。
- 安全强度由 capacity 决定: 碰撞 2^(c/2), 原像 2^c。

### 2. 填充 pad10*1 + 域分离后缀

填充为: 消息 ‖ 后缀字节 ‖ 0x00… ‖ 0x80(即 bit 1, 0…, 1, 首尾 bit 恒为 1)。当末块恰好剩 1 字节时后缀与 0x80 **合并成一个字节**(SHA3 为 `0x06|0x80 = 0x86`, SHAKE 为 `0x1F|0x80 = 0x9F`)。

域分离后缀让不同实例的输入永不相同:

| 实例 | rate(字节) | capacity | 后缀 | 说明 |
| --- | --- | --- | --- | --- |
| SHA3-224 | 144 | 448 | 0x06 | bit 01 |
| SHA3-256 | 136 | 512 | 0x06 | bit 01 |
| SHA3-384 | 104 | 768 | 0x06 | bit 01 |
| SHA3-512 | 72 | 1024 | 0x06 | bit 01 |
| SHAKE128 | 168 | 256 | 0x1F | bit 1111 |
| SHAKE256 | 136 | 512 | 0x1F | bit 1111 |

> 注意: SHA-2 的"后缀"是长度域(Merkle–Damgård), SHA-3 的后缀是**域分离**——所以 SHAKE 输出任意长而互不冲突。

### 3. Keccak-f[1600] 五步轮函数

24 轮, 每轮依次:

1. **θ(theta)**: 每列 5 lane 求奇偶校验 `C[x] = ⊕y A[x,y]`; 差异量 `D[x] = C[x-1] ⊕ rot1(C[x+1])`; 全体 `A[x,y] ^= D[x]`。提供 2 轮扩散。
2. **ρ(rho)+π(pi)**: lane 内循环左移 `r[x][y]`(偏移表见下) + 平面坐标换位 `B[y, 2x+3y] = A[x,y]`。打散位间/word 间位置。
3. **χ(chi)**: `A[x,y] = B[x,y] ⊕ ((¬B[x+1,y]) ∧ B[x+2,y])` —— **唯一的非线性步**, 是 Keccak 安全性的来源。
4. **ι(iota)**: `A[0,0] ^= RC[轮]`, 破坏轮间对称性。RC 由一个 8-bit LFSR(`R = (R<<1) ^ ((R>>7)·0x71)`)逐位生成, bit j 落在 `2^j - 1` 位置。

旋转偏移表 `r[x][y]`(keccak.team Table 2):

```
        y=0  y=1  y=2  y=3  y=4
x=0:     0   36    3   41   18
x=1:     1   44   10   45    2
x=2:    62    6   43   15   61
x=3:    28   55   25   21   56
x=4:    27   20   39    8   14
```

### 4. 字节序约定

lane 的 64 bit 按**小端**字节序映射到状态字节(state[8k..8k+7] → lane k, 低位在前); bit 编号 LSB=0, `rot(W, r)` 表示把 bit i 移到 bit i+r。

## 对比

| 维度 | SHA-2(Merkle–Damgård) | SHA-3(海绵) |
| --- | --- | --- |
| 结构 | 压缩函数链式迭代 | 置换 + 吸收/挤出 |
| 输出长度 | 固定 | SHA3 固定; SHAKE 任意(XOF) |
| 长度扩展攻击 | HMAC 前直接用有风险 | 结构性免疫(capacity 保护) |
| 硬件实现 | 中 | 优秀(纯置换无密钥调度) |
| 软件 AVX-512 | ~2 cycles/B | ~6.4 cycles/B(SHA3-256) |

## 环境

- Python 3.10+(stdlib `hashlib` 仅用于交叉验证)
- C: C99(`cc sha3.c -o sha3`)
- Go 1.20+(`go run sha3.go`)

## 运行方式

```bash
python sha3.py     # 5 组自测: 官方向量/SHAKE/跨块/雪崩/padding 边界
go run sha3.go     # 同上(官方向量内置)
cc sha3.c -o sha3 && ./sha3
```

## 关键代码

- `keccak_f1600()`: 五步轮函数, lane 存于 `a[x + 5*y]` —— Python/Go/C 三版同构。
- RC 常量**不从表抄录**, 而是按官方 CompactFIPS202 的 LFSR 生成式推导 + 逐项断言(本次开发确实抓到了抄录错误: 两栏排版被工具压平导致第 10 项起错位)。
- `sponge()`: 吸收/挤出/填充, `state[last] ^= suffix; state[rate-1] ^= 0x80` 两行实现"合并填充"。

## 性能边界

- 纯 Python 版约 0.5~1 MB/s(教学实现, 无 SIMD); C 版单线程约 100~200 MB/s(未优化)。
- 安全边界: SHA3-256 碰撞 2^128 / 原像 2^256; SHAKE128 输出超过 ~2^(c/2) 位后安全强度下降, 长输出用 SHAKE256。

## 注意事项与常见坑

1. **RC 常量抄录是重灾区**——权威源(keccak.team)用两栏排版, 文本化后极易错位。用 LFSR 生成式自产 + 断言最稳。
2. **填充合并**: 末块剩 1 字节时 0x06 与 0x80 必须合并为 0x86, 拼成两个字节会平白多一个块且结果全错。
3. **字节序**: lane 是小端, 直接按大端组装 state 会得到"看似随机"的错误摘要。
4. **域分离不可省**: SHA3-256 与 Keccak-256(后缀 0x01)是两个函数; 以太坊用后者, 别混。
5. ρ 的旋转量按 `(t+1)(t+2)/2 mod 64` 走位生成(官方走位式), 与偏移表两种写法等价, 混用前先核对。
6. squeeze 时**最后一次置换后不要多挤**: 输出够长即停。

## 参考资料(实际读过)

1. Keccak 官方团队规格摘要(轮函数伪代码/RC/偏移表/各实例参数): https://keccak.team/keccak_specs_summary.html
2. XKCP(Keccak 官方参考实现)CompactFIPS202 Python 版, RC 的 LFSR 生成式与海绵结构对照: https://github.com/XKCP/XKCP/blob/master/Standalone/CompactFIPS202/Python/CompactFIPS202.py
3. NIST FIPS 202(SHA-3 标准)与 NIST 官方示例值(SHA3-256("")=a7ffc6f8…, SHA3-512("")=a69f73cc…, SHAKE128("")=7f9c2ba4…): 经 keccak.team/FIPS 202 引用与多源交叉确认
4. NIST CSRC SHA-3 项目页: https://csrc.nist.gov/projects/hash-functions
