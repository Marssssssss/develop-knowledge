# 恒定时间实现与侧信道防护

「算法对」不等于「实现对」：RSA 的实现可以在数学上完全正确，却因为**一次提前 return**
把私钥泄露出去。本 demo 把 OpenSSL / Go standard library 的常量时间原语摊开写一遍，
再用一个观测器把「泄露」变成**可断言的量**。

## 1. 泄漏从哪里来（BearSSL 的执行模型）

BearSSL 的总结是：绝大多数机器指令本身就是恒定时间的，要盯的只有三类：

1. **内存访问**——表元素的地址会进缓存；命中/未命中的时间差能被同机甚至同 CPU 的
   另一个虚拟机测出来（对 RSA 与 AES 都已有实际攻击）。
2. **条件跳转**——取指本身就是读内存，跳转与否会读不同的地址。
3. **少数指令**——除法、部分乘法器在操作数不同时耗时不同（BearSSL 特别点名）。

历史节点：Kocher 1996 用**计时**还原 RSA 私钥；Boneh & Brumley 2003 证明
**远程**计时攻击对 SSL 服务器同样实用。

## 2. 掩码原语（OpenSSL `include/internal/constant_time.h`）

```c
constant_time_msb(a)      = 0 - (a >> 31)                    // 最高位复制成全 1 / 全 0
constant_time_is_zero(a)  = msb(~a & (a - 1))                // 只有 a=0 时 a-1 才回绕成全 1
constant_time_eq(a, b)    = is_zero(a ^ b)
constant_time_lt(a, b)    = msb(a ^ ((a ^ b) | ((a - b) ^ b)))
constant_time_select(m,a,b) = (barrier(m) & a) | (barrier(~m) & b)
```

`lt` 那行为什么这么绕：`a - b` 的借位落在最高位，但**只有 a、b 同号时**借位才等价于
`a < b`，所以还要与 `a ^ b` 的符号位做一次修正。demo 里枚举了 40×40 对整数逐项核对，
并钉住 `lt(0, 2^31) = 真`（无符号语义；按有符号解释会判反）。

Go 的 `crypto/subtle` 提供同一套（`ConstantTimeSelect` / `ConstantTimeByteEq` /
`ConstantTimeLessOrEq`），差别只是把条件值约定成 **0/1** 而不是 0/全 1，
且 `ConstantTimeCompare` 在长度不等时**立即返回 0**（长度本来就不是秘密）。

## 3. `value_barrier`：防的不是攻击者，是编译器

```c
static ossl_inline unsigned int value_barrier(unsigned int a) {
    __asm__("" : "=r"(r) : "0"(a));      // 或 volatile
}
```

一旦编译器能证明 `mask ∈ {0, 全 1}`，它就有权把 `(mask & a) | (~mask & b)`
**优化回一个条件跳转** —— 你写的恒定时间就白写了。屏障的作用是让取值范围分析失效。
demo 里用一个 `Compiler` 模型演示这件事（**这是模型不是真编译器**，口径已在代码注释标明）：

```text
不带屏障 -> 分支序列 [False, True]   ← 掩码（也就是秘密）被直接观测到
带屏障   -> 分支序列 [] ，只剩固定次数的算术
```

## 4. 四处「同一件事的两种写法」

| 场景 | 朴素写法泄漏了什么 | 恒定时间写法 |
| --- | --- | --- |
| `memcmp` | 观测值随**首个差异位置**线性变化 | 累积 `acc |= x[i] ^ y[i]`，不提前返回 |
| 表查找 | **访问序列 = 索引**（进缓存） | 整表读一遍再掩码挑，代价 O(n) |
| 模幂 | 平方-乘的步数 = 比特数 + 1 的个数 | Montgomery 阶梯：每比特算两个乘积再掩码挑 |
| PKCS#7 去填充 | 观测值随**填充长度**变化（填充预言机） | 全程掩码，只回一个合法/非法 |

实测（demo 输出）：填充 1/8/16 字节时朴素版观测值 4/18/34，常量时间版恒为 17；
朴素模幂在 `exp=0b1011` 与 `0b1000` 下步数是 7/5，阶梯版恒为 12（= 3 × 比特数）。

**阶梯版的实现细节**：`mask = 0 - bit` 得到全 1 或全 0，**一个 `if` 都不能有**；
写 `1 - eq32(...)` 也是错的（全 1 掩码被当成数值 0xFFFFFFFF 用了），
正确的"取反并归一"是 `~eq32(a,b) & 1`。

## 5. 运行

```bash
python python/selfcheck_ct.py   # 1702 条断言全绿（含 lt32 的 40×40 全枚举）
python python/main.py
go run go/ct.go go/main.go
```

自检覆盖：原语真值表（与 Python 语义逐项对照、`select` 的三种掩码、`select_int` 的负数回解释）、
memcmp 四种差异位置下朴素版观测值**互不相同**而常量时间版**相同**、
表查找四种索引下朴素版访问序列互不相同而常量时间版恒为 0..255、
模幂四种指数下结果与 `pow` 一致且阶梯版步数只与比特长度有关、
PKCS#7 四种填充长度与三种非法输入、以及 value_barrier 的屏障对照。

## 6. 参考资料（实读）

- BearSSL《Constant-Time Crypto》（Thomas Pornin，79378 B）：Why Constant-Time Crypto?（Kocher 1996、
  Boneh & Brumley 2003）、Execution Model（三类泄漏源）、Compiler Woes
  —— https://bearssl.org/constanttime.html
- OpenSSL `include/internal/constant_time.h`（master，15742 B）：`constant_time_msb` / `is_zero` /
  `lt` / `eq` / `select` 的原文实现，以及 `value_barrier` 的内联汇编与注释
  —— https://github.com/openssl/openssl/blob/master/include/internal/constant_time.h
- Go `src/crypto/subtle/constant_time.go`（master，1836 B）：`ConstantTimeCompare` 长度不等即返回 0、
  `ConstantTimeSelect` 约定 v ∈ {0,1}、`ConstantTimeLessOrEq` 要求非负且 ≤ 2^31-1
  —— https://github.com/golang/go/blob/master/src/crypto/subtle/constant_time.go

> 口径：本 demo 的"观测器"统计的是**事件序列**（分支走向、访问索引、运算步数），
> 不是真实计时；`Compiler` 是"取值范围分析后折叠分支"这一件事的模型，不是真编译器。
> 真实防护还需要考虑编译期屏障之外的运行时行为（如 CPU 的频率缩放、DVFS、 speculative execution）。
