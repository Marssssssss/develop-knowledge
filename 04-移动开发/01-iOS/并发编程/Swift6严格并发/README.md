# Swift 6 严格并发:Sendable / 隔离区 / 全局变量

Swift 5 的并发检查是「非 Sendable 跨 actor 一律报错」,误报多到很多人直接关掉。
Swift 6 换了一套**控制流敏感**的判据:**隔离区(isolation region)**。核心变化只有一句 ——
不再问「这个类型 Sendable 吗」,而是问「这个值在这一点上还能不能被别处摸到」。

本篇把三份提案的可判定部分落成可执行模型:`python/concurrency_regions.py` 建模隔离区与
Sendable 判定(42 条断言),`swift/ConcurrencyRegions.swift` 是同题 Swift 侧对照。

## 1. 两个正交的问题

| 问题 | 提案 | 判据 | 失败时的典型报错 |
| --- | --- | --- | --- |
| 类型能不能安全共享 | SE-0302 `Sendable` | 结构递归:成员全 Sendable | `non-sendable type ... passed in implicitly asynchronous call` |
| 某个值在这一刻能不能跨过去 | SE-0414 区域隔离 | 控制流数据流:值所在区域是否已并入别的隔离域 | `use of 'x' after transferring to another isolation domain` |
| 全局存储从哪访问都行吗 | SE-0412 全局并发 | 要么隔离到 global actor,要么「不可变 **且** Sendable」 | `reference to var 'x' is not concurrency-safe` |

**Sendable 是类型层面的充分条件,不是必要条件。** 一个非 Sendable 的类实例,只要编译器
能证明「传过去之后没人再碰它」,Swift 6 就放行 —— 这是 SE-0414 的全部价值。

## 2. 隔离区:定义与合并规则(SE-0414)

两个值 `x`、`y` 在程序点 `p` 属于**同一隔离区**,当且仅当:

1. `x` 可能与 `y` 互为别名;或
2. 通过 `y` 的属性的链式访问,可以触达到 `x`。

模型的合并规则直接照抄提案「Rules for Merging Isolation Regions」。给定 `y = f(a₀…aₙ)`:

1. 所有非 Sendable 实参的区域**合并**成一个大区域(保守假设:实现里任意参数都可能被另一个参数引用);
2. 若某个 `aᵢ` 非 Sendable 且 `y` 非 Sendable,则 `y` 落在同一个合并区;若 `aᵢ` 全是 Sendable,
   `y` 自己开一个新的 disconnected 区;
3. 若 `y` 是已有变量(可变):被闭包按引用捕获过 → 新旧区域**合并**(3a);否则**旧区域被遗忘**(3b)。

从这几条能推出全部日常现象(模型里逐条验过):

| 写法 | 结果 | 断言 |
| --- | --- | --- |
| `let y = x` | 同区域 | 02 |
| `let y = x.field` | 同区域(读属性等价于把 `x` 当 `self` 传给 getter) | 03 |
| `x.field = y` | `x` 与 `y` 合并 | 04 |
| 闭包捕获 `x`、`y` | `x`、`y`、**闭包本身**三者同区域 | 05 |
| `func f(x:…, y:…)` 函数体 | `x`、`y` 同区域(所以非 Sendable 的 `self` 会把所有参数拉进自己的区) | 06 |
| `x = y` 再 `x = z` | `y` 回到自己的区,`x` 与 `z` 同区 | 07 |
| `x = y` 但 `x` 被闭包按引用捕获 | 旧区与新区**合并**,不是被遗忘 | 08 |

## 3. 四类隔离区与「传递」

| 区域 | 能否传出本隔离域 | 说明 |
| --- | --- | --- |
| **disconnected** | 能,只要传出后本域不再用它 | `let x = NonSendable()` 的初始状态 |
| **actor isolated** | **永远不能** | 区域强绑定 actor 实例,传出去就会内外同时可访问 |
| **task isolated** | 不能(但**同 task 调用不算传递**) | 目前只出现在 nonisolated async 函数的参数上 |
| **invalid** | 任何使用都报错 | 条件控制流把两个互不相容的区域合到了一起 |

`python` 模型里对应三组断言:

* 传值给 `@MainActor` 函数后,**同一区域里的别名值**在调用方再使用 → `use-after-transfer`(10–12);
* `let x = await a.nonSendable` / `await transferToMainActor(a.nonSendable)` → `cannot-transfer-isolated`(13);
* `await nonIsolatedCallee(x)` 在 `nonisolated async` 函数里**不是传递**;同样的 `x` 传给
  `@MainActor` 才报错(14–15);
* `if { a1.use(x) } else { a2.use(x) }` 在汇合点得到 invalid 区域(16–17)。

**invalid 区域是很多「看不懂的报错」的来源**:单个分支都合法,合并之后静态上无法确定
`x` 属于哪个域,于是之后每一步都报错。

## 4. 弱传递:所有权没走,只是不能用了

传值跨隔离域用的是 **weak transfer**:调用方**仍然持有**这个值,只是不能再访问它。
所以 `deinit` 发生在**调用方作用域末尾**,而不是传递发生的那一刻(断言 18):

```
transfer ──► "After nonisolated callee" ──► "deinit was called"
```

