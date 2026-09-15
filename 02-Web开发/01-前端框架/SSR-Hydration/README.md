# SSR 流式渲染 + 选择性水合

## 简介

React 18 把 SSR 从"整页渲染完再发送"改成**边渲染边发送**:慢区域先给占位,数据到了再推一段。客户端则以**边界为单位**独立水合,用户点到哪块就先水合哪块。

本 demo 用虚拟时间线把服务端与客户端两侧都跑一遍(零依赖,~390 行),量化三件事:

| 问题 | 实测结论 |
| --- | --- |
| 首个字节能多早? | 流式 **5ms** vs 非流式 **300ms**(慢数据 300ms 时) |
| 首个边界可交互能多早? | 流式 **220ms** vs 非流式 **350ms** |
| 用户点击未水合区域会怎样? | 该边界**插队**;被打断的水合作废,总时长 **+10ms** |

## 原理详解

### 1. 服务端:shell 先走,慢区域后补

官方 API 的两个关键回调:

- `onShellReady` —— shell(不依赖慢数据的外壳)就绪,**立刻开始响应**,不必等整棵树
- `onAllReady` —— 整棵树就绪,给爬虫用

慢区域用 `<Suspense>` 包住,shell 阶段只发 fallback,**并用注释标记圈出插入点**:

```html
<html><body><header>app</header>
<!--$S:0--><div class="skeleton">loading posts…</div><!--/$S:0-->
```

数据就绪后推的 chunk 用**同一个 id** 的标记,客户端因此能精确替换该区间:

```html
<!--$S:0--><ul class="posts"><li>post 1</li></ul><!--/$S:0-->
```

> 真实 React 还会在响应内联 `$RC(...)` 脚本指示 "replace content";本 demo 用注释标记 + `mode: 'replace-content'` 表达同一语义。

`renderToReadableStream`(边缘运行时)是同一模型的 Promise 版本:返回的 Promise 在 shell 就绪时 resolve,`stream.allReady` 用于需要完整 HTML 的场景。

### 2. 客户端:选择性水合 = 按边界排队 + 用户输入优先

`hydrateRoot(container, <App/>)` 把服务端 HTML 变成可交互应用。关键性质:

1. **按边界为单位水合** —— 慢边界不再阻塞整页可交互
2. **用户输入优先** —— 点击尚未水合的区域,React 会优先水合它;若当前正水合别的区域,还会**打断**(水合属于可中断的并发渲染)
3. **内容没到就无法水合** —— 交互只影响"已到达"的边界

本 demo 的调度器实测三种情形:

| 情形 | 结果 |
| --- | --- |
| 两个边界同时就绪,无交互 | `shell > b1 > b2`(文档顺序) |
| b1 水合期间用户点 b2 | `shell > b2! > b1`(插队 1 次,打断 1 次) |
| b2 内容 300ms 才到,用户 205ms 就点它 | `shell@5 > b1@200 > b2@300`(拦不住"没数据") |

### 3. 流式为什么更快:把"水合 shell"和"等数据"重叠

| 时间线 | 非流式 | 流式 |
| --- | --- | --- |
| 第一个字节 | 300ms(等全部数据) | **5ms**(shell 就绪) |
| shell 水合 | 300 → 330ms | **5 → 35ms**(数据还没到就完成了) |
| b1 可交互 | 350ms | **220ms** |

提前量 = shell 水合耗时(30ms)抢在等待之前,加上 b1 内容本身早到的 100ms。**这正是流式 SSR 的收益来源:并行化"传输/水合"与"等数据"**。

### 4. 水合不匹配:三态处理

`hydrateRoot` 要求客户端首遍输出**与服务端逐字一致**,否则:

| 差异类型 | React 行为 | 本 demo 模型 |
| --- | --- | --- |
| 完全一致 | 直接接管 | `match` |
| 文本/属性不同(时间戳等) | 可恢复:`onRecoverableError`,按客户端结果重渲染该处 | `recoverable` + `reported` |
| 结构(标签)不同 | 不可恢复:整棵子树退化为客户端渲染 | `fatal` |
| `suppressHydrationWarning` | 静默警告,**不修正内容**(官方:只作用一层) | `suppressed` |

官方列出的高频原因:根部多余空白换行、`typeof window !== 'undefined'` 分支、浏览器专有 API(`window.matchMedia`)、两端取到不同数据(时间戳/随机数)、多 root 时 `useId` 前缀不一致。

推荐解法是**两遍渲染**:首遍与服务端一致,`useEffect` 里 `setIsClient(true)` 后再做第二遍。代价是组件渲染两次,慢网络下可能闪烁。

### 5. 流式下 Error Boundary 不能省

shell 一旦 flush,**HTTP 状态码已经发出,后续错误无法再改成 500**。此时只有两条路:

1. 用 Error Boundary 把失败区域替换成兜底 UI(其余区域照常)
2. 让客户端水合时拆掉该子树

所以流式 SSR 的标准做法是:**每个流式区域同时包 Suspense Boundary(管"还在加载")和 Error Boundary(管"加载失败")**。

## 对比

