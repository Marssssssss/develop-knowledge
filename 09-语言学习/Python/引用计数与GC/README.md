# 引用计数、分代 GC 与弱引用(CPython 内存管理)

## 简介

CPython 的内存回收是**两层机制**:**引用计数**负责绝大多数对象的即时释放(归零就归还内存),
**分代循环 GC** 兜住引用计数管不了的那一类 —— **引用循环**。`weakref` 提供"持有引用但不保活"的能力,
是缓存、观察者、终结清理的基础设施。

- 一句话:引用计数管"没人要的",循环 GC 管"互相要但外面没人要的",weakref 管"想要但不想管生死"。
- 关键概念:
  - **引用计数(refcount)**:对象头的计数器,赋值/传参/入容器等操作触发 `+1/-1`
  - **immortal 对象**:PEP 683 的"永不释放"标记,refcount 恒为 `0xFFFFFFFF`
  - **循环垃圾**:互相引用且外部不可达,refcount 永不归零;**分代(0/1/2)**:按活过多少次收集分层
  - **终结器(finalizer)**:`__del__` 或 `weakref.finalize`,回收前的清理钩子
- 历史:引用计数从 CPython 诞生就有;循环 GC 在 2.0 加入(分代);PEP 442(3.4)让带
  `__del__` 的对象也能被循环回收;PEP 683(3.12)引入 immortal 对象。

## 原理详解

### 1. 引用计数

- 每个对象头有 `ob_refcnt`;`sys.getrefcount(obj)` 可观测,但**比直觉多 1**:
  "The count returned is generally one higher than you might expect, because it includes the
  (temporary) reference as an argument to `getrefcount()`."
- 还有一类**不可靠**情况:immortal 对象(单例 `None`/`True`/`False`、小整数、驻留字符串、
  静态类型对象等)。sys 文档(3.12 起):"some objects are immortal and have a very high
  refcount that does not reflect the actual number of references. Consequently, do not rely
  on the returned value to be accurate, other than a value of 0 or 1."
- 本机实测(3.13.14):`sys.getrefcount(1)` 与 `sys.getrefcount(None)` 均为 `4294967295`
  (`0xFFFFFFFF`)—— 正是 PEP 683 的 immortal 标记;普通 `list` 随别名增删呈 2 → 3 → 2,如实反映。

### 2. 归零即释放

refcount 减到 0 时**同步**释放(立刻调用 `__del__` 并归还内存),不需要 GC 参与。但这是 **CPython 实现细节**而非语言保证 ——
PyPy/Jython 时机完全不同,所以"用 `__del__` 保证资源释放"不可移植,应改用 `with` / `try-finally` / `weakref.finalize`。

### 3. 引用循环:GC 的存在理由

```text
a.peer = b; b.peer = a; del a, b
  a.refcnt: 2 -> 1(外部引用没了,但 b.peer 还指着)
  b.refcnt: 2 -> 1(同理)
  => 两个对象都不可达,refcount 却都不为 0 -> 引用计数彻底失效
```

gc 文档明说二者关系:"Since the collector **supplements the reference counting** already used
in Python, you can disable the collector if you are sure your program does not create
reference cycles." 循环 GC 的可达性分析天然能处理环。

### 4. 分代与阈值

- 原文:"New objects are placed in the youngest generation (generation `0`). If an object
  survives a collection it is moved into the next older generation. Since generation `2` is
  the oldest generation, objects in that generation remain there after a collection."
- 触发逻辑:"When the number of allocations minus the number of deallocations exceeds
  *threshold0*, collection starts. Initially only generation `0` is examined. If generation
  `0` has been examined more than *threshold1* times since generation `1` has been examined,
  then generation `1` is examined as well."
- **默认值别硬编码**:长期被引用的默认是 `(700, 10, 10)`,但本机 3.13.14 实测
  `gc.get_threshold()` = `(2000, 10, 10)`(3.13 为配合增量 GC 上调过 threshold0,
  功能回退后阈值保留)。`threshold2` 语义也在 3.14 ↔ 3.14.5 之间反复(先忽略后恢复)。
- `gc.set_threshold(0, ...)` 的文档语义:"Setting *threshold0* to zero disables collection."

### 5. gc 模块接口速查