提案明确解释了为什么不做 strong transfer:那会让所有 async 函数默认按 owned 取参数
(ABI 破坏 + ARC 开销 + 函数体再也不能标 `readonly`)。

两个推论,是迁移期最常撞到的:

* **`nonisolated async` 函数返回后,区域重新变回 disconnected**,可以再传一次(19–20)——
  因为它没有持久隔离状态,参数也不能再传出 task;
* 弱传递之后读 `Sendable` 字段:**类只放行 `let` 字段**(`var` 字段可能被别的域写,21–22);
  **值类型 + 不可变绑定则两个都放行**(按值传入,callee 改的是副本,23)。

## 5. Sendable 的判定表(SE-0302)

| 类型 | 结论 | 依据 |
| --- | --- | --- |
| actor | 隐式 Sendable | 自带同步 |
| 非 public / frozen public 的 struct、enum,成员全 Sendable | **隐式** Sendable | 省样板;public 非 frozen 不推导,因为会成为 API 契约 |
| 任意类型 | `@unchecked Sendable` 一律放行 | 作者自证 |
| final class 且全部存储属性为 `let` 且类型 Sendable | 允许 | 其余 class 只能 `@unchecked` |
| 泛型 `struct Y<T>` | 不 Sendable,也**不隐式产生条件一致** | 要自己写 `extension Y: Sendable where T: Sendable` |
| 泛型 `struct X<T: Sendable>` | 隐式 Sendable | 实例数据保证 Sendable |

还有一条容易被忽略:**`Sendable` 一致只能写在类型定义所在的源文件**(否则别人看不见
private 存储属性就能把 `MySneakyNSPerson` 变成 Sendable);`@unchecked` 例外(36–37)。

## 6. 全局变量(SE-0412)

严格并发下每个全局变量必须满足二者之一:

1. 隔离到某个 global actor;或
2. **不可变 且 Sendable**(两条缺一不可 —— 断言 41 专门验「可变但 Sendable 且未隔离」仍然报错)。

逃生舱是 `nonisolated(unsafe)`,它**只关闭静态检查**,不加任何同步;`@preconcurrency import`
把其他语言的全局变量降级为警告。提案还特意否掉了「隐式加锁」:
`global = global * 2` 这种读-改-写在锁里也不原子。

## 7. 运行方式

```bash
cd python && python selfcheck_concurrency_regions.py    # 42 条断言
```

Swift 侧 `swift/ConcurrencyRegions.swift` 只作人工对照(本机无 Swift 工具链),注释里
`// ERROR:` 的行与 Python 模型的判定一一对应。

## 8. 关键代码

```python
def transfer(env, key, target_domain):
    dom = env.domain_of(key)
    if dom == INVALID:
        return "invalid-region"
    if dom is not None and dom != target_domain:
        return "cannot-transfer-isolated"   # actor / task 隔离区不可传出
    env.region(key)["domain"] = target_domain
    return None
```

## 9. 性能与适用边界

* 区域隔离是**编译期数据流分析**,零运行时开销;代价是分析保守:任何函数调用都假设
  参数之间可能互为别名,所以「明明不相干的两个对象」也可能被并进一个大区域。
* 它是**过程内**分析为主,跨函数靠 `sending`/`transferring` 标注补精度(提案 Future Directions)。
* 模型只实现了可判定部分:`@unchecked`、`nonisolated(unsafe)`、`@preconcurrency` 这类
  「人工担保」在模型里一律视为放行,不模拟真实运行时的竞争。

## 10. 注意事项与常见坑

1. **「传完还能用」是错觉**:`await store.add(client)` 之后再 `client.xxx` 必报错,即使
   `add` 内部只是把值塞进数组而没改它 —— 判据是别名可能性,不是实际行为。
2. **`let` 不等于安全**:`let x = a.nonSendable` 里 `x` 混进了 `a` 的区域,在 `a` 之外
   根本用不了(actor isolated 区域)。
3. **闭包是区域放大器**:捕获两个非 Sendable 值就把它们俩和闭包绑成一个区域,之后
   传任何一个都会污染另外两个。
4. **`var` 重复赋值在 99% 的情况是「遗忘」**,但只要被闭包按引用捕获过就变成「合并」——
   这条规则是很多人「为什么还是报错」的答案。
5. **迁移期先修全局量**:`nonisolated(unsafe)` 是把炸弹交给运行时,不是修好。
6. 全局量规则里「不可变 **且** Sendable」是**且**:`let ns = NonSendable()` 这种
   不可变但非 Sendable 的全局量同样不合格。

## 参考资料

* SE-0414《Region based Isolation》
  <https://cdn.jsdelivr.net/gh/swiftlang/swift-evolution@main/proposals/0414-region-based-isolation.md>
* SE-0302《Sendable and @Sendable closures》
  <https://cdn.jsdelivr.net/gh/swiftlang/swift-evolution@main/proposals/0302-concurrent-value-and-concurrent-closures.md>
* SE-0412《Strict concurrency for global variables》
  <https://cdn.jsdelivr.net/gh/swiftlang/swift-evolution@main/proposals/0412-strict-concurrency-for-global-variables.md>
