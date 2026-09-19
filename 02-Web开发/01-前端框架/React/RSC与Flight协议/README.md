# React Server Components：Flight 线协议

## 一、简介

RSC 与 SSR 解决的是不同问题。React 官方的原话是：Server Components *"render ahead of time, before bundling, in an environment separate from your client app or SSR server"* —— 它们可以在构建期跑（读文件系统、读 CMS），也可以每请求跑；**客户端收到的不是组件代码，而是渲染结果**。

把「服务端组件树」搬到浏览器的那段数据格式就是 **Flight**。本 demo 复刻它的两个核心：

1. **行状态机**：`<hexRowID>:<TAG>[<hexByteLen>,]<payload>`，可以在任意字节边界被切分、跨网络包重组；
2. **引用前缀表**：payload 里以 `$` 开头的字符串不是普通字符串，而是指向其它行/特殊值的引用。

代码：`flight_protocol.py` + `selfcheck_flight.py`（31 断言全通过）+ `flight_protocol.go`。

## 二、原理详解

### 2.1 行格式：五态状态机

```
ROW_ID  --读到 ':'-->  ROW_TAG  --长度类 tag--> ROW_LENGTH --读到 ','--> ROW_CHUNK_BY_LENGTH
                          |--其它 tag-------------> ROW_CHUNK_BY_NEWLINE
                          \--未知 tag-------------> ROW_CHUNK_BY_NEWLINE（字符不消费！）
```

- `rowID` 是**十六进制**累加：`rowID = (rowID << 4) | (byte > 96 ? byte - 87 : byte - 48)`。
- 带「字节长度」的 tag 只有 15 个：`T A O o b U S s L l G g M m V`，形如 `0:T1f,{...}`（长度是 **UTF-8 字节数**，不是字符数，中文 payload 差别很大）。
- 其余 `A-Z` 加上 `#` `r` `x` 按 `\n` 切分。
- **未知 tag 不消费该字节**（源码 else 分支没有 `i++`），因为它「probably part of the data」。所以 `1:hello:world` 的第二个 `:` 后面的内容仍然是 payload，而不是新的一行。

### 2.2 引用前缀（`parseModelString` 的 `switch (value[1])`）

| 前缀 | 含义 | 前缀 | 含义 |
| --- | --- | --- | --- |
| `$`(单个) | `REACT_ELEMENT_TYPE` | `$$x` | 转义成 `$x` |
| `$L<hex>` | lazy chunk（**后到内容**） | `$@<hex>` | Promise chunk |
| `$w<hex>` | Weak Promise | `$S<name>` | `Symbol.for(name)` |
| `$h<ref>` | Server Reference | `$H<ref>` | Server Object Reference |
| `$T<ref>` | Temporary Reference | `$Q/$W` | Map / Set |
| `$B/$K` | Blob / FormData | `$Z` | Error |
| `$i<ref>` | Iterator | `$D<ISO>` | Date |
| `$n<int>` | BigInt | `$I/$N/$u` | Infinity / NaN / undefined |
| `$-0` / `$-Infinity` | 负零 / 负无穷 | | |

这些前缀存在的理由很实在：JSON 表达不了 `undefined`、会丢 `BigInt` 精度、把 `-0` 变成 `0`、把 `NaN` 变成 `null`。Flight 用「字符串 + 前缀」把它们全部塞回 JSON 的合法子集里。

### 2.3 流式：shell 先到，Suspense 内容后到

```
0:T…,{"shell":1,"comments":"$L1"}     ← 先发：comments 是个 lazy 占位
1:T…,["c1","c2"]                       ← 后发：同一个 chunk 被兑现
```

关键点：`$L1` 解析出的 `Lazy` **持有的是 chunk 对象本身**，不是当时的值。行 1 到达时 `chunk.resolve()` 兑现，此前挂在该 chunk 上的所有引用同时看到新值。这正是「HTML shell 先刷、Suspense 边界内容后补」在协议层的样子。

`$@` 同理，但返回的是 chunk（Promise）本身，可以 `.then()` 注册回调——对应官方「在服务端创建 promise、在客户端用 `use()` 续上」的写法。

### 2.4 与「序列化整个 props」的区别

`'use client'` 是**模块图边界**：从 client 模块 import 出去的东西会变成 client reference（`$h`/`$I` 一类），真正落在客户端的是「去哪加载这个模块」的坐标，而不是函数体。这也是为什么服务端组件里的库不会进 bundle（官方例子：`marked` 35.9K + `sanitize-html` 206K 全部不进包）。

## 三、对比

| | 传输内容 | 客户端能否交互 | 是否进 bundle |
| --- | --- | --- | --- |
| SSR（HTML） | 渲染好的 HTML 字符串 | 需要水合后才有 | 组件代码要下发 |
| RSC（Flight） | 组件树的结构化行流 | client 组件坐标 + 可续的 promise | 服务端组件代码**不下发** |
| 纯 CSR | 组件代码 + 数据请求 | 是 | 全部下发 |

SSR 关心「首屏像素」，RSC 关心「哪些代码根本不该到浏览器」。两者可以叠加（RSC 的输出再 SSR 成 HTML）。

## 四、环境

- Python 3.8+（仅标准库）
- Go 1.20+（对照实现）

## 五、运行方式

```bash
python selfcheck_flight.py
# flight_protocol: 31/31 assertions passed
```

## 六、注意事项与常见坑

1. **未知 tag 分支不能前进指针。** 复刻时若顺手 `i++`，`1:hello:world` 会被吃掉首字母变成 `ello:world`——本 demo 开发期就是这个 bug。
2. **行长度是字节数。** 用字符数算长度，遇到非 ASCII payload 立刻错位，后续所有行全部解析失败。
3. **带长度的行之间没有分隔符。** 手动拼接时多加一个 `\n`，会被下一行的 `ROW_ID` 状态当成十六进制字符读进去（`10-48 = -38`），rowID 直接烂掉。
4. **`$$` 是 `value.slice(1)` 不是 `value.slice(2)`。** `$$x` 的语义是「一个字面量 `$x`」，不是「去掉前缀后剩 `x`」。
5. **chunk 收尾必须兑现。** 只记 `raw` 不 `resolve()`，所有 `$L`/`$@` 引用会永远停在 pending——表现为「UI 一直显示 fallback」。
6. **引用解析是懒的。** 未到达的行不会报错，而是保持 pending；不要为了「不崩」而把它填成空对象，那会把「数据还没到」和「数据为空」混为一谈。

## 七、性能边界

- 状态机是 O(总字节数)，缓冲区按长度预取，跨包重组无回溯。
- 每行一个 chunk，长列表会线性增加 chunk 数；`resolve` 时遍历 waiters，单个 chunk 被大量引用时回调是 O(引用数)。
- BigInt / Date / Symbol 都要过一次字符串解析，热路径上属于额外成本。

## 八、参考资料（实际读过）

- React 源码 · `ReactFlightClient.js`（行状态机 ROW_ID/ROW_TAG/ROW_LENGTH、引用前缀 switch）— https://cdn.jsdelivr.net/gh/facebook/react@main/packages/react-client/src/ReactFlightClient.js
- React 官方 · Server Components（构建期渲染、'use client' 边界、async 组件、promise 跨边界用 `use` 续上）— https://react.dev/reference/rsc/server-components
