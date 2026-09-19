# Svelte 5 runes：编译式细粒度响应性

## 一、简介

Svelte 5 的 runes（`$state` / `$derived` / `$effect`）是**编译器关键字**而非库函数：它们以 `$` 前缀出现、看起来像函数调用，但不需要 import、不能赋值给变量、也不能当参数传递，只在特定位置合法（放错位置由编译器报错）。

本 demo 用 Python（+ Go 对照）复刻这套运行时的**最小骨架**，把三条容易含混的语义做成了可执行的判据：

1. `$derived` 是 **pull**（标脏 → 下次读取才重算），不是 push；
2. `$effect` 的重跑是**批处理**的，且**排在 DOM 更新之后**；
3. `$state` 的对象形式写入时**原对象永不被 mutate**。

代码：`svelte_runes.py`（模型）+ `selfcheck_svelte_runes.py`（自检，29 断言）+ `svelte_runes.go`（Go 对照）。

## 二、原理详解

### 2.1 三层订阅结构

| 层 | 对应 rune | 语义 |
| --- | --- | --- |
| `Source` | `$state` 的原子 | 一个值 + `version` + 订阅者集合；写入值相同则不推进 version |
| `Derived` | `$derived` | 依赖 = 表达式里**同步读到**的 state；上游变化只**标脏**，读取时才重算 |
| `Effect` | `$effect` | 挂载后运行；重跑走队列，按 `prio` 排序（模板/DOM 更新为 0，用户 effect 为 1） |

依赖收集靠一个「当前消费者」栈 `_cur`：任何 `read(src)` 都会把 src 记进栈顶消费者的依赖表，并**反向订阅**（`src.subs.add(consumer)`）。

### 2.2 pull 而非 push

```
n.set(2)        →  derived.recomputes 仍然不变（只置 dirty = True）
derived.get()   →  此时才真的重算，recomputes + 1
derived.get()   →  clean，不再重算
```

这条差别是 Svelte 与「每次 setState 就立刻重算全部 computed」的 push 模型的分水岭：没人读的 derived 永远不会被计算。

### 2.3 依赖是「这一次同步读到的」

`$derived` 的依赖不是编译期静态确定的全部变量，而是**本次求值实际读到的**那些槽位。所以分支切换后依赖集会变：

```python
dyn = Derived(lambda: read(a) if read(flag) else read(b))
```

- 走 `a` 分支时改 `b`：`dyn.dirty` 保持 `False`；
- `flag` 翻转后再求值：改 `a` 不再唤醒 `dyn`。

要让这条成立，重算前**必须先解绑旧依赖**（见 §六 坑 1）。

### 2.4 相等短路

`Derived.recompute()` 里只有 `new != old` 才推进 `version`。因此把值改出去又改回来（`3 → 4 → 3`），下游 effect **一次都不重跑**——先求值 derived、再比对 version 决定是否真跑 effect，这是 flush 的核心。

### 2.5 effect 的批处理与顺序

- 同一个 tick 内改两个 state，flush 后 effect 只重跑**一次**；
- 模板/DOM 更新 effect（`prio=0`）先于用户 effect（`prio=1`），对应官方「re-runs … happen after any DOM updates have been applied」；
- effect 可以返回 teardown；teardown **立即先于本次重跑**执行。

### 2.6 `$state` 的深代理且不 mutate 原对象

对象/数组形式返回 `StateProxy`：每个键惰性创建一个 `Source`（初值取自原对象），写入只改 `Source`，`_obj` 从头到尾只读。官方文档明确：*"When you update properties of proxies, the original object is not mutated."*

数组的 `length` 本身也必须是一个可订阅槽位，否则 `push` 后「按长度遍历」的读者收不到通知（见 §六 坑 2）。

## 三、与其它方案的对比

| | 依赖发现时机 | 更新粒度 | 触发方式 |
| --- | --- | --- | --- |
| Svelte 5 runes | 运行期同步读取（编译期生成读取代码） | 组件内单个表达式 | 标脏 + pull |
| Vue 3 `reactive` | 运行期 Proxy get | 组件级 render effect | push 调度 |
| React Hooks | 无依赖追踪，靠 `setState` 重跑整个组件 | 组件级 | push 全量重渲染 |
| Solid signals | 运行期读取 | 表达式级 | push（Memo 立即重算） |

Svelte 与 Solid 的差别就在于这一节：Solid 的 `createMemo` 是 push（上游变了立刻算），Svelte 的 `$derived` 是 pull。

## 四、环境

- Python 3.8+（无第三方依赖）
- Go 1.20+（仅做跨语言对照，本机无工具链时按 §六 人工审查）

## 五、运行方式

```bash
python selfcheck_svelte_runes.py
# svelte_runes: 29/29 assertions passed
```

## 六、注意事项与常见坑

1. **重算前必须解绑旧依赖。** 只清空正向依赖表、不清反向订阅表，会导致「已经切走的分支」仍被旧 Source 唤醒——表现为 derived 莫名变 dirty、effect 多跑。修复：`Consumer.unsubscribe_all()` 在每次重算/重跑开头执行。
2. **数组长度要单独建模成 Source。** 只给元素建 Source 的话，`push` 后 `range(len(arr))` 读到的还是旧长度，新元素永远不在依赖表里，断言「push 触发重算」必然失败。
3. **teardown 里读到的是已更新的值，不是旧值。** 官方只保证 teardown 在重跑之前运行，不保证它看到旧状态；需要旧值必须自己缓存。
4. **依赖只认同步读取。** `await` 之后的读取官方有专门转换（`$derived` 文档：state after `await` 也会被跟踪）；本 demo 未建模异步分支。
5. **解构即失去响应性。** `const { a } = obj` 拿到的是求值时刻的快照，官方文档明确写了这一点。

## 七、性能边界

- 依赖表规模 = 单次求值实际读到的槽位数；深代理每个键一个 Source，宽对象会线性增长 Source 数量。
- pull 语义下「没人读的 derived」零计算成本，但代价是读取路径上要做一次 `stale()` 扫描（O(依赖数)）。
- 相等短路能省掉整棵下游子树，但比较本身是 `!=`（深比较在对象场景可能不便宜）。

## 八、参考资料（实际读过）

- Svelte 官方文档 · What are runes? — https://cdn.jsdelivr.net/gh/sveltejs/svelte@main/documentation/docs/02-runes/01-what-are-runes.md
- Svelte 官方文档 · `$state`（深代理、原对象不被 mutate、解构非响应式）— https://cdn.jsdelivr.net/gh/sveltejs/svelte@main/documentation/docs/02-runes/02-$state.md
- Svelte 官方文档 · `$derived`（依赖 = 同步读取、标脏后下次读取重算）— https://cdn.jsdelivr.net/gh/sveltejs/svelte@main/documentation/docs/02-runes/03-$derived.md
- Svelte 官方文档 · `$effect`（microtask 运行、批处理、teardown 时机）— https://cdn.jsdelivr.net/gh/sveltejs/svelte@main/documentation/docs/02-runes/04-$effect.md
- 页面版（便于阅读）：https://svelte.dev/docs/svelte/what-are-runes
