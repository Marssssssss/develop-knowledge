# COOP / COEP / CORP 与跨源隔离

> Spectre 这一类攻击的根因是:**同一进程里的两个站点共享地址空间**。
> 同源策略管的是"能不能读到数据",管不了"能不能用计时器侧信道把它测出来"。
> 跨源隔离(cross-origin isolation)要做的就是**把你的文档挪进一个只有它自己的
> 浏览上下文组**,代价是:所有跨源子资源都必须**显式同意**被你嵌入。

## 1. 简介

三个头,三条职责:

| 头 | 谁设置 | 管什么 |
| --- | --- | --- |
| `Cross-Origin-Opener-Policy` (COOP) | 顶层文档 | 谁能通过 `window.opener` / `postMessage` 碰到我 |
| `Cross-Origin-Embedder-Policy` (COEP) | 顶层文档 | 我要求我加载的跨源资源必须显式同意 |
| `Cross-Origin-Resource-Policy` (CORP) | **被加载的资源** | 我同意被谁加载 |

CORP 的方向容易被记反:它不是"我要加载谁",而是"**我允许谁来加载我**"。

## 2. 原理详解

### 2.1 COOP:五种取值(HTML §7.1.3)

| 取值 | 语义 |
| --- | --- |
| `unsafe-none` | 默认。与前任文档共用 top-level browsing context |
| `same-origin-allow-popups` | 强制新建 browsing context(除非前任同策略且同源);**但自己打开的弹窗可以不受限** |
| `same-origin` | 额外要求:它打开的 auxiliary browsing context 必须同源且同策略,否则"看起来是关着的"(`window.opener === null`) |
| `same-origin-plus-COEP` | **不能由响应头直接设置**。是 `COOP: same-origin` 与一个"兼容跨源隔离"的 COEP **组合**出来的内部取值 |
| `noopener-allow-popups` | 无条件新建,切断 opener |

`match opener policy values`(决定要不要换浏览上下文组):

```text
① 两边都是 unsafe-none                     → true（不切换）
② 任一边是 unsafe-none                     → false（切换）
③ 值相等 且 两个 origin 同源                → true
④ 其余                                     → false
```

注意 ②:**`unsafe-none` 与任何非 `unsafe-none` 组合都要切换**。这意味着
COOP 是"双边"的 —— 光在自己这边设 `same-origin`,从别的页面跳过来依然会切换。

### 2.2 COEP:解析**失败即放开**(§2.3)

COEP 的值是 Structured Header 的 **token**。规范给了一张表,本 demo 逐行实现:

| 响应头值 | 最终策略 |
| --- | --- |
| (没有头) | `unsafe-none` |
| `require-corp` | `require-corp` |
| `unknown-value` | `unsafe-none` |
| `require-corp, unknown-value` | `unsafe-none` |
| `unknown-value, unknown-value` | `unsafe-none` |
| `unknown-value, require-corp` | `unsafe-none` |
| **`require-corp, require-corp`** | **`unsafe-none`** |

最后一行是 production 事故高发点:**两个 COEP 响应头会被合并成一个 list,
而 list 不是 token,于是整条被忽略**,静默失去跨源隔离。

规范给了理由:为了对未知取值前向兼容。代价是这类错误**不会报错**。

### 2.3 CORP internal check(§3.2.1)

```text
① mode ∈ {same-origin, cors, websocket}        → allowed
② mode == navigate 且 embedder 是 unsafe-none  → allowed
③ 取响应的 CORP；为 null 且 embedder 是 require-corp → 按 same-origin 处理
④ null / cross-origin → allowed
   same-origin  → request origin 与 current URL origin 同源才 allowed
   same-site    → 主机 same-site 且（scheme 是 https 或 响应的 HTTPS state 是 "none"）
⑤ 未知值 → allowed
```

两条要紧的推论:

- **`require-corp` 下"不写 CORP"等于 `same-origin`**(③),即最严格;
  想被跨源加载必须显式写 `Cross-Origin-Resource-Policy: cross-origin`。
- **CORP 独立于 COEP 生效**(自检里有对应断言):即使页面完全没开 COEP,
  资源自己写上 `CORP: same-origin` 也能挡住跨源的 `no-cors` 嵌入
  (典型场景:防 `<img>` / `<script>` 跨站拉取)。只有 `navigate` 这一 mode
  对 `unsafe-none` 有短路(因为 iframe 的嵌入关系由 XFO / `frame-ancestors` 管)。

