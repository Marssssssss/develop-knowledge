# XSS 与 CSP(Content Security Policy)

## 简介

跨站脚本(XSS)是一种**注入攻击**:攻击者把可执行脚本(JavaScript/HTML)注入到受害用户的浏览器上下文中,从而窃取会话 cookie、劫持账户、伪造 UI 或重定向到钓鱼站。OWASP Top 10 中 XSS 长期占据 A03 注入类核心位置(2021)、合并后仍属 A03。Content Security Policy(CSP)是浏览器侧纵深防御层,即便应用层有未过滤的输入也能阻断内联脚本执行。

## 关键概念清单

| 术语 | 一句话 |
| --- | --- |
| **Reflected XSS** | 用户输入被服务端**反射回**响应 HTML(如搜索结果页),恶意脚本随 URL 传播 |
| **Stored XSS** | 攻击载荷先**持久化**(DB/评论/日志),其他用户访问时自动执行 |
| **DOM-based XSS** | 漏洞在**客户端 JS** 中(读 `location.hash` 后写入 `innerHTML`),服务端响应不变 |
| **Server XSS / Client XSS** | OWASP 2012+ 重组术语:服务端 vs 客户端(后者含 DOM XSS + AJAX 回填) |
| **Content Security Policy** | 响应头 `Content-Security-Policy` 声明脚本/样式等可信来源,违规即阻断 |
| **Output Encoding** | 按上下文转义用户输入:HTML body / HTML attr / JS / URL / CSS 五种规则不同 |
| **Safe Sink** | 自动转义的 DOM API,如 `textContent` / `setAttribute` / `encodeURIComponent` |

## 历史背景

- 1999 年早期 IE/Mozilla 已能嵌入 `<script>` 跨页执行 — Microsoft 报告过 IE 安全公告 MS98-003
- 2005 年 Amit Klein 在 WASC 文章 *DOM Based XSS or XSS of the Third Kind* 中首次定义 DOM XSS
- OWASP 2012 年改用 **Server XSS / Client XSS** 二维分类(Script Source Code / Storage 维度)
- 2012 年 Mozilla Firefox 16 起支持 `Content-Security-Policy` 响应头
- 2017 年 W3C `Web Application Security Working Group` 将 CSP 标准化为 W3C Candidate Recommendation

## 原理详解

### OWASP 2017 XSS 三类型(后归并入 Server/Client 矩阵)

| 类型 | 数据流向 | 服务端响应是否变化 |
| --- | --- | --- |
| Reflected | Request → Server HTML response | 是(注入出现在响应体) |
| Stored | Database → Server HTML response | 是 |
| DOM-based | Request fragment(`location.hash`)→ Client JS → DOM | **否**(HTTP 响应本身是干净的) |

### Output Encoding 五大上下文(OWASP 规则摘要)

| 上下文 | 编码方式 | 必须转义的最小字符集 |
| --- | --- | --- |
| HTML Body | HTML Entity | `&` `<` `>` `"` `'` |
| HTML Attribute | HTML Entity(必须**用引号包裹**) | `&` `<` `>` `"` `'` + 空格 |
| JavaScript | `\uXXXX` Unicode | 所有非字母数字 |
| URL | `%HH` percent-encoding | URL 元字符 |
| CSS | `\XX` 或 `\XXXXXX` hex | 所有非字母数字 |

> **Anti-pattern 警告**:WAF 黑名单正则(<script>、onerror 等关键字过滤)会被 `<\u0073cript>` Unicode 转义、`&#x6A;avascript;` HTML 实体、`scr ipt` 拆词轻易绕过。OWASP 明确**不推荐**黑名单方案。

### CSP 核心指令

```
Content-Security-Policy:
  default-src 'self';              # 默认仅同源资源
  script-src 'self' 'nonce-XYZ';   # 脚本仅同源 + 带 nonce 的内联
  object-src 'none';               # 禁 Flash/Java Applet(防 legacy 攻击)
  base-uri 'self';                 # 限制 <base> 标签
  frame-ancestors 'none';          # 防 clickjacking(替代 X-Frame-Options)
```

**nonce 机制**:服务端为每响应生成一次性随机 nonce,如 `<script nonce="a8d3f">console.log(1)</script>`,CSP 响应头声明 `script-src 'nonce-a8d3f'`,**其他内联脚本被阻断**。

**`unsafe-inline` 关键字**:允许内联脚本(回退用,**严禁默认开启**)。`'strict-dynamic'` 允许 nonce 脚本动态加载的下游脚本。

### CSP 违规上报

```
Content-Security-Policy: ...; report-uri /csp-report; report-to csp-endpoint
Content-Security-Policy-Report-Only: ...  # 仅报告不阻断(灰度)
```

