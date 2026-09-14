# 字节码与自适应解释器(CPython 3.11+ 的 quickening)

## 简介

CPython 先把源码编译成 **code object**(含一段 2 字节为单位的字节码),再由**栈式求值循环**
逐条执行。3.11 起求值循环变成"专用化的自适应解释器":字节码会在运行时**就地改写**,
把 `LOAD_ATTR` / `BINARY_OP` / `LOAD_GLOBAL` 等通用指令换成针对当前类型的快速变体。

- **一句话**:一种"不生成机器码的 JIT 替代方案" —— 靠内联缓存做推测性专用化,错猜了就廉价回退。
- 关键概念:
  - **code object**:字节码 + `co_consts` / `co_names` / `co_varnames` / `co_stacksize` 的容器
  - **opcode / oparg**:每条基础指令 2 字节(1 字节操作码 + 1 字节操作数)
  - **EXTENDED_ARG**:把操作数从 1 字节扩展到最多 4 字节的前缀指令
  - **CACHE / 内联缓存**:紧跟指令的额外 2 字节槽,存计数器与专用化数据
  - **quickening**:把慢指令替换成自适应变体,执行若干次后专用化;失败则去优化回自适应态
- 历史:PEP 659(Mark Shannon,2021)设计,Python 3.11 落地;PEP 已转为历史文档,
  后续演化见官方 *Specializing Adaptive Interpreter* 文档。

## 原理详解

1. **编译**:`compile()` 把源码编译成 code object。`dis.dis()` 的文档明确:
   "Strings are first compiled to code objects with the `compile()` built-in function
   before being disassembled."
2. **编码**:3.6 起每条基础指令固定 2 字节
   ("Changed in version 3.6: Use 2 bytes for each instruction")。
3. **求值**:解释器是栈机器,dis 文档以 `STACK` 列表建模,
   "The top of the stack corresponds to `STACK[-1]`"。典型语义:

   | 指令 | 栈效应(dis 文档原文语义) |
   | --- | --- |
   | `BINARY_OP` | `rhs = STACK.pop(); lhs = STACK.pop(); STACK.append(lhs op rhs)` |
   | `CALL` | 弹出可调用对象与全部实参,**压回返回值** |
   | `RETURN_VALUE` | "Returns with `STACK[-1]` to the caller of the function." |
   | `POP_TOP` | `STACK.pop()` |

4. **操作数扩展**:oparg 只有 1 字节(≤255),超出时用 `EXTENDED_ARG` 前缀:
   "For each opcode, at most three prefixal `EXTENDED_ARG` are allowed, forming an
   argument from two-byte to four-byte." Demo §3 用 300 条赋值把常量索引推到 299,
   观察到 2 个前缀 + `LOAD_CONST`。
5. **内联缓存**:`CACHE` 不是真指令,而是"为解释器预留的空间,把有用数据直接缓存进字节码",
   默认被所有 dis 工具隐藏(`show_caches=True` 可见);逻辑上属于**前一条指令**。
6. **专用化(PEP 659)** 分三态:

   ```text
   通用指令  --quickening-->  自适应指令  --预热计数归零-->  专用指令
   LOAD_ATTR                 LOAD_ATTR_ADAPTIVE           LOAD_ATTR_INSTANCE_VALUE
        ^                                                        |
        +------------- 饱和计数器触发下限:去优化 <---------------+
   ```

   - **预热**:每个自适应指令维护计数器(放在 CACHE 的第 0 个 16 bit 槽),
     "attempts to specialize itself when that counter reaches zero"。
   - **专用**:每个专用指令维护**饱和计数器**,输入符合预期就 +1;不符就 -1 并执行通用逻辑,
     "If the counter reaches the minimum value, the instruction is de-optimized by simply
     replacing its opcode with the adaptive version."
   - **粒度**:单个字节码。PEP 明确这是关键设计 ——
     "Specialization at the level of individual bytecodes makes de-optimization trivial,
     as it cannot occur in the middle of a region."
   - **家族**:`LOAD_ATTR` 可专用为 `LOAD_ATTR_INSTANCE_VALUE` / `LOAD_ATTR_MODULE` /
     `LOAD_ATTR_SLOT`(配合 `__slots__`);`LOAD_GLOBAL` 可专用为 `LOAD_GLOBAL_MODULE` /
     `LOAD_GLOBAL_BUILTIN`,后者只需检查全局名字表的 keys 是否变化,不关心值的修改。
7. **附带数据**:8 bit 操作数放不下专用化所需信息,于是"a number of 16 bit entries
   immediately following the instruction are used to store this data" —— 即 inline data cache。

## 对比 / 选型

| 方案 | 机制 | 代价 | 备注 |
| --- | --- | --- | --- |
| CPython ≤3.10 | 纯通用字节码 | 属性/全局变量查找每次都走完整字典路径 | 结构简单、无预热 |
| CPython ≥3.11 自适应解释器 | 单指令粒度推测 + 内联缓存,无需生成机器码 | 预热成本低、去优化简单;内存换速度 | PEP 659;**不改变语言语义与 API** |
| PyPy JIT | 追踪编译成机器码 | 预热长、内存高、C 扩展兼容差 | 峰值性能更高 |

