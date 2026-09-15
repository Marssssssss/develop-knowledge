# 异常链(`__context__` / `__cause__`)与异常组(`except*`)

## 简介

- Python 3.0(PEP 3134 实际随 3.0 的 `__traceback__` 与 3.3 的 `raise from` 落地)引入**异常链**:
  在处理一个异常时又发生新异常,旧异常不再丢失,自动/手动挂在异常对象的属性上。
- Python 3.11(PEP 654)引入**异常组(ExceptionGroup)**与 `except*`:
  一次抛出**多个互不相干的异常**(如并发任务批量失败),并按类型分别处理。
- 关键概念:
  - `__context__`:隐式链,"处理 A 时发生了 B"(时间先后,不一定因果)
  - `__cause__`:显式链,`raise B from A` 声明"B 的直接原因是 A"
  - `__suppress_context__`:设了 `__cause__`(或 `from None`)后置 True,回溯不再显示旧异常
  - `ExceptionGroup` / `BaseExceptionGroup`:可嵌套的异常容器,表示一棵"异常树"
  - `except*`:对组做 `split`,每个子句拿到匹配叶子的**子组**,未匹配部分继续传播

## 原理详解

### 1. 隐式链 `__context__`(PEP 3134 四条语义)

1. 每个线程有一个异常上下文,初始为 `None`。
2. 抛出异常时,若实例尚无 `__context__`,解释器把它设为线程的异常上下文。
3. 抛出后立即,线程异常上下文 = 该异常。
4. 退出 `except` 块(自然结束 / `return` / `yield` / `continue` / `break`)时,
   线程异常上下文重置为 `None`。

```text
except A:            # 此时线程上下文 = A
    raise B          # B.__context__ = A;随后线程上下文 = B
# 退出后线程上下文重置 → 再抛 C 时 C.__context__ = None
```

### 2. 显式链 `raise ... from`(PEP 3134)与抑制(PEP 409/415)

- `raise B from A` 等价于 `B.__cause__ = A; raise B`,并隐含 `__suppress_context__ = True`。
- `raise B from None`:保留 `__context__` 但置 `__suppress_context__`,用于"翻译异常后隐藏底层细节"。
  (注:`__suppress_context__` 由 PEP 409 提出、PEP 415 定稿;PEP 3134 原文只把它列为开放问题。)
- 回溯两种链接消息(按 `__cause__` 优先遍历链):
  - `__cause__` 链 → `The above exception was the direct cause of the following exception:`
  - `__context__` 链 → `During handling of the above exception, another exception occurred:`
- `__traceback__` 是异常实例属性;注意它会在 异常→traceback→栈帧 之间制造引用循环,
  推迟局部资源释放(PEP 3134 中受到的最强烈反对)。

### 3. 异常组构造(PEP 654)

- `ExceptionGroup(message, excs)` 与 `BaseExceptionGroup(message, excs)`:
  两个仅位置参数;`ExceptionGroup` 装非 `Exception` 子类 → 构造期 `TypeError`。
- `BaseExceptionGroup` 若叶子全是 `Exception` 子类,**自动返回 `ExceptionGroup` 实例**。
- 组可嵌套 → 一棵树;根到叶的 traceback 段拼接才是叶子的完整路径(`leaf_generator` 算法)。

### 4. `subgroup` / `split` / `derive`

| 方法 | 语义 |
| --- | --- |
| `subgroup(cond)` | 同元数据、同嵌套结构,只留 `cond(exc)` 为真的叶子;**无匹配返回 `None` 而非空组**;条件匹配内部节点则整棵子树纳入 |
| `split(cond)` | 返回 `(match, rest)`;平凡分割时另一侧为 `None`;空嵌套组从结果剔除 |
| `derive(excs)` | `subgroup`/`split` 造新组的工厂;自定义组子类不重写会**丢子类类型**(按叶子类型决定组类型) |

### 5. `except*` 执行模型

1. 按顺序对不断缩小的 `unhandled` 组调 `split`;非平凡分割则执行子句体,
   `e` = 匹配叶子的**子组(保留嵌套结构)**。
