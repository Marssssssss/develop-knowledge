# Signals — push-pull 细粒度响应式

## 简介

Signals 是 2024 年起由 **Angular / Preact / Solid / Svelte / Vue / MobX / Qwik / Ember / RxJS** 等维护者共同推进的 TC39 标准提案(当前 **Stage 1**,提案自称"可视为 Stage 0")。它就是各大框架内部那套"自动依赖追踪 + 按需重算"机制的标准化的版本。

本 demo 按提案算法描述实现完整语义(零依赖,~500 行):

| 组件 | 内容 |
| --- | --- |
| `Signal.State<T>` | `get()` / `set()`,可自定义 `equals`(默认 `Object.is`),`sinks` 有序集合 |
| `Signal.Computed<T>` | 四状态机 `~clean~ / ~checked~ / ~computing~ / ~dirty~`,惰性 + 记忆化 + 错误缓存 |
| `Signal.subtle.Watcher` | 三状态机 `~waiting~ / ~watching~ / ~pending~`,同步 `notify`,只读的 `getPending()` |
| `Signal.subtle` | `untrack` / `currentComputed` / `introspectSources` / `introspectSinks` / `hasSinks` / `hasSources` |

一句话结论:**state 变化只"推"脏标记,值只在被读时"拉"出来算** —— 这就是 push-pull。

## 原理详解

### 1. 为什么不纯 push、也不纯 pull

官方 FAQ 原文:

> "Evaluation of computed Signals is **pull-based**: computed Signals are only evaluated when `.get()` is called... At the same time, changing a State signal may immediately trigger a Watcher's callback, 'pushing' the notification. So Signals may be thought of as a **'push-pull' construction**."

- **纯 push**(立即重算全部下游):写一次状词会触发整条链路重算,而下游可能根本没人读 → 浪费;更糟的是**读到不一致的中间态**(glitch)。
- **纯 pull**(每次读都重算):没有缓存,等于没有记忆化。
- **push-pull**:`set()` 同步、只传播**脏标记**;`get()` 时按需、按拓扑序重算。

配套的官方性质:

| 性质 | 含义 |
| --- | --- |
| Laziness | 声明时不求值,依赖变化时也不立即求值 |
| Memoization | 依赖未变则重复读不重算 |
| Glitch-free | 引入拓扑排序,**永不做"无谓且会读到陈旧值"的计算** |
| Lossy | 连续两次写,第一次"丢失"(Signal 是"当前值的单元格",不是时间流) |
| 同步性 | `set()` 立即生效,`get()` 立即返回;`notify` 同步执行 |

### 2. Computed 的四状态机(提案转换表)

```
              set(直接State源)         求值确认全部直接源未变
  clean ──────────────► dirty      checked ──────────────► clean
    │                     ▲            ▲                      │
    │ set(间接State源)     │ 直接Computed源重算后值变了          │
    └────► checked ───────┘            │                      │
                                      └──────────────────────┘
   dirty ──(要执行回调)──► computing ──(回调结束,无论返回/抛出)──► clean
```

关键差异:**直接源**变化 → 直接 `dirty`;**间接源**(隔着至少一层 Computed)变化 → 只标 `checked`,因为"可能是陈旧的"还需要下一步确认。`checked` 的确认过程就是拓扑排序的实现 —— 逐个读取直接源,若某个 Computed 源重算后值变了,自己立刻转 `dirty`。

### 3. glitch-free 的实测

菱形依赖 `a → (b, c) → d`,`a` 从 1 改成 2:

| 实现 | 重算次数 | d 观察到的值 | 是否正确 |
| --- | --- | --- | --- |
| 本实现(push-pull) | 3 次(Δb=1, Δc=1, Δd=1) | `[10]` | ✅ 无中间态 |
| 纯 push 基线 | 4 次(d 被推 2 次) | `[7, 10]` | ❌ 出现 `7 = 新b(4) + 旧c(3)` |

`7` 就是 glitch:它对应的状态在真实世界里从未存在过。官方 FAQ 指出这种"不准确的中间值曾被展示给用户"正是 push 模型的根本问题。

### 4. Watcher 的三状态机与 frozen 语义

```
waiting ──watch()──► watching ──依赖变化──► pending ──notify跑完──► waiting
   ▲                                                                  │
   └────────────────── unwatch() 掉到零个被监视信号 ────────────────────┘
```

- `notify` **同步**执行(在 `set()` 内部,图着色完成后),不排微任务 —— 原因见官方 FAQ:"callback 不能读写 Signal,所以同步调用不会破坏一致性;而为每种回调开一个微任务是昂贵且不必要的"。
- `notify` 执行期间 **`frozen = true`**,读/写任何信号都会抛错(本 demo 断言了这一点)。
- 多个 watcher 依次执行,**异常聚合为 `AggregateError`** 后再抛给调用者,不中断其余 watcher。
- `getPending()` 只返回 `dirty`/`checked` 的 **Computed**,不含 State(State 的当前值永远是最新的)。
- 首个 sink 出现/最后一个 sink 消失会触发 `[Signal.subtle.watched]` / `[Signal.subtle.unwatched]` 回调,并**递归向上**传播 —— 这是框架做"惰性图提升"(只在被监视时才维持上层节点)的钩子。

### 5. 与 Vue / Solid / Svelte 的关系

同一套算法各自有 API 皮肤:Vue 的 `ref` / `reactive`(Proxy 包装 + 组件级 effect)、Solid 的 `createSignal`、Svelte 5 的 `$state` / `$derived`(编译器改写)、Angular 的 `signal()`。**提案只标准化"图 + 状态机"这一层,故意不包含 `effect()`** —— 因为 effect 绑定调度与销毁,属框架职责;提案只给 `subtle.Watcher` 作为实现 effect 的基座。