`same-site` 那条还有个 note:**安全传输来的响应不匹配非安全的发起源** ——
即使主机同站,http 页面也拿不到 https 响应。

### 2.4 嵌套文档必须自己声明(§3.1.3 / §4.3)

parent 是 `require-corp` 时,子文档必须**自己**也声明 `require-corp`,
否则 Blocked。规范专门用 §4.3 解释了为什么不"级联"(cascading):
级联会让子文档在不知情的情况下被拉进隔离环境,从而意外获得
`SharedArrayBuffer` 这类能力。

### 2.5 跨源隔离的充要条件

```text
COOP: same-origin  +  COEP: require-corp  (且安全上下文)
   → opener policy value 升级为 same-origin-plus-COEP
   → self.crossOriginIsolated === true
```

非安全上下文(如 http)下,`obtain an opener policy` 会**直接返回默认策略**
—— 也就是说跨源隔离只在安全上下文里存在。

## 3. 关键代码

```python
def corp_internal_check(embedder_policy_value, mode, request_origin,
                        current_url_origin, corp_header, ...):
    if mode in ("same-origin", "cors", "websocket"):
        return True
    if mode == "navigate" and embedder_policy_value == UNSAFE_NONE:
        return True
    policy = corp_header
    if policy is None and embedder_policy_value == REQUIRE_CORP:
        policy = SAME_ORIGIN          # 不写 CORP = 按 same-origin 处理
    ...
```

## 4. 运行方式

```bash
cd COOPCOEP跨源隔离
python selfcheck_coop_coep.py     # 60 条断言,输出 "ALL OK"
go run .
```

## 5. 性能与工程边界

- **换浏览上下文组是有代价的**:COOP 触发切换后,新文档不在原进程里,
  无法复用原来的渲染进程与缓存;每次跨 COOP 边界的导航都是一次新进程创建。
  这正是它对 Spectre 有效的**原因**,不是副作用。
- **`require-corp` 会打断一大票第三方资源**:广告、埋点、字体、CDN 图片
  只要没配 CORP 或 CORS 就全部失败。**先上 `Cross-Origin-Embedder-Policy-Report-Only`
  收报告再切强制**。
- **缓存分区**:跨源隔离会影响共享缓存的命中率(不同顶层站点的子资源不再共用
  一份 HTTP 缓存)。

## 6. 注意事项与常见坑

1. **`require-corp, require-corp` = 没开**。检查响应头是不是被中间层(CDN、
   网关注入、应用框架)重复写了一次。这是"我明明配了却没隔离"的头号原因。
2. **CORP 的方向别记反**:它是**被加载方**的声明。你自己的页面加 CORP 头
   不会让第三方脚本能被加载。
3. **`same-origin-allow-popups` 达不到跨源隔离**。需要 `SharedArrayBuffer` /
   高精度计时器就必须用 `same-origin`,代价是 OAuth 弹窗、支付窗口这类
   `window.opener` 交互会失效。
4. **同源 ≠ 可以不声明 CORP**:`require-corp` 下 `same-origin` 判定通过,
   所以同源资源无需 CORP 头;但**跨源的 `no-cors` 资源必须要** `cross-origin`。
5. **`noopener-allow-popups` 不提供安全边界**。HTML §7.1.3 原文列了一串它
   挡不住的东西:同源请求、同源 iframe、Cookie、`localStorage`、Service Worker、
   `postMessage`/`BroadcastChannel`、自动填充。它只切断 opener。
6. **同站判定依赖"可注册域"**:本模型用"最后两个标签"做教学近似,
   真实实现要用公共后缀列表(否则 `a.co.uk` 与 `b.co.uk` 会被误判成同站)。

## 7. 参考资料(本 demo 实际读取)

- WHATWG — *HTML Living Standard* §7.1.3 Cross-origin opener policies
  <https://html.spec.whatwg.org/multipage/browsers.html>
- WICG — *Cross-Origin Embedder Policy*
  <https://wicg.github.io/cross-origin-embedder-policy/>
  (§2.3 Parsing 的 fail-open 表、§3.1 Integration with HTML、
  §3.2.1 Cross-Origin Resource Policy Checks、§4.3 Cascading vs. requiring)
- WHATWG — *Fetch Living Standard* §Cross-Origin-Resource-Policy header
  <https://fetch.spec.whatwg.org/#cross-origin-resource-policy-header>
- MDN — *Cross-Origin-Opener-Policy*
  <https://developer.mozilla.org/docs/Web/HTTP/Headers/Cross-Origin-Opener-Policy>