2. 一个组可触发多个子句,但**每个子句至多执行一次**(一次拿到全部匹配叶子)。
3. 未匹配叶子自动 reraise 继续传播;裸异常被捕获时包装为 `ExceptionGroup('', [原异常])`,
   未匹配时保持裸形态传播。
4. 匹配判定 = 子类检查(与 `except` 相同);子句顺序敏感。
5. 子句体内禁止 `continue`/`break`/`return`(SyntaxError)——组内异常相互独立;
   `except*` 中 `raise e` 的异常**不会**被同一 try 的其他子句匹配。

## 对比 / 选型

| 维度 | `except` | `except*` |
| --- | --- | --- |
| 匹配对象 | 单个异常 | 组内按类型分拆叶子 |
| 未匹配行为 | 冒泡整个异常 | 只冒泡未匹配子组 |
| handler 执行次数 | 0 或 1 次 | 0..N 个子句各 1 次 |
| 混用 | 同一 try 内禁止(SyntaxError) | 同左 |
| `except ExceptionGroup` | 捕获组对象本身 | 反向:`except* ExceptionGroup` 是运行期 TypeError |

## 环境准备

- OS:任意(纯标准库)
- Python:**3.11+**(`except*`/`ExceptionGroup`);本 demo 实测 3.13.14
- 依赖:无

## 运行方式

```bash
python3 main.py
```

## 关键代码片段

```python
# 未匹配部分继续传播:KeyError 漏到外层
try:
    raise ExceptionGroup("eg", [ValueError(1), TypeError(2), KeyError(4)])
except* ValueError:
    ...          # 处理匹配的子组
except* TypeError:
    ...          # 每个子句至多一次
# → 外层 except ExceptionGroup 捕到仅含 KeyError 的 rest

# 自定义组:重写 __new__(PEP 654 建议),并加 __init__ 吞掉自定义参数
class MyGroup(BaseExceptionGroup):
    def __new__(cls, message, excs, code):
        self = super().__new__(cls, message, excs)
        self.code = code
        return self
    def __init__(self, message, excs, code):
        super().__init__(message, excs)
```

## 性能与边界

- `except*` 的分组处理是 O(子句数 × 叶子数) 级别;真正贵的通常是 traceback 采集本身。
- 组是"树":每次 `subgroup`/`split` 只在需要分割处新建组实例,叶子不拷贝,
  `__cause__`/`__context__`/`__traceback__` 按引用共享(PEP 654 原文)。
- 被捕获的 `e` 是**临时对象**(`raise e` / 修改它不会影响原组的形状)。

## 注意事项与常见坑

- **坑:以为 `except OSError` 能捕获含 OSError 的组**——不能,`except` 只看最外层类型。
- **坑:`except*` 里写 `return`** —— SyntaxError;想提前退出须改用标志位。
- **坑:子类没声明 `__slots__`-式的"重复父类 slot"**类比——组子类忘记重写 `derive` →
  `split` 结果静默退化为基类类型。
- **坑:自定义组只重写 `__new__` 不重写 `__init__`** → `MyGroup(...) takes no keyword arguments`
  (type.__call__ 仍会把原参数传给继承的 `__init__`;本 demo 开发期实测踩中)。
- `except* ExceptionGroup` 是**运行期** TypeError(本 demo 实测:仅在真有异常要匹配时触发),
  不是编译期 SyntaxError——与部分资料的粗略描述不同。

## 参考资料(实际阅读过的权威来源)

- [PEP 3134 – Exception Chaining and Embedded Tracebacks](https://peps.python.org/pep-3134/) —
  `__context__`/`__cause__`/`__traceback__` 的四条语义、链遍历与显示顺序原文。
- [PEP 654 – Exception Groups and except*](https://peps.python.org/pep-0654/) —
  为什么不能扩展 `except`、`split`/`subgroup`/`derive` 语义、`except*` 执行模型全文。
- [PEP 409 / PEP 415](https://peps.python.org/pep-0415/) — `__suppress_context__` 与 `from None` 的定稿。