违规报告为 `application/csp-report` JSON POST:`{"csp-report":{"document-url":"...","blocked-uri":"...","violated-directive":"script-src 'self'"}}`。

## 演示

### 1. 反射型 XSS 服务端渲染(不安全)

```
GET /search?q=<script>alert(1)</script>
→ 响应: <div>您搜索了:<script>alert(1)</script></div>   ← 脚本直接执行
```

### 2. HTML Entity 编码后(安全)

```
GET /search?q=<script>alert(1)</script>
→ 响应: <div>您搜索了:&lt;script&gt;alert(1)&lt;/script&gt;</div>  ← 浏览器渲染为文本
```

### 3. DOM-based XSS(客户端 sink 不安全)

```javascript
// 不安全:读 location.hash 写入 innerHTML
const query = document.location.hash.slice(1);  // #q=foo
document.body.innerHTML = `<h1>${query}</h1>`;   // ← 用户可控写入 DOM

// 安全:用 textContent(自动 HTML Entity)
const h1 = document.createElement('h1');
h1.textContent = document.location.hash.slice(1);
document.body.appendChild(h1);
```

### 4. CSP 响应头阻止

```
HTTP/1.1 200 OK
Content-Security-Policy: script-src 'self' 'nonce-a8d3f'

<button onclick="alert(1)">click</button>      ← 阻断:onclick 内联事件处理器
<script nonce="a8d3f">console.log('ok')</script>  ← 允许:有合法 nonce
```

## 环境准备

- Python 3.8+(`http.server` 标准库,无第三方依赖)
- Node.js 14+(可选,运行 JS 部分)

## 运行方式

```bash
cd XSS与CSP/python
python3 xss_demo.py

# 输出 5 个 demo,纯 stdout 模拟浏览器/服务端,无需启动 HTTP server
```

```bash
cd XSS与CSP/javascript
node dom_xss.js
```

## 关键代码片段(Python `xss_demo.py`)

```python
# HTML Body 上下文的安全输出编码(OWASP 规则 #1)
def html_body_escape(s: str) -> str:
    return (s.replace('&', '&amp;')
             .replace('<', '&lt;')
             .replace('>', '&gt;')
             .replace('"', '&quot;')
             .replace("'", '&#x27;'))

# HTML Attribute 上下文必须先引号包裹(防止注入 onload="...")
# 服务器拼接前:<div class="user-input">{user_value}</div>
# 不能:<div class=user-input>{user_value}</div>   ← 空格可切断属性
```

## 性能与边界

- **HTML 实体编码**开销:O(n) 字符串扫描;一个 1 KB 响应约 5-10 μs
- **CSP** 验证开销:浏览器解析 ~10-50 个指令约 0.1-0.5 ms
- **textContent vs innerHTML**:前者 ~3x 慢但**绝对安全**(HTML 解析跳过);后者快但需手动过滤

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `<script>alert(1)</script>` 在 textarea 中 | HTML 解析器不解析 textarea 内实体 | **服务端**上下文感知转义或 DOMPurify 后端解析 |
| `href="javascript:alert(1)"` | URL 上下文未过滤 `javascript:` scheme | URL 上下文必须 encode + 白名单 `http(s):` + `mailto:` |
| Vue/React `{userInput}` 被认为"安全" | 框架只默认转义文本节点,**不**转义 `v-html` / `dangerouslySetInnerHTML` | 显式审计所有 `v-html` / `dangerouslySetInnerHTML` 调用 |
| CSP `script-src 'unsafe-inline'` | 旧项目兼容回退 | 改用 nonce + `'strict-dynamic'` |
| CSP report-only 仍上线 | 灰度模式 | 配 `report-uri` + 监控违规后再切 enforcement |

## 参考资料(实际阅读)

- [OWASP Cross Site Scripting Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html) — HTML/Attribute/JS/URL/CSS 五大上下文编码规则表
- [OWASP Types of Cross-Site Scripting](https://owasp.org/www-community/Types_of_Cross-Site_Scripting) — Server/Client 2×2 矩阵分类的官方原文
- [OWASP Top 10 2017 A7 XSS](https://owasp.org/www-project-top-ten/2017/A7_2017-Cross-Site_Scripting_%28XSS%29) — Reflected/Stored/DOM XSS 三类型定义
- [OWASP Top 10 2025 A03 Injection(并入 XSS)](https://owasp.org/Top10/2025/A05_2025-Injection) — 2025 最新注入分类
- [OWASP XSS Filter Evasion Cheat Sheet](https://owasp.org/www-community/xss-filter-evasion-cheatsheet) — 黑名单方案失败案例(为何 OWASP 反对 WAF 黑名单)
- [MDN Content Security Policy (CSP)](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP) — directive 完整列表 + nonce/hash/`strict-dynamic` 语义
- [W3C CSP Level 3](https://www.w3.org/TR/CSP3/) — 标准规范