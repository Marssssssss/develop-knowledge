# 符号执行与约束求解（claripy 位向量 + angr 状态机）

> 符号执行把输入变成**符号**而不是具体值，沿路径累积**路径约束**，遇到分支就分裂成两条状态，最后问求解器「什么输入能走到这里」。本 demo 拆两块：claripy 的位向量语义（这套语义写错一条就全盘皆错），以及 angr `SimulationManager` 的 stash 状态机。

## 一、位序：`a[31]` 是最左（最高）位

claripy 的位编号和 Python 的字节序直觉相反：

```text
32 位 a：
  a[31]  最左位（最高位）
  a[0]   最右位（最低位）
  a[31:30]  最高两位
  a[1:0]    最低两位
```

实测 `BVV(0x7FFFFFFF, 32)`：`a[31] == 0`、`a[0] == 1`。

由此派生出三条容易写反的：

- **`chop(bits)` 的第一个元素是最左（最高）的那一段**。源码里是 `reversed([self[(n+1)*bits-1 : n*bits] ...])` —— 先按低位切再反转。实测 `0x01020304.chop(8) == [0x01, 0x02, 0x03, 0x04]`。
- **`get_byte(index)` 是大端字节序号，0 是最高字节**。内部 `pos = (size+7)//8 - 1 - index`。
- **`concat(*args)` 里 `self` 在最左（最高位）**：`BVV(1,4).concat(BVV(2,4)) == 0x12`。

## 二、整数与布尔的隐式位宽

```python
_from_int(like, value)   -> BVV(value, like.length or 64)
_from_Bool(like, value)  -> If(value, BVV(1, like.length), BVV(0, like.length))
```

也就是说**参与运算的 Python 整数会被静默截断到左操作数的位宽**：

- `BVV(1, 8) + 300` 的位宽是 8，值是 `301 & 0xFF = 45`（不是 301）。
- `BVV(0, 8) - 1 == 255`（回绕）。
- `BVV(0, 8) + True == 1`，`+ False == 0`。

`zero_extend` / `sign_extend` 按文档示例：`BVV(0b1111,4).zero_extend(4) == 0b00001111`，`sign_extend(4) == 0b11111111`。

## 三、State 是插件盒，Manager 是 stash 状态机

`SimState` 默认插件：`regs / registers / mem / memory / solver / inspect / history / scratch / posix / fs / libc / heap / callstack`。`state.copy()` 得到的是**独立**的新状态（本 demo 断言改副本不影响原状态）。

`SimulationManager` 把状态放在若干"stash"里：

```text
_integral_stashes = ("active","stashed","pruned","unsat","errored",
                     "deadended","unconstrained")
ALL = "_ALL"    DROP = "_DROP"
```

- `step()`：对 `active` 里每个状态求后继，**没有后继的进 `deadended`**，约束不可解的默认丢弃（`save_unsat=True` 时进 `unsat`）。
- `move/stash/prune/drop`：按 filter 在 stash 之间搬，`DROP` 表示直接扔掉。
- `explore(find=..., avoid=..., num_find=1, find_stash="found", avoid_stash="avoid")`：**第一行就是 `num_find += len(self._stashes[find_stash])`** —— 已经找到过 N 个的话，这次会继续找，直到 `found` 里有 N+`num_find` 个。本 demo 用「先塞一个 found 再 explore」把它钉成断言。
- `avoid` 的搬移先于 `find`：命中 avoid 的状态先被挪走，不会同时进 found。

## 四、路径爆炸

每个分支点把状态数翻倍：实测 5 层二分后 `active = 32`。这也是为什么工程上要用 `Veritesting`、状态合并、`prune` 或者限制 `n`。

## 五、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/symex_bv.py` | 位向量 AST、位序/切片/chop/get_byte/concat/扩展、整数与布尔强制、求值、暴力求解 |
| `python/symex_state.py` | `SimSolver`（add/eval/min/max/is_sat）、`SimState` 插件表、`SimulationManager` 的 stash 状态机 |
| `python/selfcheck_symex.py` | **86 条断言实跑全绿** |
| `python/main.py` | 逐步演示 |
| `go/symex.go` + `go/manager.go` + `go/main.go` | Go 侧同题实现（四项静态检查全过） |

求解器用暴力枚举代替 Z3：符号位宽 ≤ 8 位时是完备的，更宽时只在候选窗口里找 —— 只用于验证语义，不代表 angr 的能力（angr 走 Z3）。

## 六、实测输出（节选）

```text
0x7fffffff[31] = 0  (最左位)     0x7fffffff[0] = 1  (最右位)
chop(8)   = ['0x1', '0x2', '0x3', '0x4']
get_byte  = ['0x1', '0x2', '0x3', '0x4']
BVV(1,8) + 300  -> 位宽 8, 值 45
约束 x>3, x<10 -> min=4 max=9 全解=[4, 5, 6, 7, 8, 9]
第 5 步：active=32
find=4 -> found=[4]     已有 1 个 found 后再 explore -> found=[99, 5]
```

## 参考资料（已读）

- [claripy `claripy/ast/bv.py`](https://raw.githubusercontent.com/angr/claripy/master/claripy/ast/bv.py) — 位序文档、`chop`/`__getitem__`/`get_byte`/`get_bytes`/`zero_extend`/`sign_extend`/`concat`/`_from_int`/`_from_Bool`
- [angr `angr/sim_state.py`](https://raw.githubusercontent.com/angr/angr/master/angr/sim_state.py) — `SimState` 的插件清单与类型标注
- [angr `angr/sim_manager.py`](https://raw.githubusercontent.com/angr/angr/master/angr/sim_manager.py) — `_integral_stashes`、`ALL`/`DROP`、`explore` 的 `num_find` 累加与 `Explorer` 装配
