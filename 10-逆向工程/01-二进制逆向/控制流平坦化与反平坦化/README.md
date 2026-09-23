# 控制流平坦化与反平坦化（OLLVM Flattening）

> 平坦化（control flow flattening）把函数里所有基本块塞进一个 `switch`，用一个 `switchVar` 状态变量在它们之间跳。反汇编看到的是一个巨大的分发表 + 若干个只做 `switchVar = 常量` 然后回到循环头的块。本 demo 复刻 OLLVM 的 `Flattening.cpp` 决策过程、case 值的生成函数 `scramble32`，并给出反平坦化的还原口径。

## 一、pass 做了什么

```text
1. 若任一基本块的终结指令是 invoke          -> 直接放弃（return false）
2. 若块数 <= 1                               -> 放弃
3. 入口块不进 switch；若入口以条件分支/多后继结尾，
   先 splitBasicBlock 切出一段（本 demo 记为 <entry>.first）并插到最前
4. 分配 switchVar（alloca i32），初值 = scramble32(0, key)
5. 建 loopEntry / loopEnd / switchDefault：
      loopEntry: switchVar = load; switch(switchVar) { case... default: swDefault }
      swDefault / 各块尾部 -> loopEnd -> loopEntry
6. 逐个 addCase：第 i 个块的 case 值 = scramble32(i, key)
7. 改写每块的转移：
      0 后继（ret）    -> 不动 switchVar
      1 后继           -> switchVar = findCaseDest(succ)
      2 后继（条件）   -> switchVar = select(cond, caseTrue, caseFalse)
8. fixStack：把所有 phi 与跨块使用的寄存器降级到栈
```

## 二、case 值为什么每次编译都不一样：`scramble32`

`CryptoUtils::scramble32(in, key)` 用四张 AES 预计算 T 表做四轮混合：

```text
round1: a = TE0[(in>>24)^k0] ^ TE1[(in>>16)^k1] ^ TE2[(in>>8)^k2] ^ TE3[in^k3]
round2: b = TE0[(a>>24)^k4]  ^ TE1[(a>>16)^k5]  ^ TE2[(a>>8)^k6]  ^ TE3[a^k7]
round3: a = TE0[(b>>24)^k8]  ^ TE1[(b>>16)^k9]  ^ TE2[(b>>8)^k10] ^ TE3[b^k11]
round4: b = TE0[(a>>24)^k12] ^ TE1[(a>>16)^k13] ^ TE2[(a>>8)^k14] ^ TE3[a^k15]
return  b ^ LOAD32H(key)        # LOAD32H = key 前 4 字节的大端值
```

`key` 是每次运行随机取的 16 字节（`get_bytes(scrambling_key, 16)`），所以同一份源码两次编译出的 case 值完全不同。

**四张表不是独立的**：`TE1 = ROTR8(TE0)`、`TE2 = ROTR16(TE0)`、`TE3 = ROTR24(TE0)`。本 demo 从 GF(2^8) 逆 + 仿射变换**现算 S 盒**再生成 TE0，并逐项断言与 `CryptoUtils.cpp` 里抄出的字面量一致（`TE0[0..3] = c66363a5 / f87c7c84 / ee777799 / f67b7b8d`）。

## 三、一处源码级「坑」：兜底值

改写转移时，`findCaseDest(succ)` 只在后继**在 switch 里**时返回 case 值；否则（后继是入口块这类情况）源码用：

```c
scramble32 (switchI->getNumCases () - 1, scrambling_key)
```

注意此时 `getNumCases()` 已经等于**块数**，所以 `块数 - 1` 正好是**最后一个块的 case 值**。也就是说「没找到」的回边不是跳到 default，而是**静默跳到最后一个基本块**。本 demo 把这条钉成断言（`fallback == case_of[order[-1]]`），并配负控：写成 `块数` 会偏一格。

## 四、反平坦化口径

拿到二进制后：

1. 找到循环头（`switch` 所在的块）与 `switchVar` 的 alloca；
2. 收集所有 `case` 常量 → 每个 case 对应一个真实基本块；
3. 每个块末尾的 `store` 就是转移：常量 = 无条件边，`select` = 条件边的两个目标；
4. 用 `case 值 -> 块` 的逆查表把常量还原成边，CFG 就回来了。

跳转表可能被编译器降级成一串 `cmp/je`，但 case 值本身是 32 位随机常量，无法按序推块顺序 —— 必须逐个查。

## 五、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/flatten.py` | S 盒 / T 表现算、`scramble32`、CFG 模型、`flatten` 决策过程、状态机执行、反平坦化 |
| `python/selfcheck_flatten.py` | **72 条断言实跑全绿** |
| `python/main.py` | 逐步演示 |
| `go/flatten.go` + `go/main.go` | Go 侧同题实现（四项静态检查全过） |

自检覆盖三类：T 表与 S 盒对源码字面量；`scramble32` 的钉值与结构性质（全零 key 下结果四字节相同，因为四轮输入字节全同）、少了末尾 `LOAD32H` 异或必然不同、前 200 个 case 值互不冲突；菱形 CFG 的「原 CFG 执行序列」与「状态机执行序列」逐条件对拍，自环、回边兜底、invoke/单块放弃、phi 降级。

## 六、实测输出（节选）

```text
scramble32(0..4, key) = 4c2b67af / 0827c599 / 0c51df22 / 0cad9e63 / 0c81d360
case 0x4c2b67af -> entry.first  ('select', 136824217, 206692130)
case 0x0827c599 -> B1           ('jmp', 212704867)
case 0x0c51df22 -> B2           ('jmp', 212704867)
case 0x0cad9e63 -> B3           ('ret',)
c0=True  -> ['entry.first', 'B1', 'B3'] (ret)
fallback = 0x0827c599 ；最后一块 case = 0x0827c599
```

## 参考资料（已读）

- [obfuscator-llvm `Flattening.cpp`（llvm-4.0）](https://raw.githubusercontent.com/obfuscator-llvm/obfuscator/llvm-4.0/lib/Transforms/Obfuscation/Flattening.cpp) — 放弃条件、入口切分、case 分配、`findCaseDest` 与兜底值、`fixStack`
- [obfuscator-llvm `Utils.cpp`](https://raw.githubusercontent.com/obfuscator-llvm/obfuscator/llvm-4.0/lib/Transforms/Obfuscation/Utils.cpp) — `fixStack` 的 phi/逃逸降级循环、`toObfuscate` 里 `nofla` 必须先于 `fla` 判定
- [obfuscator-llvm `CryptoUtils.cpp`](https://raw.githubusercontent.com/obfuscator-llvm/obfuscator/llvm-4.0/lib/Transforms/Obfuscation/CryptoUtils.cpp) — `AES_PRECOMP_TE0..TE3` 字面量、`scramble32` 四轮公式、`LOAD32H`