## 对比

| 机制 | 粒度 | 追踪方式 | 失效策略 |
| --- | --- | --- | --- |
| Signals | 单个数据单元格 | 读时自动收集 | push 脏标记 + pull 求值 |
| Vue `reactive` | 对象属性(Proxy) | 读时自动收集 | 同上(依赖 effect 调度) |
| 虚拟 DOM diff | 组件子树 | 运行时全树比对 | 每次渲染全量比对 |
| 手动订阅(Redux) | store 整体 | 显式 `subscribe` | 选择器比较 |

## 环境

- Node.js ≥ 14(本地 22.22.2 实测),零第三方依赖
- TS 版未编译(本机无 tsc);用字面量联合类型表达两个状态机

## 运行方式

```bash
cd 02-Web开发/01-前端框架/Signals/js
node signals_check.js     # 46 项断言,全部通过时 exit 0
```

## 关键代码

```js
// 推:只标脏,不重算
const visit = (node, direct) => {
  for (const sink of node.sinks) {
    if (sink instanceof Computed) {
      if (direct) { if (sink.state !== DIRTY) sink.state = DIRTY; }   // 直接源 → dirty
      else if (sink.state === CLEAN) sink.state = CHECKED;            // 间接源 → checked
      visit(sink, false);
    } else {
      if (sink.state === W_WATCHING) sink.state = W_PENDING;
      watchers.push(sink);
    }
  }
};

// 拉:checked 的确认过程即拓扑排序
if (this.state === CHECKED) {
  for (const src of [...this.sources]) { src.get(); if (this.state === DIRTY) break; }
  if (this.state === CHECKED) this.state = CLEAN;
}
if (this.state === DIRTY) this._recompute();
```

## 性能边界

- **`sinks` 用有序 `Set`**:依赖顺序可观察(读顺序影响 `sources` 顺序),但也意味着遍历成本随依赖数线性增长
- **`checked` 确认是 O(源数) 的额外读操作**:层数越深,"确认没变"的代价越接近"重算一遍" —— 这是 `checked` 状态存在的意义(避免无谓重算),代价是确认开销
- **`set()` 是同步传播**:一次写可能触发整条链的脏标记;若图极大,建议在外层做批量(提案刻意不做内置 batching)
- **错误也被缓存**:抛错的 computed 每次读都重抛,直到依赖变化 —— 避免"每次读都触发一次失败计算"
- **无内置自动 GC 保证**:未被引用的 computed 应可被回收,但 Watcher 必须 `unwatch`(长生命周期组件里漏掉就是内存泄漏)
- **frozen 期间禁止读写**:所以 `notify` 里不能"顺手读一下最新值",必须等回调返回后再读 —— 这是调度器的责任

## 注意事项与常见坑

- **错误缓存的对象同一性**:缓存的是错误**对象**(重抛同一引用),不是新建的包装错误;本 demo 断言了 `e1 === e2 === boom`
- **`untrack` 必须 `finally` 还原**:回调抛错时不还原会让整个后续依赖图错乱
- **`sources` 必须有序**:提案明确"读顺序可观察",用无序集合会让 `equals` / `watched` 回调的调用顺序变得不确定
- **`computing` 必须按栈式还原**(`outer` 保存/恢复):嵌套 computed 求值时归因于**最内层**那个
- **动态依赖要重连边**:分支切换后,旧的源上必须摘掉自己,否则旧分支变化仍会触发重算(本 demo 专门断言)
- **提案字面语义有个坑**:`set()` 传播时若只把 **clean → dirty** 的直接 sink 标脏,那么"先被间接源标成 `checked`、随后其**直接** State 源又变化"的节点会停在 `checked`,确认阶段收敛为 `clean` 并**返回陈旧值**。本 demo 用 `config.directCheckedToDirty` 开关复现了这个 bug(得到 11,正确应为 12),并在实现里把 `checked` 一并置 `dirty`
- **环形依赖必须显式检测**:`computing` 状态读到自身要抛错,否则栈溢出
- **不要用 `Object.is` 比较新建的对象字面量**:每次 `set({...})` 都是新引用,必然传播 —— 需要结构化比较时用 `equals`

## 参考资料

实际读过:

- [tc39/proposal-signals](https://github.com/tc39/proposal-signals) —— API 表面(`Signal.State` / `Signal.Computed` / `Signal.subtle.Watcher` / `SignalOptions`)、push-pull 构造与全部 FAQ 原文、Computed 四状态转换表、Watcher 三状态转换表、失效传播的逐步算法(`set` 的第 4–6 步)、glitch-free / lossy / memoization 的官方定义
- [Signals, the push-pull based algorithm — Willy Brauner](https://willybrauner.com/journal/signal-the-push-pull-based-algorithm) —— 用全局 `STACK` 实现自动依赖收集,以及 dirty flag 如何在 pull 阶段完成缓存与重算的推导
- [JavaScript Signals: The Reactivity Primitive Coming to the Web Platform](https://apnahive.com/javascript-signals-the-reactivity-primitive-coming-to-the-web-platform) —— push-then-pull 的定位、各框架 signal 实现的收敛、与 Promises/A+ 的类比
- [Beyond Framework Silos: Native TC39 Signals](https://idea2dev.com/en/post/beyond-framework-silos-architecting-tomorrows-web-with-native-tc39-signals) —— `Signal.subtle.Watcher` 实现 effect 的写法与 `Symbol.dispose` 清理模式