| 接口 | 作用 |
| --- | --- |
| `collect(generation=2)` | 手动收集(0/1/2 指定代数),返回"回收 + 不可回收"对象数 |
| `enable` / `disable` / `isenabled` | 开关自动收集 |
| `get_threshold` / `set_threshold` | 读/写三代阈值 |
| `get_count` → `(count0, count1, count2)` | 各代当前计数 |
| `get_stats` | 每代累计 `collections` / `collected` / `uncollectable` |
| `is_tracked(obj)` | 是否被 GC 追踪(原子类型不追踪;空 `{}` 也不追踪) |
| `is_finalized(obj)` | 是否已执行过终结器(3.9+) |
| `callbacks` | 收集前后回调 `(phase, info)`,含 generation/collected/uncollectable |
| `freeze` / `unfreeze` | 移入"永久代",不再被扫描(适合启动后不变的缓存) |
| `get_referrers` / `get_referents` | 反查引用者/被引用者(调试泄漏);`garbage` 为不可回收列表,`DEBUG_LEAK = COLLECTABLE\|UNCOLLECTABLE\|SAVEALL` |

### 6. PEP 442(3.4):安全终结

3.4 之前"带 `__del__` 的对象不能参与循环回收",会泄漏进 `gc.garbage`。PEP 442 把收集流程改为五步:

```text
① 清空循环隔离集(CI)内对象的弱引用,并调用它们的回调   <- 此时对象仍然可用
② 调用 CI 内所有对象的终结器                          <- 新增步骤
③ 重新遍历 CI 判断是否仍"隔离":若已有对象被外部可达(复活),放弃本次收集
④ 把 CI 变成循环垃圾(CT):用 tp_clear 系统性断开内部引用
⑤ 结束(所有 CT 对象在第 ④ 步的副作用中已经释放)
```

- C 层:类型新增 `tp_finalize` 槽(仍用 `tp_del` 的 C 扩展走旧路径并落入 `gc.garbage`);
  GC 头部保留一位标记"已终结",避免重复调用。
- 保证:"an object's finalizer is always called exactly once, even if it was resurrected
  afterwards." 但对 CI 内对象,**终结器调用顺序未定义**。
- 收益:"objects with a `__del__()` method don't end up in `gc.garbage` anymore."(gc 文档)

### 7. weakref:不保活的引用

| 接口 | 语义要点 |
| --- | --- |
| `ref(obj[, cb])` | 调用 `ref()` 取原对象,referent 已死则返回 `None`;回调在"即将被终结"时调用 |
| `proxy(obj)` | 透明代理,免解引用;**不可哈希**;referent 死后访问属性抛 `ReferenceError` |
| `WeakValueDictionary` | 值为弱引用,值无强引用时条目自动丢弃(见 demo 的缓存例子) |
| `WeakKeyDictionary` | 键为弱引用;注意"等值但非同一"键的替换陷阱 |
| `WeakSet` | 元素为弱引用;`WeakMethod` 用于绑定方法(临时对象,普通 `ref` 留不住) |
| `finalize(obj, func, *a, **kw)` | 比 `__del__` 更可靠;**保证存活到对象被回收**;支持 `detach()`/`alive`/`peek()`/`atexit` |

要点摘录:
- "A weak reference to an object is not enough to keep the object alive" —— 弱引用不改变存活条件。
- 回调顺序:"from the **most recently registered callback to the oldest registered callback**"
  (本 demo 实测 `['r2', 'r1']`);回调异常"cannot be propagated",只打印到 stderr。
- 存活检查要写成 `ref() is not None` 一次取值,分开的"先查后取"会造成竞态。
- 支持弱引用的类型:"class instances, functions written in Python (but not in C), instance
  methods, sets, frozensets, some file objects, generators, type objects, sockets, arrays,
  deques, regular expression pattern objects, and code objects."
- `list`/`dict` **本身不支持**但**子类化**后可以;`tuple`/`int` 即使子类化也不支持。
- 定义 `__slots__` 时必须显式加 `'__weakref__'`,否则实例不可被弱引用。

## 对比 / 选型

引用计数 vs 追踪式 GC(PyPy/Java):前者归零即回收、时机可预测,但需额外机制处理循环,且每次
赋值都要改计数;后者天然处理循环,但要停顿(或并发标记)并维护额外标记位。

| 方案 | 触发时机 | 推荐场景 |
| --- | --- | --- |
| `__del__` / `weakref.finalize` | 前者在 refcount 归零或循环回收时触发("notoriously implementation specific");后者保证存活到对象被回收,退出时按创建逆序调用 | 简单内部对象用 `__del__`;清理外部资源、给第三方挂钩子用 `finalize` |
| `with` / `try-finally` | 显式作用域结束 | **首选:最可预测** |

## 环境准备

- 操作系统:任意(refcount/GC 是 CPython 特性);语言版本 Python 3.9+(`gc.is_finalized`,实测 3.13.14);依赖仅 `gc`/`sys`/`weakref`

