# 点击劫持与框架嵌入防护(X-Frame-Options / CSP frame-ancestors)

> 这个防护项的判定逻辑里藏着一处**官方反直觉结论**:多个同样的合法值允许嵌入、多个非法值也允许嵌入,
> 但"一个合法值 + 任意其它值"一律拒绝。本 demo 按 HTML 标准逐条实现,并用官方结果表做断言。

## 简介

点击劫持(UI redress)不是"页面被嵌进 iframe"本身,而是 **"用户以为在点 A,实际点了 B"**。
OWASP 给出三种**相互独立**的机制,应当叠加:

| 机制 | 作用层 | 生效条件 |
| --- | --- | --- |
| `X-Frame-Options` / CSP `frame-ancestors` | 浏览器决定是否渲染 frame | 必须走 **HTTP 响应头**(`<meta>` 无效) |
| `SameSite` cookie 属性 | 跨站请求能否携带 cookie | 阻止"嵌进去也能带着会话操作" |
| frame-buster 脚本 | 页面内 JS 自我检测 | **不可依赖**(见下文绕过手法) |

## 原理详解

**1. HTML 标准 §7.7 的处理模型(逐步)**

```text
① 顶层文档? → 直接允许(约束只针对 child navigable)
② 存在 disposition="enforce" 且含 frame-ancestors 的 CSP? → 返回 true 并**忽略 XFO**
③ 取 X-Frame-Options 的所有值,转小写去重成集合
④ 集合大小 >1 且含 deny/allowall/sameorigin → 拒绝("困惑"的用法一律拦下)
⑤ 集合大小 >1(全是非法值)      → 允许(等同头被省略)
⑥ 唯一值是 deny                   → 拒绝
⑦ 唯一值是 sameorigin             → 逐层检查**每个**祖先是否与子文档同源,全同源才允许
⑧ 走到这里(唯一值是孤立非法值)  → 允许
```

**官方结果表**(本 demo 逐行断言):

| 头值 | 结果 |
| --- | --- |
| `SAMEORIGIN, SAMEORIGIN` | 同源嵌入允许 |
| `SAMEORIGIN, DENY` | 拒绝 |
| `SAMEORIGIN,` | 拒绝(空串也算一个值) |
| `SAMEORIGIN, ALLOWALL` | 拒绝 |
| `SAMEORIGIN, INVALID` | 拒绝 |
| `ALLOWALL, INVALID` | 拒绝(`allowall` 属"困惑"集合) |
| `ALLOWALL,` | 拒绝 |
| `INVALID, INVALID` | **允许**(去重后单值 → 等同没有这个头) |

**2. `frame-ancestors` 压过 `X-Frame-Options`**

CSP 规范原文:*"If a resource is delivered with a policy that includes a directive named
`frame-ancestors` and whose disposition is `enforce`, then the `X-Frame-Options` header MUST be
ignored."* 注意是**无条件忽略** —— 即使 XFO 更严格也不会叠加。所以"两个都写、取交集"是错的;
真正的正确姿势是"两个都写,但知道 CSP 说话算数"。

**3. `SAMEORIGIN` 检查整条祖先链**

`SAMEORIGIN` 不是"直接父窗口同源",而是**从最内层父窗口一路到顶层窗口,每一层都必须同源**。
这与废弃的 `ALLOW-FROM` 形成对比:后者只看**顶层**浏览上下文,在嵌套 frame 场景下会失效。

**4. frame-buster 为什么不能作为主防线**

OWASP 列出的绕过手法(本 demo 存成场景表并可断言):

- **双重 frame**:`parent.location = self.location` 在跨源时会触发后代 frame 导航限制,直接抛错,跳不出去。
- **`<iframe sandbox>` / `document.designMode` / JS 被禁用**:子框架里脚本根本不执行,页面照样显示。
- **代理剥头**:`X-Frame-Options` 被反代/WAF 剥掉是常见事故 —— 这也是要同时下发 CSP 的原因。

反过来,OWASP 推荐的"best-for-now"脚本写法之所以更安全,是因为它是 **fail-closed** 的:

```html
<style id="antiClickjack">body{display:none !important;}</style>
<script>
  if (self === top) {                    // 正常顶层加载 → 撤掉遮蔽样式
    var el = document.getElementById("antiClickjack");
    el.parentNode.removeChild(el);
  } else { top.location = self.location; }   // 被嵌 → 跳出;脚本被禁用则内容保持隐藏
</script>
```

