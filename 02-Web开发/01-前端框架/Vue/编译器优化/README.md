# Vue 3 编译期优化 — 静态提升 / Patch Flags / Block Tree

## 简介

Vue 与 React 最本质的分野不是 API,而是**框架同时掌握编译器和运行时**。官方把这条路线叫 **Compiler-Informed Virtual DOM**:

> "In Vue, the framework controls both the compiler and the runtime. This allows us to implement many compile-time optimizations that only a tightly-coupled renderer can take advantage of."
> —— [vuejs.org/guide/extras/rendering-mechanism.html](https://vuejs.org/guide/extras/rendering-mechanism.html)

本 demo 实现一条完整链路(零依赖,~730 行):

```
模板字符串 → parse → AST → transform(提升 / 打标 / 建块 / 压静态)→ codegen → render 函数源码
                                                                          ↓ new Function 真执行
                                                              mount / patch(优化路径 vs 全量基线)
```

一句话结论:**Vue 把"运行时才知道的 diff 范围"提前到编译期算完了**,所以它能"树结构编写、数组结构更新"。

## 原理详解

### 1. 五项编译期优化

| 优化 | 触发条件 | 编译产物 | 本 demo 实测 |
| --- | --- | --- | --- |
| Cache Static 静态提升 | 子树无动态绑定、无指令、无插值 | 提升为模块级 `_hoisted_N`,只创建一次 | 每轮 render 的 vnode 创建数 **9 → 4** |
| stringifyStatic 静态压缩 | 连续 ≥5 个静态元素 | 压成一个 static vnode,挂载直接 `innerHTML` | **5 个 div = 1 次 innerHTML** |
| Patch Flags 靶向更新 | 元素有动态绑定 | `createElementVNode("div", {...}, null, 2 /* CLASS */)` | props 比较 **6 → 2** |
| Tree Flattening 块树 | 根 / v-if / v-for / 插槽出口 | 块的动态后代平铺进 `dynamicChildren` | patch 访问节点 **6 → 4** |
| cacheHandler 事件缓存 | 内联 `@click="fn"` | `_cache[0] \|\| (_cache[0] = (...args) => …)` | handler 引用跨渲染恒定 |

### 2. Patch Flags:把"要更新什么"编码成位掩码

官方枚举逐条转录自 [patchFlags.ts](https://raw.githubusercontent.com/vuejs/core/main/packages/shared/src/patchFlags.ts):

```js
TEXT = 1, CLASS = 2, STYLE = 4, PROPS = 8, FULL_PROPS = 16, NEED_HYDRATION = 32,
STABLE_FRAGMENT = 64, KEYED_FRAGMENT = 128, UNKEYED_FRAGMENT = 256,
NEED_PATCH = 512, DYNAMIC_SLOTS = 1024, DEV_ROOT_FRAGMENT = 2048,
CACHED = -1,   // 旧名 HOISTED,负数 → 只用 === 比较,永不参与按位与
BAIL = -2,     // 退出优化模式,必须整棵 diff
```

多个标记按位或合并成一个数字(如 `TEXT|STYLE = 5`),运行时一次 `&` 判断是否要做某项工作。**为什么必须是 2 的幂**:`flag & TEXT` 一条指令判包含,这是整个优化成立的前提。

> 本 demo 实测一个反直觉点:`<button>点击 {{count}}</button>` 这种**数组 children** 不会拿到 TEXT 标记(TEXT 只用于"唯一子节点是插值"的 children fast path,即 `createElementVNode("p",{…},_toDisplayString(_ctx.msg),1)`),它的 patchFlag 是 `NEED_HYDRATION = 32`。数组子节点由位置对齐 diff 处理。

### 3. Block Tree:为什么能跳过整棵静态树

`openBlock()` 往栈上压一个数组,元素创建时"谁有 patchFlag 谁把自己 push 进去",`createBlock()` 收尾时把这个数组挂成 `dynamicChildren`:

```js
function createVNode(type, props, children, patchFlag = 0, dynamicProps, isBlock = false) {
  const vnode = { … };
  if (patchFlag > 0 && !isBlock && currentBlock) currentBlock.push(vnode);  // 自动收集
  return vnode;
}
function createBlock(type, props, children, patchFlag) {
  const vnode = createVNode(…, /* isBlock */ true);   // 块自己不挂进自己的数组
  vnode.dynamicChildren = currentBlock || [];
  closeBlock();
  if (currentBlock) currentBlock.push(vnode);          // 但作为子块交给父块
  return vnode;
}
```

本 demo 模板的根块只收集到 **3 个动态节点**(`p` / `span` 块 / `button`),9 个元素里的 6 个静态元素连一次遍历都没有发生:

```
patch 访问节点:优化路径 4(根+p+span块+button)  vs  基线 6(多出 h1 与 static 块)
差值恰好 = 2 个提升子树
```

关键点:块有自己的 `dynamicChildren` 时,**完全不做结构性 children diff**(对应官方 `patchElement` 的 `patchBlockChildren` 分支)。这是"跳过静态"比"比较后得知相等"更彻底的原因。

### 4. v-if 为什么要新建块

结构性指令会破坏"子节点顺序不变"的假设(块树的根基)。所以 `v-if` 编译成三元表达式 + 新块:

```js
_ctx.show
  ? (openBlock(), createBlock("span", { style: _ctx.style }, toDisplayString(_ctx.count), 5 /* TEXT, STYLE */))
  : createCommentVNode("v-if", true)
```

新块被登记为**父块 `dynamicChildren` 的一个成员**(实测:关闭 v-if 后该成员从数组消失,patch 只访问根+p+button)。静态结构因此保持稳定,父块不必重新计算。

### 5. 对水合(hydration)的意义

官方明确:`patchFlags` 与树扁平化同样大幅提升 SSR hydration —— 单元素水合可依据 patchFlag 走快路径,**只有块节点及其动态后代需要被遍历**,等于在模板层面实现了 partial hydration。这也是本 demo 只产出一个 patchFlag 位掩码、却能同时服务客户端 patch 与 SSR 水合的原因。

## 对比:Compiler-Informed vs 纯运行时

| 维度 | Vue(编译期+运行期) | React(纯运行时) |
| --- | --- | --- |
| 静态子树 | 提升复用,永不重建/不遍历 | 每次 render 重建 vnode |
| 更新范围 | 编译期算出,位掩码靶向 | 运行时全树 diff |
| 语法自由度 | 受模板语法限制(可静态分析) | JSX 全动态,任意表达式 |
| 优化代价 | 需要编译器 + 运行时协同 | 无编译期信息可用 |
| 逃生舱 | 可手写 render 函数(失去优化) | 无 |

## 环境

- Node.js ≥ 14(本地 22.22.2 实测),零第三方依赖
- `new Function` 用于执行编译产物 —— 与 Vue 运行时编译器的做法一致(生产构建走 AOT,不引入运行时编译器)
- TS 版未编译(本机无 tsc)

## 运行方式

```bash
cd 02-Web开发/01-前端框架/Vue/编译器优化/js
node compiler_check.js     # 34 项断言,全部通过时 exit 0
```

## 关键代码

```js
// 编译产物(实际由 codegen 生成后 new Function 执行)
const _hoisted_1 = /*#__PURE__*/ createStaticVNode("<div class=\"box\">…</div>", 5);
const _hoisted_2 = /*#__PURE__*/ createVNode("h1", null, "静态标题", -1 /* CACHED */);

function render(_ctx, _cache) {
  return ((openBlock(), createBlock("div", { "id": "app", class: normalizeClass(_ctx.cls) }, [
      _hoisted_2,
      createVNode("p", { "class": "static", "id": "p1" }, toDisplayString(_ctx.msg), 1 /* TEXT */),
      _hoisted_1,
      _ctx.show ? (openBlock(), createBlock("span", { style: _ctx.style }, toDisplayString(_ctx.count), 5 /* TEXT, STYLE */))
                : createCommentVNode("v-if", true),
      createVNode("button", { "onClick": _cache[0] || (_cache[0] = (...args) => _ctx.onClick(...args)) },
        [ "点击 ", toDisplayString(_ctx.count) ], 32 /* NEED_HYDRATION */),
    ], 2 /* CLASS */)));
}
```

## 性能边界

- 静态提升的收益**随静态子树规模线性放大**;整个模板都动态时提升无收益(vnode 创建数不降)
- 静态压缩有阈值:不足 5 个连续静态元素时逐个提升,`innerHTML` 反而不划算(解析开销 + 丢失节点引用)
- patchFlag 只覆盖**编译期可判定**的更新类型;`v-html`、动态 key、动态插槽会被标记 `FULL_PROPS` / `BAIL`,退回全量 diff
- 块数不是越少越好:`v-for` 会为每个迭代项建块,子块过多会让 `dynamicChildren` 变长(实测 3 个动态节点时优化收益明显,全动态列表则退化为线性遍历)
- 手写 render 函数(或 JSX)**不会**得到这些优化 —— 编译器看不到就没有标记,运行时只能全量 diff

## 注意事项与常见坑

- **`CACHED`/`BAIL` 是负数**:任何 `patchFlag & FLAG` 判断都必须先 `patchFlag > 0`,否则负数按位与会出诡异结果
- **TEXT 标记不是"有插值就打"**:只用于唯一子节点是插值的 children fast path;数组 children 打 TEXT 会让运行时把整个数组写进 `textContent`(本 demo 首轮自检就挂在这)
- **块 vnode 不能把自己挂进自己的 `dynamicChildren`**:必须 `isBlock` 排除,否则遍历时自引用
- **根节点必须显式包 `openBlock()`**:漏掉会让 `dynamicChildren = null`,所有动态节点失去收集容器(本 demo 第二个自检 bug)
- **嵌套 `v-if` 要递归走 `genNode` 而不是 `genElement`**:否则指令被当普通属性生成到 props 里,产出语法错误的 JS
- whitespace 处理会改变静态判定:`'condense'` 下纯空白文本节点被丢弃,否则 `<div>\n  <span/>\n</div>` 里混入空白文本节点,静态压缩永远凑不满阈值
- 浮点/字符串比较:文本 diff 用 `!==` 比较原始值即可,不要对已 `toDisplayString` 的结果再比较
- 本 demo 未实现卸载与 keyed diff(块增删只做 append),真实 Vue 会按块粒度卸载

## 参考资料

实际读过:

- [Vue 官方文档 — Rendering Mechanism](https://vuejs.org/guide/extras/rendering-mechanism.html) —— Virtual DOM / Render Pipeline / Cache Static / Patch Flags / Tree Flattening / Impact on SSR Hydration 全节
- [vuejs/core `patchFlags.ts`](https://raw.githubusercontent.com/vuejs/core/main/packages/shared/src/patchFlags.ts) —— PatchFlags 枚举与逐条注释原文(含 `CACHED` 取代旧名 `HOISTED`、`NEED_HYDRATION` 取代旧名 `HYDRATE_EVENTS`)
- [Vue Template Explorer](https://template-explorer.vuejs.org/) —— 官方 playground,用于核对 `_hoisted_N` / `openBlock()` / patchFlag 注释的产物形状
- [Vue3 编译器优化 — ele-cat](https://ele-cat.github.io/views/interview/frontend/vue/compiler.html) —— 为什么 patchFlag 是 2 的幂、Vue 能做而 React 难做的原因
- [vue3 源码解读 — 童话的博客](https://tongxingkuan.xin/articles/vue3) —— parse/transform/generate 三阶段与 `cacheHandler` 的编译产物形态
