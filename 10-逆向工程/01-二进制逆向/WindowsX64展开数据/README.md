# Windows x64 展开数据：.pdata / .xdata 与栈回溯

> Linux 侧用 `.eh_frame` 的 DW_CFA 程序描述「怎么回滚」（见 429 demo），Windows x64 用的是另一套：**表驱动**。编译器为每个非叶函数在 `.pdata` 里放一条 `RUNTIME_FUNCTION`，指向 `.xdata` 里的 `UNWIND_INFO`；异常发生时操作系统查表、按 unwind code 数组逐步回滚。本 demo 把这套结构编解码 + 展开执行逐条复刻。

## 一、两张表

```text
RUNTIME_FUNCTION（.pdata，按函数地址排序，image relative）
  ULONG begin / ULONG end / ULONG unwind_info

UNWIND_INFO（.xdata）
  UBYTE:3  version        # 当前为 1
  UBYTE:5  flags
  UBYTE    size of prolog
  UBYTE    count of unwind codes
  UBYTE:4  frame register  |  UBYTE:4  frame register offset (scaled)
  USHORT * n  unwind codes array
  之后：exception handler（ULONG + 语言相关数据）或 chained info（3 个 ULONG）
```

`UNWIND_CODE` 是一个 USHORT：`UBYTE offset in prolog | UBYTE:4 unwind op | UBYTE:4 op info`。

## 二、三处最容易记反的地方

1. **`count of unwind codes` 数的是槽，不是操作码**。`UWOP_SAVE_NONVOL` 占 2 个槽、`UWOP_SAVE_NONVOL_FAR` 占 3 个、`UWOP_ALLOC_LARGE` 视 op info 占 2 或 3 个。遍历数组必须按槽数跳，不能逐槽当操作码解析。

2. **数组按 prolog 偏移降序排列，且总被补齐到偶数项**（末项可能未用）。所以「最小展开数据是 8 字节」——`count=1` 也要占两个槽。

3. **chained info 的位置是 `UnwindCode[(CountOfCodes + 1) & ~1]`**，而不是 `CountOfCodes`。`count=4` 和 `count=3` 都落在槽 4；`count=5` 才落到槽 6。本 demo 直接断言这四个值。

## 三、操作码语义

| 操作码 | 槽数 | 撤销动作 |
| --- | --- | --- |
| `UWOP_PUSH_NONVOL` (0) | 1 | `reg = [RSP]; RSP += 8` |
| `UWOP_ALLOC_LARGE` (1) | 2 / 3 | info=0：`RSP += slot*8`；info=1：`RSP += hi<<16 \| lo`（未缩放） |
| `UWOP_ALLOC_SMALL` (2) | 1 | `RSP += info*8 + 8`（8 ~ 128 字节） |
| `UWOP_SET_FPREG` (3) | 1 | `RSP = FP - 16 * frame_offset` |
| `UWOP_SAVE_NONVOL` (4) | 2 | `reg = [base + slot*8]` |
| `UWOP_SAVE_NONVOL_FAR` (5) | 3 | `reg = [base + (hi<<16\|lo)]` |
| `UWOP_SAVE_XMM128` (8) | 2 | `xmmN = [base + slot*16]`（**缩放 16**） |
| `UWOP_SAVE_XMM128_FAR` (9) | 3 | 同上但偏移不缩放 |
| `UWOP_PUSH_MACHFRAME` (10) | 1 | 中断/异常机器帧：`RSP += 40`（info=0）或 `+48`（info=1） |

`base` 的取法是本套数据里最绕的一条：**Frame Register 为 0 时偏移自 `RSP`；否则自「FP 建立时的 RSP」，即 `FP - 16 * scaled`**。注意 `UWOP_SAVE_XMM128` 的缩放系数是 16 而 `UWOP_SAVE_NONVOL` 是 8。

## 四、展开的三种落点

规范把 RIP 相对函数起点分成三种情况：

- **a) 在 epilog**：不查表，直接对后续指令流做「是否匹配合法 epilog 尾部」的模拟。
- **b) 在 prolog**（`距离 <= prolog size`）：**向前扫到第一个 `offset <= rip 偏移` 的节点，撤销它及其之后的所有节点**。因为数组是降序的，前面那些 `offset > rip 偏移` 的项对应还没执行的指令。
- **c) 其余**：整段 code 数组全部撤销；若有 `UNW_FLAG_EHANDLER` 则先调语言相关 handler。

查不到 `RUNTIME_FUNCTION` 时按叶函数处理：`RIP = [RSP]; RSP += 8`。

实测一段 `push rbp; sub rsp,0x40`：

```text
rip+0 -> 撤销槽 []      rsp=0x1000 rbp=0x0
rip+1 -> 撤销槽 [1]     rsp=0x1008 rbp=0xaabb
rip+5 -> 撤销槽 [0, 1]  rsp=0x1048 rbp=0xccdd
```

## 五、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/winunwind.py` | `UNWIND_INFO` 编解码、槽位遍历、9 个操作码的撤销、`SET_FPREG`/`SAVE_*` 基准 |
| `python/selfcheck_winunwind.py` | **67 条断言实跑全绿** |
| `python/main.py` | 逐步演示 |
| `go/winunwind.go` + `go/main.go` | Go 侧同题实现（四项静态检查全过） |

自检覆盖：位布局回环（version/flags/prolog/count/frame 四字节）、奇偶补齐、chained 槽位公式的 4 个取值、三种 ALLOC 编码（128 / 800 / 0x11234）、`SAVE_*` 两种基准、机器帧增量 40 与 48、叶函数回退，以及负控（过短输入报错、未定义操作码报错、按升序遍历会得到不同的 RSP）。

## 参考资料（已读）

- [x64 exception handling — Microsoft Learn](https://learn.microsoft.com/en-us/cpp/build/exception-handling-x64) — `RUNTIME_FUNCTION` / `UNWIND_INFO` / `UNWIND_CODE` 字段表、9 个 unwind 操作码与槽数、寄存器编号表、`(CountOfCodes+1)&~1` 的 chained 定位、展开三分支、最小 8 字节
- [mingw-w64 `mingw-w64-headers/include/winnt.h`](https://raw.githubusercontent.com/mirror/mingw-w64/master/mingw-w64-headers/include/winnt.h) — `UNW_FLAG_NHANDLER 0x0` / `EHANDLER 0x1` / `UHANDLER 0x2` / `CHAININFO 0x4`