而流行的 `<script>if (top!=self) top.location.href=self.location.href</script>` 在脚本被禁用时是
**fail-open**(内容照常显示且可被点击)。

## 对比:三种机制的边界

| | XFO | CSP frame-ancestors | SameSite |
| --- | --- | --- | --- |
| 能否授权多个域名 | ❌(只能 DENY/SAMEORIGIN) | ✅(完整源列表、通配符) | — |
| 已废弃/淘汰 | 仍被支持,但已被 CSP 取代 | 推荐用法 | — |
| `<meta>` 里写有没有用 | ❌ 无效 | ❌ 无效 | — |
| 能否防"嵌进去后带 cookie 操作" | ❌ | ❌ | ✅ |
| 上游代理剥头 | 会失效 | 会失效(但两者互为备份) | — |

## 环境

- Python 3.9+(仅标准库)
- Node.js 18+

## 运行方式

```bash
python frame_check.py     # Python 侧 46 项断言(含官方结果表逐行)
node   frame_policy.mjs   # JavaScript 侧 21 项断言(同一算法)
```

## 关键代码

```python
# step 5:官方所谓的"困惑"用法 —— 只要集合里有合法值(或遗留的 allowall)且不止一个值,一律拒绝
if len(opts) > 1 and (opts & (VALID | {ALLOWALL})):
    return False, "confused-multiple-values"
# step 6:多个非法值 → 等同头被省略(注意这是**允许**)
if len(opts) > 1:
    return True, "all-invalid"
```

## 性能边界

- 判定是纯集合运算 + 祖先链线性扫描,纳秒级;真实开销在响应头体积与 CSP 解析,可以忽略。
- 本 demo 实现了 CSP 源列表的 `'none'` / `'self'` / `*` / host-source / `*.` 通配子域,**未实现** 端口与路径匹配、nonce 无关部分;已标注在 `frame_ancestors_match()` 的 docstring 里。
- 另一处口径差异:HTML 标准用的是"取头、解码、按逗号拆分"的 Fetch 语义;不同 UA 对**重复头**的合并方式历史上并不一致 —— 标准给出的结果表假定了"合并后按逗号拆"。

## 注意事项与常见坑

- **`<meta http-equiv="X-Frame-Options" content="deny">` 完全无效**;同理 CSP `frame-ancestors` 写在 `<meta>` 里也不生效(必须走 HTTP 头)。这是最常见的"我加了但没用"。
- **`ALLOW-FROM` 已废弃**:现代浏览器遇到它会**忽略整个头**。依赖它等于没有防护(fail-open)。
- **多值场景不要手写**:只要出现一个合法值 + 任意其它值,标准要求**拒绝**;把 `X-Frame-Options` 配成数组/被中间件重复添加,就会掉进这个分支。反过来,两条相同的 `SAMEORIGIN` 是允许的(去重后单值)。
- **`DENY` 与"没有头"的差别是默认可嵌入**。不设头时任何站点都能嵌你的页面 —— 所以"什么都不配"是最危险的配置。
- **CSP 与 XFO 不是取交集**:`frame-ancestors` 一旦 enforce 存在,XFO 被无条件忽略。想收紧就写进 CSP,不要指望 XFO 兜底。
- **`SAMEORIGIN` 要沿整条祖先链**:"我的父窗口与我同源"不够,祖父窗口跨源一样拒绝 —— 嵌套 frame 是真实存在的攻击面。
- **别把 frame-buster 当防护**:它只是在老浏览器上的兜底;要写就写 fail-closed 的 antiClickjack 版本,并永远同时下发响应头。

## 参考资料(实际联网读过)

- OWASP *Clickjacking Defense Cheat Sheet* — <https://cheatsheetseries.owasp.org/cheatsheets/Clickjacking_Defense_Cheat_Sheet.html>(三种独立机制、CSP `frame-ancestors` 与"XFO MUST be ignored"引文、`ALLOW-FROM` 废弃、meta 无效、`SAMEORIGIN` 嵌套 frame 局限、frame-buster 绕过:双重 frame / sandbox / designMode、best-for-now 脚本)
- WHATWG HTML Standard §7.7 *The X-Frame-Options header* — <https://html.spec.whatwg.org/multipage/speculative-loading.html#the-x-frame-options-header>(处理模型逐步算法、"confused"用法的官方结果表、`ALLOWALL` 的唯一影响)
- MDN `X-Frame-Options` — <https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Frame-Options>(取值语义、`ALLOW-FROM` 被忽略、meta 无效)
