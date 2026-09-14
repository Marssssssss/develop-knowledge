# 结构化模式匹配(`match` / `case`,PEP 634)

## 简介

Python 3.10 引入的 `match` 语句:把一个**值**同时与**一组结构模板**比较,既能做类型/形状分派,
又能顺手把内部字段绑定到名字上。它替代的是"长串 `isinstance` + 下标/属性取值 + `if` 判断"。

- 一句话:**对数据做结构化解构 + 分派**,一层模式同时表达"类型对不对、形状对不对、取值域对不对"。
- 关键概念:
  - **subject**:`match` 后面的被测值
  - **模式(pattern)**:可反驳(可能失败,如字面量/类模式)或**不可反驳**(必然成功,如捕获/通配符)
  - **guard**:`case ... if 表达式`,模式成功后才求值,为假则继续试下一个 case
  - **绑定**:捕获模式把 subject 的某部分绑到名字上,绑定**活到 match 之外**
  - **`__match_args__`**:类模式位置子模式的属性名映射表(dataclass/namedtuple 自动生成)
- 历史:原提案 PEP 622(2020-06)因体积过大被拆分,最终以 PEP 634(规范)、PEP 635(动机)、
  PEP 636(教程)三份文档定稿,随 Python 3.10 发布。

## 原理详解

### 1. 语法(PEP 634 原文 BNF 节选)

```text
match_stmt: "match" subject_expr ':' NEWLINE INDENT case_block+ DEDENT
case_block: "case" patterns [guard] ':' block
guard: 'if' named_expression

pattern: as_pattern | or_pattern          as_pattern: or_pattern 'as' capture_pattern
or_pattern: '|'.closed_pattern+
closed_pattern: literal | capture | wildcard | value | group | sequence | mapping | class
```

`match` / `case` 是**软关键字** —— 只在 match 语句内被当作关键字,别处仍可作变量名。

### 2. 选择规则(逐条按顺序)

1. 计算 subject(仅一次)。
2. 从上到下逐 case 尝试匹配。
3. 某 case 的模式成功后,若有 guard 则求值该 guard(求值顺序:由第一个 case 到最后一个,
   跳过模式未全成功的 case;某 case 一旦被选中,guard 求值立即停止)。