| 方案 | 首字节 | 可交互 | SEO | 复杂度 |
| --- | --- | --- | --- | --- |
| CSR | 快 | JS 加载后 | 差 | 低 |
| 传统 SSR(renderToString) | 等全部数据 | 整页水合完 | 好 | 中 |
| 流式 SSR + 选择性水合 | shell 就绪即发 | 按边界渐进 | 好(爬虫等 allReady) | 高 |
| SSG / PPR | 最快(静态) | 与水合相同 | 最好 | 中(需构建策略) |

## 环境

- Node.js ≥ 14(本地 22.22.2 实测),零第三方依赖
- 不使用真实 DOM:HTML 与 DOM 均为可序列化模型,虚拟时间线保证可复现
- TS 版未编译(本机无 tsc)

## 运行方式

```bash
cd 02-Web开发/01-前端框架/SSR-Hydration/js
node ssr_check.js     # 33 项断言,全部通过时 exit 0
```

## 关键代码

```js
// 服务端:shell 先发,标记圈出插入点
const placeholder = openMark(id) + page.boundary.fallback + closeMark(id);
send({ type: 'shell', at: 5, html: page.shell.join('') + placeholder });
// 数据就绪 → 同 id 标记 + 真实内容,客户端据此替换
send({ type: 'boundary', at: 300, id, mode: 'replace-content',
       html: openMark(id) + page.boundary.content + closeMark(id) });

// 客户端:交互优先 + 可中断
const urgent = interactions.find(i => !i.handled && available.includes(i.id) && i.at <= now);
const target = urgent ? urgent.id : available[0];
const preempt = interactions.find(i => i.at > startAt && i.at < endAt && arrival(i.id) <= endAt);
if (preempt) { interrupted++; now = preempt.at; continue; }   // 让出,半成品作废
```

## 性能边界

- **流式不减少总字节**:只是把字节分成多段、让浏览器更早开始干活;HTML 总量与非流式一致
- **插队有代价**:被打断的水合工作作废需重做,本 demo 实测总时长 +10ms;频繁交互会放大
- **水合本身仍是 CPU 密集**:可交互时间 ≈ 水合时间,边界切得越细越早可交互,但边界过多会增加调度与重复框架开销
- **首字节早 ≠ 交互早**:TTFB 改善明显,真正的交互时间取决于 JS 体积与边界粒度
- **`allReady` 会放弃流式的全部收益**:等它就是传统 SSR;只在爬虫 UA 场景用
- **流式下的错误无法改状态码**,Error Boundary 是唯一兜底

## 注意事项与常见坑

- **两端必须同参渲染**:服务端 `renderToReadableStream(<App assetMap={...}/>)` 与客户端 `hydrateRoot(document, <App assetMap={window.assetMap}/>)` 必须传同一份数据,否则必然不匹配
- **`ReactDOM.hydrate` 在 React 19 已移除**:一律用 `hydrateRoot`
- **`root.render()` 在水合完成前调用会丢弃整段服务端 HTML**,退化为纯客户端渲染
- **`suppressHydrationWarning` 只作用一层且不修正内容**,只适合时间戳这类"注定不同"的叶子节点,不能当修的捷径
- **水合错误不是"警告"而是 bug**:官方明确"最坏情况下事件处理器会挂到错误的元素上"
- **`getPending()` / 排队边界只返回"已到达"的**:交互不能凭空水合没有内容的边界(本 demo 断言了这点)
- **边界标记 id 必须两端对齐**:marker 不一致 → 客户端找不到插入点,内容替换失败
- **虚拟时间线与真实调度器有差异**:真实 React 用 `shouldYield` 帧预算切分,本 demo 用固定 `boundaryCost` 简化

## 参考资料

实际读过:

- [react.dev — renderToReadableStream](https://react.dev/reference/react-dom/server/renderToReadableStream) —— 边缘运行时的流式 API、`allReady` 与 shell 的 Promise 语义、客户端必须传同一份 `assetMap` 否则水合报错、用 Suspense 包住慢区域
- [react.dev — hydrateRoot](https://react.dev/reference/react-dom/client/hydrateRoot) —— 水合不匹配的常见原因清单、`onRecoverableError` / `onUncaughtError` / `onCaughtError`、`suppressHydrationWarning` 只作用一层且不修正文本、两遍渲染(`isClient`)的完整示例与"水合变慢"的警告
- [React v18.0 官方发布博客](https://react.dev/blog/2022/03/29/react-v18) —— `renderToPipeableStream` / `renderToReadableStream` 新 API、选择性水合(用户点击未水合区域 React 优先水合它)、自动批处理与 transition
- [Streaming Server-Side Rendering — patterns.dev](https://www.patterns.dev/posts/streaming-ssr) —— 水合"分块进行 + 用户输入优先"、`$RC("B:0","S:0")` 指令与 chunk 形状、流式下 Error Boundary + Suspense Boundary 必须成对、`onAllReady` 给爬虫、按页面特征判断是否该用流式
- [How to Implement Streaming SSR in React 18 — OneUptime](https://oneuptime.com/blog/post/2026-01-15-streaming-ssr-react-18/view) —— chunk 逐段到达的实际形态(含 `$RC` 标记与 `progressiveChunkSize`)、嵌套 Suspense 边界、`use()`/可流式数据源的写法