## 运行方式

```bash
python refcount_and_gc.py
```

## 关键代码片段

引用循环的观测点(显式关掉自动回收保证可复现;弱引用只做观测,不阻止回收):

```python
gc.disable()
a, b = Node("a"), Node("b"); a.peer, b.peer = b, a   # 互指,refcount 各为 2
watch = weakref.ref(a)
del a, b                        # 外部引用消失 -> refcount 各降为 1,不释放
assert watch() is not None
gc.collect()                    # 循环 GC 打破环
assert watch() is None; gc.enable()
```

PEP 442 的"复活"验证 —— 终结器把 `self` 挂到全局列表,整组对象因外部可达而保留:

```python
class Resurrector:
    registry = []
    def __del__(self):
        type(self).registry.append(self)          # 复活
gc.collect()
assert Resurrector.registry and gc.is_finalized(Resurrector.registry[0])
Resurrector.registry.clear(); gc.collect()
assert Resurrector.calls == 2                      # 恰好终结一次,不再重复
```

## 性能与边界

- 循环 GC 的成本与**被追踪的容器对象数量**成正比,与总对象数无关(原子类型不入追踪集)。
- 阈值太大 → 循环垃圾与 fd 等资源延迟释放,常驻内存升高;太小 → 反复扫描,CPU 上升。
  文档对调参的态度是按负载实测,并提供 `gc.freeze()` 把启动后不变的对象移出扫描范围。
- **全量收集会清空若干内建类型的 free list**:文档原文 "The free lists maintained for a number
  of built-in types are cleared whenever a full collection or collection of the highest
  generation (2) is run. Not all items in some free lists may be freed ...(in particular
  `float`)" —— 极端场景下 `gc.collect()` 会让内存占用短暂上升。
- 收集进行中再调用 `gc.collect()`,"The effect ... is undefined"。

## 注意事项与常见坑

1. **不要把 `(700, 10, 10)` 写死**:3.13 实测 `(2000, 10, 10)`;要调参请基于 `gc.get_threshold()`。
2. **`getrefcount` 不可当"引用数"用**:多 1 是传参临时引用;immortal 对象直接 `0xFFFFFFFF`。
   文档只允许依赖 0 或 1。
3. **别用 `__del__` 做关键清理**:解释器关闭时行为按实现而定;`weakref` 文档还提醒,在**守护线程**
   里创建 finalizer 可能**不会被调用**。清理外部资源用 `with` 或 `finalize`。
4. **弱引用存活检查要一次取值**:`if r() is not None: obj = r()` 分成两步在多线程下有竞态。
5. **`WeakKeyDictionary` 的等值键陷阱**:用"值相等但身份不同"的键赋值会**替换值但不替换键**,
   原键一删整个条目消失;文档给的绕法是先 `del d[k1]` 再赋新键。
6. **`proxy` 不可哈希**,别拿去当 dict 键;referent 死后访问属性抛 `ReferenceError`。
   **`__slots__` 默认与弱引用互斥**:忘了写 `'__weakref__'` 会得到 `TypeError: cannot create weak reference to ...`。
7. **`finalize` 的 `func/args` 不能引用被观测对象**(直接或间接),否则对象永不回收 ——
   文档特别强调"*func* should not be a bound method of *obj*"。

## 参考资料(实际阅读过的权威来源)

- [gc — Garbage Collector interface](https://docs.python.org/3/library/gc.html) — 分代定义与阈值语义、`collect`/`get_stats`/`is_tracked`/`callbacks`/`freeze`、`garbage` 与 `DEBUG_*`
- [weakref — Weak references](https://docs.python.org/3/library/weakref.html) — 弱引用语义、回调顺序与异常、各类弱容器、`finalize` 全接口、支持弱引用的类型与 `__slots__` 规则
- [sys — `getrefcount` / `getrecursionlimit` / `getswitchinterval`](https://docs.python.org/3/library/sys.html) — "+1" 的解释与 3.12 immortal 变更说明
- [PEP 442 – Safe object finalization](https://peps.python.org/pep-0442/) — 五步收集顺序、`tp_finalize`、复活与"恰好一次"保证、CI 内顺序未定义
- [PEP 683 – Immortal Objects, Using a Fixed Refcount](https://peps.python.org/pep-0683/) — immortal 标记、单例/小整数/静态类型成为 immortal、refcount 观测值变化
- [Python 3.13 What's New(gc 小节)](https://docs.python.org/3/whatsnew/3.13.html) — 增量 GC 及其回退、threshold 语义变动背景