4. **第一个"模式成功且 guard 为真"的 case 被执行**(原文:"the first case block whose patterns
   succeeds matching it *and* whose guard condition (if present) is 'truthy'");执行其 block。
5. 无 case 命中则整个 match 结束;guard 抛异常则异常向上冒泡。

### 3. 名称绑定

- 用 PEP 572 的作用域规则:绑到最近的函数作用域局部名(除非有 `nonlocal`/`global` by PEP 634)。
- 原文:"Name bindings made during a successful pattern match outlive the executed block and
  can be used after the match statement."
- **失败匹配**过程中的中间绑定**故意不作规定**:"User code ... should not rely on the bindings
  being made for a failed match, but also shouldn't assume that variables are unchanged."
- 同一模式内同一名字只能绑定一次(`case [x, x]` 是语法错误),但 `case [x] | x` 合法
  (OR 各分支必须绑定**相同**名字集合)。

### 4. 不可反驳(Irrefutable)与可达性

"由语法即可证明必然成功"的模式 = 不可反驳:捕获模式、通配符、左侧不可反驳的 AS 模式、
含至少一个不可反驳分支的 OR 模式、加括号的不可反驳模式。规则:
**至多一个不可反驳 case,且必须在最后** —— 违反时 CPython 报
`SyntaxError: wildcard makes remaining patterns unreachable`。

### 5. 各模式类型速查

| 模式 | 写法 | 成功条件 | 关键细节 |
| --- | --- | --- | --- |
| 字面量 | `case 0:` `case "a":` `case None:` | 数字/字符串用 `==`;**None/True/False 用 `is`** | 不支持 f-string;`3+4j` 这种复数写作 `signed_number ± NUMBER` |
| 捕获 | `case x:` | **总是成功** | `_` 不算捕获,单次绑定;同名不能重复 |
| 通配符 | `case _:` | 总是成功,**不绑定名字** | |
| 值 | `case Port.HTTPS:` | 点分名字查到的值与 subject `==` | 裸名字 `case HTTP_PORT:` 是**捕获**模式,不是值模式 |
| 组 | `case (p):` | 与 `p` 相同 | 仅用于加括号;`()` 是空序列模式 |
| 序列 | `case [a, *rest]:` | subject 是序列 且长度约束满足 | `[]` 与 `()` 语义相同;至多一个 `*`;`str`/`bytes`/`bytearray` **不匹配** |
| 映射 | `case {"k": v, **rest}:` | 每个键都存在且其值匹配 | 键用 `==`;值用 **`get()` 两参数形式**取;至多一个 `**` 且必须在最后;不允许重复键 |
| 类 | `case Point(x=0, y=y):` | `isinstance(subject, Point)` 且各属性匹配 | 关键字=属性查找(`AttributeError` → 模式失败);位置子模式经 `__match_args__` 转换 |
| OR | `case 1 \| "a":` | 任一分支成功 | 只有**最后一个**分支可以不可反驳;各分支绑定名字集合必须相同 |
| AS | `case p as name:` | 左侧成功 | 右侧不能是 `_` |

**序列模式的类型判定**:继承/注册 `collections.abc.Sequence`,或设了 `Py_TPFLAGS_SEQUENCE` 位
(标准库中为 `array.array`、`collections.deque`、`list`、`memoryview`、`range`、`tuple`)。
长度经内置 `len()` 取得:
- 定长模式:`len(subject) == 子模式数`,否则失败;
- 变长模式:`len(subject) >= 非星号子模式数`;星号分支绑定剩余项组成的 `list`。

**映射模式的类型判定**:`collections.abc.Mapping` 或 `Py_TPFLAGS_MAPPING`(标准库: `dict`、
`mappingproxy`)。因为用 `get()` 两参数形式取值,**`defaultdict` 的 `__missing__` 不会被触发**。

**类模式的 `__match_args__` 转换**(PEP 634 §Class Patterns):
1. 位置子模式数多于 `len(__match_args__)` → `TypeError`;
2. `__match_args__` 不是元组 → `TypeError`;
3. 位置子模式 `i` 用 `__match_args__[i]` 当关键字,若它不是字符串 → `TypeError`;
4. 转换后与显式关键字重名 → `TypeError`;
5. 具名元组与 dataclass **自动生成** `__match_args__`(dataclass 中 `init=False` 的字段被排除);
6. `bool/bytearray/bytes/dict/float/frozenset/int/list/set/str/tuple` 这 11 个内建类型接受
   **单个**位置子模式匹配整个 subject,等价于:

   ```python
   class C:
       __match_args__ = ("__match_self_prop__",)
       @property
       def __match_self_prop__(self):
           return self
   ```

### 6. 副作用

显式副作用**只有名称绑定**。匹配也依赖属性访问、`isinstance`、`len()`、`__eq__` 与元素访问,
但"具体调用了哪些方法/调用几次"是**未定义**的:"This proposal intentionally leaves out any
specification of what methods are called or how many times."

## 对比 / 选型

| 方案 | 表达力 | 可读性 | 典型场景 |
| --- | --- | --- | --- |
| `match` / `case` | 结构 + 类型 + 取值域 + 绑定,一层写完 | 高(声明式) | 报文/事件校验、AST 变换、命令解析 |
| `if` / `elif` + `isinstance` | 同上但需手写取值与临时变量 | 中(命令式,易漏分支) | 分支少、条件不规整 |
| `dict` 分发 | 只能按单一键值分派 | 高 | 纯"命令名 → 处理函数" |
| 访问者模式 | 最强(可扩展行为) | 低(类爆炸) | 需要双向扩展的编译器 AST |

## 环境准备

- 操作系统:任意
- 语言版本:**Python 3.10+**(本 demo 实测 3.13.14)
- 依赖:仅标准库(`enum` / `dataclasses` / `collections`)

## 运行方式

```bash
python pattern_matching.py
```

## 关键代码片段

嵌套模式一次完成"类型 + 形状 + 取值域"三层校验,无需中间变量与 try/except:

```python
match event:
    case {"type": "click", "pos": (int(x), int(y))} if x >= 0 and y >= 0:
        return f"click at ({x}, {y})"                  # 映射 + 序列 + 类模式 + guard
    case {"type": "key", "code": str(code), "mods": [*mods]}:
        return f"key {code} + modifiers {mods}"        # *mods 绑定剩余元素为 list
    case {"type": str(kind), **rest}:
        return f"未支持的事件 {kind},附带 {sorted(rest)}"   # **rest 收集剩余键
```

用 `compile()` 观测编译器强制的规则(节选自 §5):

```python
compile("match 1:\n case _:\n  pass\n case 2:\n  pass\n", "<p>", "exec")
# SyntaxError: wildcard makes remaining patterns unreachable
compile("match {}:\n case {'a': 1, 'a': 2}:\n  pass\n", "<p>", "exec")
# SyntaxError: mapping pattern checks duplicate key ('a')
```

## 性能与边界

- 位置子模式用 `==`(值相等)比较,不是恒等;因此 `case [1, 2]` 不会匹配 `[True, 2]` 之外的
  奇怪相等关系,但会匹配任何"等于 1 和 2"的对象(自定义 `__eq__` 会被调用)。
- 类模式每个关键字子模式做一次 `getattr`;`isinstance` 前置保证不会对无关对象取属性。
- 边界:模式语法在 3.10 定稿后未再扩展,新增能力主要来自标准库(如 dataclass 的
  `__match_args__` 生成);PEP 622 中曾被讨论的守卫赋值等特性未进入 3.10。

## 注意事项与常见坑

1. **`True == 1` 的顺序陷阱**:`case 1:` 会先吃掉 `True`(数字字面量用 `==`),
   要区分布尔与整数必须把 `case True:` 放在 `case 1:` 之前 —— 因为 `None/True/False` 用 `is`。
2. **裸名字是捕获模式**:`case HTTP_PORT:` 不会比较常量,而是把 subject **绑定**到该名字,
   永远成功。要比较常量必须写**点分名字**(`Port.HTTPS`、`mod.HTTP_PORT`)。
3. **映射模式不触发 `__missing__`**:用 `get()` 两参数形式取值,所以 `defaultdict` 不会被
   偷偷写入新键;反过来,期望"取值即建键"的代码会失效。
4. **`case _` / 捕获模式放中间会让后续分支永不可达**,CPython 直接报语法错误。
5. **失败匹配的中间绑定不可依赖**(规范明说 left unspecified),分支里不要读"上一次循环残留"的绑定。
6. **序列模式不匹配 `str` / `bytes` / `bytearray`**,虽然它们有 `__len__` 和下标;
   需要匹配字符串请用字面量/类模式。
7. **`__match_args__` 必须是元组**,列表会 `TypeError: Bad.__match_args__ must be a tuple`。
8. 别在 guard 里做有副作用的操作:guard 的求值次数与顺序在嵌套 OR 下不保证可预测。

## 参考资料(实际阅读过的权威来源)

- [PEP 634 – Structural Pattern Matching: Specification](https://peps.python.org/pep-0634/)
  —— 语法 BNF、选择规则、不可反驳定义、各类模式语义、`__match_args__` 转换规则、
  序列/映射的 `Py_TPFLAGS_*` 判定、副作用未定义声明
- [The match statement(Python 语言参考)](https://docs.python.org/3/reference/compound_stmts.html#match)
  —— PEP 634 的现行权威版本(官方标注 PEP 为历史文档)
- [PEP 635 – Structural Pattern Matching: Motivation and Rationale](https://peps.python.org/pep-0635/)
  —— 与 `if`/`elif`、字典分发、访问者模式的对比动机(本 README 对比表依据)
- [PEP 636 – Structural Pattern Matching: Tutorial](https://peps.python.org/pep-0636/)
  —— 模式逐类教学示例