PEP 659 自述不改变语言/库/API:"The only way that users will be able to detect the presence
of the new interpreter is through timing execution, the use of debugging tools, or
measuring memory use."

## 环境准备

- 操作系统:任意(字节码格式与平台无关,但与 CPython 版本强相关)
- 语言版本:Python **3.11+**(`cache_info` 属性需 3.13+;本 demo 实测于 3.13.14)
- 依赖:仅标准库 `dis` / `sys`

## 运行方式

```bash
python bytecode_vm.py
```

## 关键代码片段

栈式求值循环的核心四条分支 —— 与 dis 文档的 `STACK` 语义逐条对应:

```python
elif op == BINARY_OP:                      # §原理 3 表格
    rhs, lhs = stack.pop(), stack.pop()
    stack.append(BINOPS[list(BINOPS)[arg]](lhs, rhs))
elif op == STORE_FAST:                     # STACK.pop() 写入局部变量表
    local[code.co_varnames[arg]] = stack.pop()
elif op == JUMP_IF_FALSE:                  # 弹栈;为假则跳转
    if not stack.pop():
        pc = arg
        continue
elif op == RETURN_VALUE:                   # "Returns with STACK[-1] to the caller"
    return stack[-1], trace
```

自适应专用化的三态状态机(对应 PEP 659 的预热 / 专用 / 去优化):

```python
if self.state == "LOAD_ATTR_ADAPTIVE":
    self.counter -= 1
    if self.counter == 0 and isinstance(obj, dict):     # 预热结束 -> 专用化
        self.state = "LOAD_ATTR_INSTANCE_VALUE"
elif isinstance(obj, dict):                             # 专用态快路径:计数器饱和
    self.counter = min(self.counter + 1, SATURATE)
else:                                                   # 猜测失败:衰减并回退
    self.counter -= 1
    if self.counter <= 0:                               # 触底 -> 去优化
        self.state, self.counter = "LOAD_ATTR_ADAPTIVE", WARMUP
```

## 性能与边界

- PEP 659 估计提速区间:**10% – 60%**;摘要给出"up to 50%","Even if the speedup were
  only 25%, this would still be a worthwhile enhancement."
- 收益来源:"The largest contributors are speedups to attribute lookup, global variables,
  and calls."
- 覆盖率:PEP 拒绝方案一节提到"Experiments show that 25% to 30% of instructions can be
  usefully specialized."
- **边界**:字节码不是稳定 ABI —— "No guarantees are made that bytecode will not be added,
  removed, or changed between versions of Python. Use of this module should not be
  considered to work across Python VMs or Python releases."

## 注意事项与常见坑

1. **别把字节码当接口**。要跨版本稳定,用语言语义或 `ast` / `sys.monitoring`,不要匹配 opname。
2. **3.13 起 `dis.get_instructions()` 不再把 CACHE 单列**:改为 `Instruction.cache_info`
   字段;`show_caches` 参数已弃用且无效果(Python 3.13 What's New,dis 小节)。
   本 demo §3 因此用"2 字节槽总数 − 结构化指令数"来数 CACHE 槽。
3. **跳转偏移的口径变过**:3.10 起参数是**指令偏移**而非字节偏移(dis 文档);
   3.12 起跳转目标按"CACHE 之后"计算 —— "the presence of the `CACHE` instructions is
   transparent for forward jumps but needs to be taken into account when reasoning about
   backward jumps."
4. **`EXTENDED_ARG` 会让 offset 与 start_offset 不一致**:`offset` 指向指令本身,
   `start_offset` 含前缀,算真实字节长度要用后者。
5. **oparg 是位域容器**:如 `LOAD_ATTR` 用最低位区分属性/方法加载,`LOAD_GLOBAL` 低位表示
   是否在全局前压入 `NULL`,`COMPARE_OP` 取 `cmp_op[opname >> 5]`。直接当索引用会错。
6. **改字节码很危险**(`show_caches=True` 的原文警告):
   "Populated caches can look like arbitrary instructions, so great care should be taken when
   reading or modifying raw, adaptive bytecode containing quickened data."

## 参考资料(实际阅读过的权威来源)

- [dis — Disassembler for Python bytecode](https://docs.python.org/3/library/dis.html)
  —— 指令编码、`EXTENDED_ARG`、`CACHE`、`Instruction` 全字段、栈语义、`stack_effect`
- [PEP 659 – Specializing Adaptive Interpreter](https://peps.python.org/pep-0659/)
  —— quickening / 自适应 / 专用化 / 去优化 / 附带数据 / `LOAD_ATTR` 与 `LOAD_GLOBAL` 家族 / 提速区间
- [What's New In Python 3.13(dis 小节)](https://docs.python.org/3/whatsnew/3.13.html)
  —— `get_instructions()` 不再单列 CACHE、新增 `cache_info`
- [CPython `Python/specialize.c`](https://github.com/python/cpython/blob/main/Python/specialize.c)
  —— PEP 659 参考文献中列出的专用化实现位置
