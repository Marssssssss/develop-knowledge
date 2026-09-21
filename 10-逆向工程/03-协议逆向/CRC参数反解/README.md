# CRC 参数反解（RevEng / Rocksoft 参数化模型）

## 简介

协议逆向里最常见的一个具体动作：抓到一批报文，报文尾部有个 2 字节（或 1/4 字节）的字段，猜它是校验和——那么它是**哪种 CRC**？

CRC 不是一种算法，而是一个**六参数族**：`width, poly, init, refin, refout, xorout`。参数一变，同一个报文算出的值完全不同。所谓「CRC-16」至少对应几十种互不兼容的算法。

本 demo 做的事：

1. 用参数化模型实现 CRC，**与 RevEng 官方目录的全部 113 个模型的 `check` 值逐一对拍**；
2. 只给若干 `(报文, 校验和)` 样本，**反解**出这 6 个参数；
3. 说明为什么「参数解出来不唯一」，以及为什么断言只能落在**函数等价**上。

## 原理详解

### 1. 六参数模型（Rocksoft 记法）

| 参数 | 含义 |
| --- | --- |
| `width` | 寄存器位数 |
| `poly` | 生成多项式的低 `width` 位（最高位的 `x^width` 隐含） |
| `init` | 寄存器初值 |
| `refin` | 是否**反射输入字节** |
| `refout` | 是否**反射输出寄存器** |
| `xorout` | 最终异或值 |

```text
reg = reflect(init, width)          if refin
for byte in msg:
    b   = reflect(byte, 8)          if refin
    8 次:  out = msb(reg) ^ bit;  reg <<= 1;  if out: reg ^= poly
reg = reflect(reg, width)           if refout
return reg ^ xorout
```

**最容易写反的一条：寄存器永远左移。** `refin` 反射的是输入字节（和 init），不是「把寄存器改成右移」。按「refin=true ⇒ 右移寄存器 + 用反射后的多项式」去写，CRC-32 会算出 `0xFC891918` 而正确值是 `0xCBF43926`——差得毫无规律，很难靠肉眼发现。

### 2. 反解第一步：用「等长差」消掉 init 与 xorout

CRC 对 init 是**线性**的。对长度相同的两条报文，init 传播到最终寄存器的那一项完全相同，于是：

```text
crc(m1) ^ crc(m2) 与 init、xorout 都无关，只由 poly 决定
```

（`refout` 是比特置换，线性，可以搬到等式右边。）

所以 poly 可以**单独**搜：枚举 2^W 个候选多项式，只拿**最短的一对等长报文**做探针，看残值之差是否对得上。这一步把搜索空间从 2^(3W) 降到 2^W。

**报文不能截断**。探针要「最短的等长报文对」，是因为计算量正比于字节数——但绝不能把长报文截短来加速，截断后 CRC 与原报文毫无关系，不变式直接失效。

### 3. 反解第二步：init 与 xorout

poly 定了之后：

- `xorout` 由任意一条样本直接反解：`xorout = crc0(m0; init) ^ c0`；
- `init` 在 2^W 里搜。注意 **首条样本不构成约束**（xorout 就是由它算出来的），必须用第二条样本剪枝，否则 2^W 个 init 会全部「通过」。

### 4. 参数解不唯一

实测 CRC-8/SMBUS 反解出 **2 组**参数，都能复现全部样本：

```text
poly=0x07 init=0x00 xorout=0x00      ← 目录里的真值
poly=0x07 init=0xfd xorout=0xfd      ← 同样正确
```

原因很直白：`init` 与 `xorout` 之间存在一个自由度的平移——把 `init` 异或上 `k`，只要 `xorout` 也异或上 `k` 传播后的同一个值，输出完全一致。所以：

> **断言只能落在函数等价（一批报文上的输出序列），不能落在参数相等上。**

同理，`(refin, refout, poly)` 与它们的反射形也常常给出同一个函数。

### 5. 目录里的口径不一致（如实记录，不偷偷改数据）

113 个模型里有 **4 个**条目的 `init` 已经是「寄存器域」的值，`refin=true` 时**不能**再反射一次：

`CRC-16/ISO-IEC-14443-3-A`（init=0xC6C6）、`CRC-16/RIELLO`（0xB2AA）、`CRC-16/TMS37157`（0x89EC）、`CRC-24/BLE`（0x555555）。

按统一规则反射 init，这 4 个的 `check` 就对不上（自检 E5 对两侧都做了断言：反射后**错**、不反射**对**）。

`residue`（把 check 追加到报文后再算一次的残值）**本 demo 不断言**：它与「check 值在链路上的位对齐方式」有关，width 不是 8 的倍数时（CRC-10/11/12…）口径不唯一，实测几种字节序假设都对不上全部模型。宁可记录为未知，也不挑一个凑合的口径去刷绿。

## 运行方式

```bash
cd python && python main.py           # 反解演示
cd python && python selfcheck_crc.py  # 656 条断言
cd go && go run .                     # Go 同题实现（width-8 反解）
```

## 关键代码

| 位置 | 职责 |
| --- | --- |
| `python/catalogue.py` | 从官方目录页实抓的 113 个模型（自动生成） |
| `python/crc_model.py: crc` | 位级参数化 CRC（width 可以 < 8） |
| `python/crc_model.py: crc_fast` | 字节级查表快速路径（自检与位级逐条对拍） |
| `python/main.py: crc0_poly` | poly 搜索的内层循环（不建表，否则慢 100 倍） |
| `python/main.py: search_polys` | 等长差不变式筛 poly |
| `python/main.py: solve_init_xorout` | init 暴力搜索 + 第二条样本剪枝 |
| `go/crc.go / go/reveng.go` | Go 同题实现 |

## 性能边界

| width | poly 枚举 | init 搜索 | 实测耗时 |
| --- | --- | --- | --- |
| 8 | 256 × 4 | 256 | **约 0.4 秒** |
| 16 | 65536 × 4 | 65536 | **约 400 秒** |
| 32 | 4.3×10⁹ | — | **不可行** |

所以「32 位 CRC 参数反解」在工程上不是靠纯暴力，而要额外约束（常见多项式白名单、已知 init=0 或全 1、报文长度已知等）。本 demo 的 `recover` 对 width > 16 直接跳过 init 搜索。

另：内层循环**不能**为每个候选 poly 建 256 项查表——建表的开销（2048 次移位）远超直接算一次短报文（8 次移位），这是 400 秒与 4 秒的差别。

## 注意事项

- 样本里必须有**两条等长**报文，否则差不变式无从构造（`search_polys` 会直接返回空）。
- 报文要有足够多样性：全 0 报文对多个多项式都成立，会让候选集虚胖。
- 只有正样本（都是合法报文）时，参数族天然不唯一，这是**数学性质不是 bug**。

## 参考资料（已读）

- [Catalogue of parametrised CRC algorithms — CRC RevEng（271 KB HTML，113 个模型）](https://reveng.sourceforge.io/crc-catalogue/all.htm) —— 每个模型的 `width/poly/init/refin/refout/xorout/check/residue` 八元组、分类标注（attested / confirmed / academic / third-party / unconfirmed）、以及「`check` 是对 ASCII `123456789` 的 CRC」与 `residue` 的定义
- [CRC RevEng 主页](https://reveng.sourceforge.io/) —— 参数化模型（Rocksoft）的定义与 `reveng -s` 的搜索式反解思路
- Python 标准库 `zlib.crc32` —— 作为 CRC-32/ISO-HDLC 的独立 oracle 交叉验证（自检 E3，24 条随机报文逐条对拍）
