# Fetch Metadata 与资源隔离策略

浏览器每次发请求时会自动附带四个 `Sec-Fetch-*` 头，把「这个请求是谁、从哪来、要去哪」
显式告诉服务器。服务器因此可以在**应用层**判断一个请求是否可疑，而不必依赖
`Origin`/`Referer`（两者都会缺失、会被 strip、也会被伪造判断绕开）。

## 一、四个头（W3C Fetch Metadata §2）

| 头 | 类型 | 含义 | 取值 |
| --- | --- | --- | --- |
| `Sec-Fetch-Dest` | `sf-token` | 请求目的地 | `empty`（`fetch()`）、`image`、`worker`、`document`、`iframe` … |
| `Sec-Fetch-Mode` | `sf-token` | 请求模式 | `cors` / `navigate` / `no-cors` / `same-origin` / `websocket` |
| `Sec-Fetch-Site` | `sf-token` | 发起方与目标的关系 | `cross-site` / `same-origin` / `same-site` / `none` |
| `Sec-Fetch-User` | `sf-boolean` | 导航是否由用户激活触发 | 只在导航请求且为真时出现（`?1`） |

规范 §2 给的两个对照示例：

```http
# 跨站 <img>
Sec-Fetch-Dest: image
Sec-Fetch-Mode: no-cors
Sec-Fetch-Site: cross-site

# 用户点击站内链接触发的顶层导航（example.com → example.com）
Sec-Fetch-Dest: document
Sec-Fetch-Mode: navigate
Sec-Fetch-Site: same-origin
Sec-Fetch-User: ?1
```

注意 `fetch()` 的 destination 是**空串**而不是 `empty` 之外的某个词——`Sec-Fetch-Dest: empty`
是一个真实会出现在线上的值，写策略时别把它当成「缺失」。

## 二、`Sec-Fetch-Site` 的判定算法（§2.3）

```
1. header = same-origin                      ← 初值
2. 若是「用户显式触发」的导航请求 → none
3. 若 header 不是 none，遍历 request 的 url list：
     a. url 与 request 同源        → continue        ← 注意是 continue，不是赋值
     b. header = cross-site
     c. 若 request 与 url 不同站   → break
     d. header = same-site
```

三个容易读错的点：

1. **初值就是 `same-origin`**。url list 为空时（例如没有重定向）算法一步不改，结果就是
   `same-origin`。
2. **同源的 url 是 `continue`，不参与赋值**。所以「链上出现过同源 URL」不会把结果拉回
   `same-origin`——只有**全部**同源才会保持 `same-origin`。
3. **先写 `cross-site` 再判断**。跨站时 `break` 停在那里，`cross-site` 被保住；同站时才
   降级成 `same-site`。所以 `cross-site` 一旦出现就**不可能被后面的 URL 覆盖**。

## 三、重定向链会「记住」最坏的一环（§4.1）

规范原文的例子：从 `https://example.com/` 出发

| 链 | `Sec-Fetch-Site` |
| --- | --- |
| `example.com/redirect` | `same-origin` |
| → `subdomain.example.com/redirect` | `same-site`（registrable domain 相同） |
| → `example.net/redirect` | `cross-site` |
| → 再绕回 `example.com/` | **仍是 `cross-site`** |

最后一步绕回同源，**结果不会变好**：因为算法在第 3 个 URL 处就 `break` 了，第 4 个 URL
**根本没被检查**。本 demo 用 `examined` 计数器把这个事实断言下来（只检查了 3 个）。

唯一的例外是 `none`——§4.1 的 note 建议地址栏粘贴短链这类场景在重定向后保持 `none`。

## 四、为什么它比 `Origin`/`Referer` 可靠（§4.2）

四个头都带 `Sec-` 前缀，而 `Sec-` 前缀使它们成为 **forbidden response-header name**，
也就是 JS 无法通过 `fetch()` 的 `headers` 选项改写。

```js
fetch(url, { headers: { "Sec-Fetch-Site": "same-origin" } });  // 无效，头不会带上
```

这一点由浏览器强制，服务端可以假设这四个头**不是攻击者用 JS 塞进来的**。

## 五、服务端侧的处理口径

- **§2.2 / §2.3**：`Sec-Fetch-Mode` 与 `Sec-Fetch-Site` 出现**不认识的值**时，服务端
  **SHOULD 忽略这个头**（为了前向兼容未来新增的请求类型）。
  规范对 `Sec-Fetch-Dest` 没有这句要求，所以 Dest 原样透传。
- **§3**：只有 URL 是 *potentially trustworthy*（https / localhost）才会带这些头；
  不是的话**一个都不发**。所以策略必须处理「完全没有元数据」的情况。
- 策略示例（**工程惯例，非规范条文**，本 demo 的 `isolation_policy`）：
  导航请求只放行 `same-origin` / `same-site` / `none`；子资源只放行 `same-origin` / `same-site`；
  没有元数据时 **fail-open**（老浏览器），否则会大面积误伤。

## 六、口径说明

- 本 demo 的 `same_site` 实现的是 URL 规范的 **schemelessly same site**（只比 registrable
  domain）。URL 规范完整的 "same site" 还额外要求 scheme 相容，这里没有建模。
- `registrable_domain` 用了一个 3 条目的简化 Public Suffix List（`com`/`net`/`org`/`co.uk`
  中的 `co.uk` 是多级后缀的代表）。真实实现必须用完整 PSL。
- `potentially trustworthy` 简化为「https 或 localhost」，未覆盖 `file:`、`.localhost` 等情形。

## 代码结构

| 文件 | 内容 |
| --- | --- |
| `fetchmeta.py` | RD 计算 + 同源/同站判断 + §2.3/§2.4 算法 + §3 闸门 + 隔离策略 |
| `fetchmeta.go` | 同模型的 Go 转写 |
| `selfcheck_fetchmeta.py` | Python 自检（实跑 **53** 断言） |
| `selfcheck_fetchmeta.go` | Go 自检（同套断言） |

## 参考资料（实际读过）

- W3C《Fetch Metadata Request Headers》— `https://w3c.github.io/webappsec-fetch-metadata/`
  （140594 B 全文实读）：§2 Fetch Metadata Headers、§2.1–§2.4 四个头的定义与设置算法、
  §3 Integration with Fetch and HTML、§4.1 Redirects、§4.2 The Sec- Prefix、
  §4.3 Directly User-Initiated Requests
- 文中引用的 [RFC9651] Structured Field Values、[Fetch] request destination、
  URL 规范的 *same site* / *registrable domain*
